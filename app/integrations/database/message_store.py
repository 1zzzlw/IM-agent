"""消息原语：一条发言如何同时变成经过、消息、双方视图与客户端事件。

只做单条消息的写入与公共投影，不管理"这一轮是否还有效"（那在 message_repository）。
写入顺序与设计文档一致：experience → message → delivery → observation → client_event。
"""

import hashlib
import json
from datetime import datetime, timezone
from uuid import uuid4

from app.core.serialization import dump


def _hash(content):
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def payload_hash(content, asset_ids=None):
    """消息内容摘要：同 ID 不同内容时用于发现冲突。"""
    return _hash(
        content + ("\n[media:" + ",".join(asset_ids) + "]" if asset_ids else "")
    )


def message_text(parts):
    """从 parts_json 中取出纯文字；媒体块不参与正文比较。"""
    parts = json.loads(parts) if isinstance(parts, str) else parts
    if isinstance(parts, dict):
        parts = parts.get("parts", [])
    return "\n".join(
        part["text"]
        for part in parts
        if isinstance(part, dict)
        and part.get("type") == "text"
        and isinstance(part.get("text"), str)
    )


def public_message(row, user_id):
    """把数据库行投影成客户端可见的消息结构。"""
    created = row["created_at"]
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return dict(
        id=row["id"],
        client_message_id=row["client_message_id"],
        conversation_id=row["world_id"],
        user_id=user_id,
        role="user" if row["origin"] == "user" else "assistant",
        content=message_text(row["parts_json"]),
        parts=json.loads(row["parts_json"])
        if isinstance(row["parts_json"], str)
        else row["parts_json"],
        message_type=1,
        created_at=created,
        seq=str(row["seq"]),
    )


def record_observation(cursor, world, message, actor_id):
    """为某个实际获知这条消息的人写观察；同一人同一条经历只写一次。"""
    cursor.execute(
        "INSERT INTO imagent_observation (id,world_id,experience_id,observer_actor_id,source_actor_id,method,perceived_json,obtained_at) "
        "VALUES (%s,%s,%s,%s,%s,'direct',%s,UTC_TIMESTAMP(6)) ON DUPLICATE KEY UPDATE id=id",
        (
            str(uuid4()),
            world["id"],
            message["source_experience_id"],
            actor_id,
            message["sender_actor_id"],
            dump(
                {
                    "schemaVersion": 1,
                    "messageId": message["id"],
                    "text": message_text(message["parts_json"]),
                    "media": json.loads(message["parts_json"]).get("parts", [])[1:]
                    if isinstance(message["parts_json"], str)
                    else message["parts_json"].get("parts", [])[1:],
                }
            ),
        ),
    )


def _insert_experience(
    cursor, world, experience_id, message_id, sender, origin, content, digest
):
    """发言先成为"已发生的经过"，再成为聊天界面里的气泡。"""
    cursor.execute(
        "INSERT INTO imagent_experience (id,world_id,kind,realm,participant_ids_json,actual_json,occurred_at,dedupe_key,payload_hash) "
        "VALUES (%s,%s,'utterance',%s,%s,%s,UTC_TIMESTAMP(6),%s,%s)",
        (
            experience_id,
            world["id"],
            "user_report" if origin == "user" else "shared_virtual",
            dump([sender]),
            dump(
                {
                    "schemaVersion": 1,
                    "speakerId": sender,
                    "messageId": message_id,
                    "text": content,
                    "meaning": "said_not_verified_fact",
                }
            ),
            "message:" + message_id,
            digest,
        ),
    )


def _insert_message(
    cursor,
    world,
    message_id,
    sender,
    origin,
    client_id,
    dedupe_key,
    digest,
    parts,
    reply_to,
    experience_id,
    channel,
    call_id,
):
    """分配世界内消息序号并写 message 行：顺序靠 next_message_seq，不靠主键自增。"""
    world["next_message_seq"] += 1
    cursor.execute(
        "UPDATE imagent_world SET next_message_seq=%s WHERE id=%s",
        (world["next_message_seq"], world["id"]),
    )
    cursor.execute(
        "INSERT INTO imagent_message (id,world_id,seq,sender_actor_id,origin,client_message_id,dedupe_key,payload_hash,parts_json,reply_to_id,source_experience_id,channel,call_id) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (
            message_id,
            world["id"],
            world["next_message_seq"],
            sender,
            origin,
            client_id,
            dedupe_key,
            digest,
            parts,
            reply_to,
            experience_id,
            channel,
            call_id,
        ),
    )


def _insert_deliveries(cursor, world, message_id, sender):
    """双方各一条消息视图；发送方直接视为已送达、已读。"""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for viewer in (world["user_actor_id"], world["primary_actor_id"]):
        cursor.execute(
            "INSERT INTO imagent_message_delivery (world_id,message_id,viewer_actor_id,delivered_at,read_at) VALUES (%s,%s,%s,%s,%s)",
            (
                world["id"],
                message_id,
                viewer,
                now if viewer == sender else None,
                now if viewer == sender else None,
            ),
        )


def _publish_message_event(cursor, world, message_id, message):
    """同事务分配用户事件序号，客户端断线后可按 event_seq 补齐。"""
    cursor.execute(
        "UPDATE imagent_user_space SET next_event_seq=next_event_seq+1 WHERE user_id=%s",
        (world["user_id"],),
    )
    cursor.execute(
        "INSERT INTO imagent_client_event (user_id,event_seq,world_id,event_type,resource_type,resource_id,payload_json,dedupe_key) "
        "SELECT user_id,next_event_seq,%s,'message.created','message',%s,%s,%s FROM imagent_user_space WHERE user_id=%s",
        (
            world["id"],
            message_id,
            dump(
                {
                    "messageId": message_id,
                    "worldId": world["id"],
                    "seq": str(message["seq"]),
                }
            ),
            "message:" + message_id,
            world["user_id"],
        ),
    )


def append_message(
    cursor,
    world,
    content,
    *,
    origin,
    dedupe_key,
    reply_to=None,
    client_id=None,
    extra_parts=None,
    channel="chat",
    call_id=None,
):
    """写入一条真实发言及其派生数据；调用方负责事务与幂等键。"""
    message_id, experience_id = str(uuid4()), str(uuid4())
    sender = world["user_actor_id"] if origin == "user" else world["primary_actor_id"]
    digest, parts = (
        payload_hash(content, [x["assetId"] for x in extra_parts or []]),
        dump(
            {
                "schemaVersion": 1,
                "parts": [{"type": "text", "text": content}, *(extra_parts or [])],
            }
        ),
    )
    _insert_experience(
        cursor, world, experience_id, message_id, sender, origin, content, digest
    )
    _insert_message(
        cursor,
        world,
        message_id,
        sender,
        origin,
        client_id,
        dedupe_key,
        digest,
        parts,
        reply_to,
        experience_id,
        channel,
        call_id,
    )
    _insert_deliveries(cursor, world, message_id, sender)
    cursor.execute("SELECT * FROM imagent_message WHERE id=%s", (message_id,))
    message = cursor.fetchone()
    record_observation(cursor, world, message, sender)
    _publish_message_event(cursor, world, message_id, message)
    return message
