import hashlib
import json
from datetime import datetime, timezone
from uuid import uuid4

from app.integrations.database.mysql import mysql_service
from app.integrations.database.user_space_repository import user_transaction
from app.integrations.database.character_repository import lock_current_world
from app.core.errors import ChatError


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _hash(content):
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _text(parts):
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


def _public(row, user_id):
    created = row["created_at"]
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return dict(
        id=row["id"],
        client_message_id=row["client_message_id"],
        conversation_id=row["world_id"],
        user_id=user_id,
        role="user" if row["origin"] == "user" else "assistant",
        content=_text(row["parts_json"]),
        message_type=1,
        created_at=created,
        seq=str(row["seq"]),
    )


_VISIBLE = """
    SELECT m.* FROM imagent_user_space s
    JOIN imagent_world w ON w.id=s.active_world_id AND w.user_id=s.user_id
    JOIN imagent_message m ON m.world_id=w.id
    JOIN imagent_message_delivery d ON d.world_id=m.world_id AND d.message_id=m.id AND d.viewer_actor_id=%s
    WHERE s.user_id=%s AND w.status='active' AND m.channel='chat' AND d.hidden_at IS NULL
      AND ((m.origin='user' AND m.sender_actor_id=w.user_actor_id)
        OR (m.origin='agent' AND m.sender_actor_id=w.primary_actor_id))
"""


def select_ai_messages(
    *, user_id: str, limit: int = 500, before_seq: int | None = None
):
    rows = mysql_service.select(
        "SELECT active_world_id FROM imagent_user_space WHERE user_id=%s", (user_id,)
    )
    if not rows or not rows[0]["active_world_id"]:
        return []
    worlds = mysql_service.select(
        "SELECT user_actor_id FROM imagent_world WHERE id=%s AND user_id=%s",
        (rows[0]["active_world_id"], user_id),
    )
    if not worlds:
        return []
    sql, params = _VISIBLE, [worlds[0]["user_actor_id"], user_id]
    if before_seq is not None:
        sql += " AND m.seq < %s"
        params.append(before_seq)
    rows = mysql_service.select(sql + " ORDER BY m.seq DESC LIMIT %s", (*params, limit))
    return [_public(row, user_id) for row in reversed(rows) if _text(row["parts_json"])]


def find_completed_turn(user_id, client_id, content):
    rows = mysql_service.select(
        "SELECT m.* FROM imagent_user_space s JOIN imagent_world w ON w.id=s.active_world_id AND w.user_id=s.user_id "
        "JOIN imagent_message m ON m.world_id=w.id WHERE s.user_id=%s AND w.status='active' AND m.dedupe_key=%s",
        (user_id, "user:" + client_id),
    )
    if not rows:
        return None
    message = rows[0]
    if message["payload_hash"] != _hash(content):
        raise ChatError("MESSAGE_ID_CONFLICT", "相同消息 ID 不能发送不同内容", 409)
    replies = mysql_service.select(
        "SELECT * FROM imagent_message WHERE world_id=%s AND dedupe_key=%s",
        (message["world_id"], "reply:" + message["id"]),
    )
    return (
        {
            "user_message": _public(message, user_id),
            "reply": _public(replies[0], user_id),
        }
        if replies
        else None
    )


def _observe(cursor, world, message, actor_id):
    cursor.execute(
        "INSERT INTO imagent_observation (id,world_id,experience_id,observer_actor_id,source_actor_id,method,perceived_json,obtained_at) "
        "VALUES (%s,%s,%s,%s,%s,'direct',%s,UTC_TIMESTAMP(6)) ON DUPLICATE KEY UPDATE id=id",
        (
            str(uuid4()),
            world["id"],
            message["source_experience_id"],
            actor_id,
            message["sender_actor_id"],
            _json(
                {
                    "schemaVersion": 1,
                    "messageId": message["id"],
                    "text": _text(message["parts_json"]),
                }
            ),
        ),
    )


def _append(
    cursor, world, content, *, origin, dedupe_key, reply_to=None, client_id=None
):
    message_id, experience_id = str(uuid4()), str(uuid4())
    sender = world["user_actor_id"] if origin == "user" else world["primary_actor_id"]
    digest, parts = (
        _hash(content),
        _json({"schemaVersion": 1, "parts": [{"type": "text", "text": content}]}),
    )
    cursor.execute(
        "INSERT INTO imagent_experience (id,world_id,kind,realm,participant_ids_json,actual_json,occurred_at,dedupe_key,payload_hash) "
        "VALUES (%s,%s,'utterance',%s,%s,%s,UTC_TIMESTAMP(6),%s,%s)",
        (
            experience_id,
            world["id"],
            "user_report" if origin == "user" else "shared_virtual",
            _json([sender]),
            _json(
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
    world["next_message_seq"] += 1
    cursor.execute(
        "UPDATE imagent_world SET next_message_seq=%s WHERE id=%s",
        (world["next_message_seq"], world["id"]),
    )
    cursor.execute(
        "INSERT INTO imagent_message (id,world_id,seq,sender_actor_id,origin,client_message_id,dedupe_key,payload_hash,parts_json,reply_to_id,source_experience_id) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
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
        ),
    )
    for viewer in (world["user_actor_id"], world["primary_actor_id"]):
        cursor.execute(
            "INSERT INTO imagent_message_delivery (world_id,message_id,viewer_actor_id,delivered_at,read_at) VALUES (%s,%s,%s,%s,%s)",
            (
                world["id"],
                message_id,
                viewer,
                datetime.now(timezone.utc).replace(tzinfo=None)
                if viewer == sender
                else None,
                datetime.now(timezone.utc).replace(tzinfo=None)
                if viewer == sender
                else None,
            ),
        )
    cursor.execute("SELECT * FROM imagent_message WHERE id=%s", (message_id,))
    message = cursor.fetchone()
    _observe(cursor, world, message, sender)
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
            _json(
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
    return message


def begin_turn(user_id, client_id, content, config_id, lease_seconds):
    with user_transaction(user_id) as cursor:
        world = lock_current_world(cursor, user_id, create=True)
        cursor.execute(
            "SELECT * FROM imagent_message WHERE world_id=%s AND dedupe_key=%s",
            (world["id"], "user:" + client_id),
        )
        message = cursor.fetchone()
        if message and message["payload_hash"] != _hash(content):
            raise ChatError("MESSAGE_ID_CONFLICT", "相同消息 ID 不能发送不同内容", 409)
        if message:
            cursor.execute(
                "SELECT * FROM imagent_message WHERE world_id=%s AND dedupe_key=%s",
                (world["id"], "reply:" + message["id"]),
            )
            reply = cursor.fetchone()
            if reply:
                return {
                    "user_message": _public(message, user_id),
                    "reply": _public(reply, user_id),
                }
        cursor.execute(
            "SELECT id FROM imagent_background_task WHERE world_id=%s AND kind='dialogue' AND status='running' AND lease_until>UTC_TIMESTAMP(6) LIMIT 1",
            (world["id"],),
        )
        if cursor.fetchone():
            raise ChatError("DIALOGUE_BUSY", "正在回复上一条消息，请稍后再试", 409)
        if not message:
            message = _append(
                cursor,
                world,
                content,
                origin="user",
                dedupe_key="user:" + client_id,
                client_id=client_id,
            )
        cursor.execute(
            "INSERT INTO imagent_background_task (id,user_id,world_id,kind,dedupe_key,request_hash,payload_json,result_json) "
            "VALUES (%s,%s,%s,'dialogue',%s,%s,%s,'{}') ON DUPLICATE KEY UPDATE id=id",
            (
                str(uuid4()),
                user_id,
                world["id"],
                world["id"] + ":" + client_id,
                _hash(content),
                _json(
                    {
                        "schemaVersion": 1,
                        "messageId": message["id"],
                        "configId": config_id,
                    }
                ),
            ),
        )
        attempt_id = str(uuid4())
        cursor.execute(
            "UPDATE imagent_background_task SET status='running',attempts=attempts+1,error_code=NULL,payload_json=%s,lease_owner=%s,lease_until=DATE_ADD(UTC_TIMESTAMP(6), INTERVAL %s SECOND) "
            "WHERE user_id=%s AND kind='dialogue' AND dedupe_key=%s",
            (
                _json(
                    {
                        "schemaVersion": 1,
                        "messageId": message["id"],
                        "configId": config_id,
                    }
                ),
                attempt_id,
                lease_seconds,
                user_id,
                world["id"] + ":" + client_id,
            ),
        )
        cursor.execute(
            "SELECT id FROM imagent_background_task WHERE user_id=%s AND kind='dialogue' AND dedupe_key=%s",
            (user_id, world["id"] + ":" + client_id),
        )
        task_id = cursor.fetchone()["id"]
        cursor.execute(
            _VISIBLE + " AND m.seq<=%s ORDER BY m.seq DESC LIMIT 40",
            (world["primary_actor_id"], user_id, message["seq"]),
        )
        history = []
        remaining = 24000
        for item in cursor.fetchall():
            size = len(_text(item["parts_json"]))
            if size > remaining:
                break
            remaining -= size
            history.append(item)
        history.reverse()
        # 基础版在准备模型上下文时才视为 AI 实际获得了文字，而非写入时强行标记已读。
        for item in history:
            if item["origin"] == "user":
                _observe(cursor, world, item, world["primary_actor_id"])
                cursor.execute(
                    "UPDATE imagent_message_delivery SET delivered_at=COALESCE(delivered_at,UTC_TIMESTAMP(6)),read_at=COALESCE(read_at,UTC_TIMESTAMP(6)) WHERE world_id=%s AND message_id=%s AND viewer_actor_id=%s",
                    (world["id"], item["id"], world["primary_actor_id"]),
                )
        return {
            "world": world,
            "task_id": task_id,
            "attempt_id": attempt_id,
            "user_message": _public(message, user_id),
            "history": [_public(item, user_id) for item in history],
            "reply": None,
        }


def complete_turn(user_id, turn, content):
    with user_transaction(user_id) as cursor:
        world = lock_current_world(cursor, user_id)
        if not world or world["id"] != turn["world"]["id"]:
            raise ChatError("WORLD_CHANGED", "当前角色已变化，请重新加载", 409)
        cursor.execute(
            "SELECT id FROM imagent_background_task WHERE id=%s AND user_id=%s AND lease_owner=%s AND status='running' AND lease_until>UTC_TIMESTAMP(6) FOR UPDATE",
            (turn["task_id"], user_id, turn["attempt_id"]),
        )
        if not cursor.fetchone():
            raise ChatError("RUN_EXPIRED", "本轮对话已失效，请重试", 409)
        reply = _append(
            cursor,
            world,
            content,
            origin="agent",
            dedupe_key="reply:" + turn["user_message"]["id"],
            reply_to=turn["user_message"]["id"],
        )
        cursor.execute(
            "UPDATE imagent_background_task SET status='succeeded',result_json=%s,lease_owner=NULL,lease_until=NULL WHERE id=%s",
            (_json({"schemaVersion": 1, "messageId": reply["id"]}), turn["task_id"]),
        )
        return _public(reply, user_id)


def fail_turn(user_id, turn, code):
    with user_transaction(user_id) as cursor:
        cursor.execute(
            "UPDATE imagent_background_task SET status='failed',error_code=%s,lease_owner=NULL,lease_until=NULL WHERE id=%s AND user_id=%s AND lease_owner=%s AND status='running'",
            (code, turn["task_id"], user_id, turn["attempt_id"]),
        )
