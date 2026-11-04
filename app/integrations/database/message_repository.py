"""本轮对话的生命周期：接收、领取租约、表达、结束、延后与失效判定。

一条消息的"写入原语"在 ``message_store``，历史查询在 ``message_history``；
这里只负责"这一轮还能不能继续"：

- ``begin_turn``：幂等、感知、历史裁剪、领租约；
- ``append_expression`` / ``settle_turn`` / ``defer_turn``：表达、结束、未察觉时延后；
- ``run_valid`` / ``fail_turn``：最终校验与失败收尾。

所有函数都在调用方的事务内工作；锁顺序与 JSON 约定见 ``person_store`` 与 ``state_commit``。
"""

import hashlib
from uuid import uuid4

from app.core.errors import ChatError
from app.core.perception import perceive
from app.core.serialization import data, dump, load
from app.integrations.database.character_repository import (
    checked_world,
    lock_current_world,
)
from app.integrations.database.message_history import VISIBLE_MESSAGES_SQL
from app.integrations.database.message_store import (
    append_message,
    message_text,
    payload_hash,
    public_message,
    record_observation,
)
from app.integrations.database.mysql import mysql_service
from app.integrations.database.person_store import enqueue, record_experience
from app.integrations.database.task_repository import validate_run
from app.integrations.database.user_space_repository import user_transaction


def _hash(content):
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def find_completed_turn(user_id, client_id, content, asset_ids=None):
    """幂等重放：同一条客户端消息已完成时，直接返回旧结果。"""
    rows = mysql_service.select(
        "SELECT m.* FROM imagent_user_space s JOIN imagent_world w ON w.id=s.active_world_id AND w.user_id=s.user_id "
        "JOIN imagent_message m ON m.world_id=w.id WHERE s.user_id=%s AND w.status='active' AND m.dedupe_key=%s",
        (user_id, "user:" + client_id),
    )
    if not rows:
        return None
    message = rows[0]
    if message["payload_hash"] != payload_hash(content, asset_ids):
        raise ChatError("MESSAGE_ID_CONFLICT", "相同消息 ID 不能发送不同内容", 409)
    replies = mysql_service.select(
        "SELECT * FROM imagent_message WHERE world_id=%s AND origin='agent' AND reply_to_id=%s ORDER BY seq",
        (message["world_id"], message["id"]),
    )
    tasks = mysql_service.select(
        "SELECT status FROM imagent_background_task WHERE user_id=%s AND kind='dialogue' AND dedupe_key=%s",
        (user_id, message["world_id"] + ":" + client_id),
    )
    settled = bool(tasks and tasks[0]["status"] == "succeeded")
    return (
        {
            "user_message": public_message(message, user_id),
            "reply": public_message(replies[0], user_id) if replies else None,
            "replies": [public_message(reply, user_id) for reply in replies],
            "settled": True,
        }
        if settled
        or (replies and tasks and tasks[0]["status"] in ("cancelled", "failed"))
        else None
    )


def begin_turn(user_id, client_id, content, config_id, lease_seconds, asset_ids=None):
    """接收一条用户消息，并产出本轮对话所需的全部上下文与凭据。

    同一事务里完成五件事（顺序不能换）：
    1. 幂等：同 client_id 已有消息时，内容一致就返回旧记录，不一致报 409；
    2. 新输入到达：作废仍在 running 的上一轮对话，旧回复不再发送；
    3. 落库：写 experience / message / 双方 delivery / 发送者观察 / client_event；
    4. 感知：用作息、手机位置、静音与免打扰判断"人物实际获知到什么程度"，
       未读时不把正文交给模型，也不生成 AI 侧观察；
    5. 领租约：把 dialogue 任务置为 running，租约到期后其他执行者才能接手。

    返回的 turn 就是本轮凭证：task_id + attempt_id 供后续每个写入点校验。
    """
    with user_transaction(user_id) as cursor:
        world = lock_current_world(cursor, user_id, create=True)
        cursor.execute(
            "SELECT * FROM imagent_message WHERE world_id=%s AND dedupe_key=%s",
            (world["id"], "user:" + client_id),
        )
        message = cursor.fetchone()
        if message and message["payload_hash"] != payload_hash(content, asset_ids):
            raise ChatError("MESSAGE_ID_CONFLICT", "相同消息 ID 不能发送不同内容", 409)
        if message:
            cursor.execute(
                "SELECT id FROM imagent_background_task WHERE world_id=%s AND kind='dialogue' AND dedupe_key=%s AND status='running' AND lease_until>UTC_TIMESTAMP(6)",
                (world["id"], world["id"] + ":" + client_id),
            )
            if cursor.fetchone():
                raise ChatError("MESSAGE_IN_PROGRESS", "这条消息正在处理中", 409)
            cursor.execute(
                "SELECT * FROM imagent_message WHERE world_id=%s AND dedupe_key=%s",
                (world["id"], "reply:" + message["id"]),
            )
            reply = cursor.fetchone()
            if reply:
                return {
                    "user_message": public_message(message, user_id),
                    "reply": public_message(reply, user_id),
                }
        # 后到的消息接续同一个人物，旧回复失效；用户发出的内容始终保留。
        cursor.execute(
            "UPDATE imagent_background_task SET status='cancelled',error_code='NEW_INPUT',lease_until=NULL WHERE world_id=%s AND kind='dialogue' AND status='running'",
            (world["id"],),
        )
        if not message:
            extra = []
            for asset_id in asset_ids or []:
                cursor.execute(
                    "SELECT id,kind,analysis_json FROM imagent_media_asset WHERE id=%s AND user_id=%s AND world_id=%s AND status='ready'",
                    (asset_id, user_id, world["id"]),
                )
                asset = cursor.fetchone()
                if not asset:
                    raise ChatError(
                        "MEDIA_NOT_FOUND", "媒体不可用或不属于当前人物", 404
                    )
                extra.append(
                    {
                        "type": asset["kind"],
                        "assetId": asset["id"],
                        "perception": load(asset["analysis_json"]),
                    }
                )
            message = append_message(
                cursor,
                world,
                content,
                origin="user",
                dedupe_key="user:" + client_id,
                client_id=client_id,
                extra_parts=extra,
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
                dump(
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
                dump(
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
            "SELECT id,attempts FROM imagent_background_task WHERE user_id=%s AND kind='dialogue' AND dedupe_key=%s",
            (user_id, world["id"] + ":" + client_id),
        )
        claimed = cursor.fetchone()
        task_id = claimed["id"]
        # 取最近 40 条、正文总量 24k 字以内的可见消息；已遗忘记忆关联的不再进入上下文。
        cursor.execute(
            VISIBLE_MESSAGES_SQL
            + " AND m.seq<=%s AND NOT EXISTS (SELECT 1 FROM imagent_observation o JOIN imagent_memory_source ms ON ms.observation_id=o.id JOIN imagent_memory mm ON mm.id=ms.memory_id WHERE o.world_id=m.world_id AND o.experience_id=m.source_experience_id AND o.observer_actor_id=%s AND mm.status='forgotten') ORDER BY m.seq DESC LIMIT 40",
            (
                world["primary_actor_id"],
                user_id,
                message["seq"],
                world["primary_actor_id"],
            ),
        )
        history = []
        remaining = 24000
        for item in cursor.fetchall():
            size = len(message_text(item["parts_json"]))
            if size > remaining:
                break
            remaining -= size
            history.append(item)
        history.reverse()
        cursor.execute(
            "SELECT context_json,routine_json FROM imagent_actor_state WHERE world_id=%s AND actor_id=%s",
            (world["id"], world["primary_actor_id"]),
        )
        actor_state = cursor.fetchone()
        cursor.execute(
            "SELECT contact_policy_json FROM imagent_relationship WHERE world_id=%s AND from_actor_id=%s AND to_actor_id=%s",
            (world["id"], world["primary_actor_id"], world["user_actor_id"]),
        )
        relation = cursor.fetchone()
        perception = perceive(
            data(actor_state["context_json"]),
            data(actor_state["routine_json"]),
            data(relation["contact_policy_json"]) if relation else {},
            message["id"],
        )
        if perception == "notification":
            record_experience(
                cursor,
                world,
                "notification:" + message["id"],
                {
                    "kind": "notification",
                    "text": "注意到聊天对象发来一条消息，尚未读到正文",
                },
                [world["primary_actor_id"]],
                kind="notification",
            )
        if perception != "read":
            history = []
        for item in history:
            if item["origin"] == "user":
                record_observation(cursor, world, item, world["primary_actor_id"])
                cursor.execute(
                    "UPDATE imagent_message_delivery SET delivered_at=COALESCE(delivered_at,UTC_TIMESTAMP(6)),read_at=COALESCE(read_at,UTC_TIMESTAMP(6)) WHERE world_id=%s AND message_id=%s AND viewer_actor_id=%s",
                    (world["id"], item["id"], world["primary_actor_id"]),
                )
        if perception == "read":
            enqueue(cursor, world, "memory", "observed:" + message["id"], {}, 5)
        public_history = [public_message(item, user_id) for item in history]
        for item in public_history:
            media = item["parts"].get("parts", [])[1:]
            if media:
                item["content"] += "\n[媒体感知线索]" + dump(
                    [p.get("perception", {}) for p in media]
                )
        return {
            "world": world,
            "task_id": task_id,
            "attempt_id": attempt_id,
            "attempts": claimed["attempts"],
            "user_message": public_message(message, user_id),
            "history": public_history,
            "reply": None,
            "perception": perception,
        }


def complete_turn(user_id, turn, content):
    """兼容旧链路的完整回复写入：校验世界与租约后写一条 agent 消息。"""
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
        reply = append_message(
            cursor,
            world,
            content,
            origin="agent",
            dedupe_key="reply:" + turn["user_message"]["id"],
            reply_to=turn["user_message"]["id"],
        )
        cursor.execute(
            "UPDATE imagent_background_task SET status='succeeded',result_json=%s,lease_owner=NULL,lease_until=NULL WHERE id=%s",
            (dump({"schemaVersion": 1, "messageId": reply["id"]}), turn["task_id"]),
        )
        return public_message(reply, user_id)


def append_expression(
    user_id, turn, content, key, extra_parts=None, expected_version=None
):
    """提交一条"人物真正说出口"的表达：先入库，再由调用方 emit 给客户端。

    expected_version 是评价阶段看到的世界版本。若此刻已经变了，说明人物在
    组织语言期间经历了新事情，直接拒绝发送，让上层重新评价，而不是发出过时的话。
    """
    with user_transaction(user_id) as cursor:
        world = checked_world(cursor, user_id, turn["world"]["id"])
        validate_run(cursor, turn)
        if expected_version is not None and world["state_version"] != expected_version:
            raise ChatError("RUN_INTERRUPTED", "人物状态已有新变化", 409)
        cursor.execute(
            "SELECT * FROM imagent_message WHERE world_id=%s AND dedupe_key=%s",
            (world["id"], key),
        )
        prior = cursor.fetchone()
        if prior:
            return public_message(prior, user_id)
        row = append_message(
            cursor,
            world,
            content,
            origin="agent",
            dedupe_key=key,
            reply_to=turn["user_message"]["id"],
            extra_parts=extra_parts,
        )
        enqueue(cursor, world, "memory", "message:" + row["id"], {}, 5)
        return public_message(row, user_id)


def settle_turn(user_id, turn, reason="completed"):
    """结束本轮对话（回复完成、选择沉默、决定延后），并投递后台记忆整理。

    沉默也是"处理完成"：任务置为 succeeded，而不是 failed——
    人物的选择不是技术故障。
    """
    with user_transaction(user_id) as cursor:
        world = checked_world(cursor, user_id, turn["world"]["id"])
        validate_run(cursor, turn)
        cursor.execute(
            "UPDATE imagent_background_task SET status='succeeded',result_json=%s,lease_until=NULL,lease_owner=NULL WHERE id=%s",
            (dump({"outcome": reason}), turn["task_id"]),
        )
        enqueue(cursor, world, "memory", "turn:" + turn["task_id"], {}, 5)


def defer_turn(user_id, turn):
    """人物还没察觉消息时，登记一个延迟的待回复任务，而不是直接回复。

    延迟 60 秒只是第一次检查的时间点；后台还会再按当时的作息与手机状态判断，
    仍未察觉就继续等，不因为重试次数耗尽而丢消息。
    """
    with user_transaction(user_id) as cursor:
        world = checked_world(cursor, user_id, turn["world"]["id"])
        validate_run(cursor, turn)
        enqueue(
            cursor,
            world,
            "pending_reply",
            "unread:" + turn["user_message"]["id"],
            {
                "clientId": turn["user_message"]["client_message_id"],
                "content": turn["user_message"]["content"],
                "assetIds": [
                    p["assetId"]
                    for p in turn["user_message"]["parts"].get("parts", [])
                    if p.get("assetId")
                ],
            },
            60,
        )


def run_valid(turn):
    """轻量有效性检查：SSE 循环每 0.3 秒用它判断本轮是否已被中断。"""
    rows = mysql_service.select(
        "SELECT id FROM imagent_background_task WHERE id=%s AND ((lease_owner=%s AND status='running' AND lease_until>UTC_TIMESTAMP(6)) OR status='succeeded')",
        (turn["task_id"], turn["attempt_id"]),
    )
    return bool(rows)


def fail_turn(user_id, turn, code):
    """技术失败收尾：只标记任务，不回滚已经发生的消息。"""
    with user_transaction(user_id) as cursor:
        cursor.execute(
            "UPDATE imagent_background_task SET status='failed',error_code=%s,lease_owner=NULL,lease_until=NULL WHERE id=%s AND user_id=%s AND lease_owner=%s AND status='running'",
            (code, turn["task_id"], user_id, turn["attempt_id"]),
        )
