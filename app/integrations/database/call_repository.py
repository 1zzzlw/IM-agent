from uuid import uuid4

from app.core.errors import ChatError
from app.core.serialization import dump, load
from app.integrations.database.character_repository import checked_world
from app.integrations.database.message_store import append_message
from app.integrations.database.mysql import mysql_service
from app.integrations.database.person_store import enqueue
from app.integrations.database.state_commit import commit_appraisal
from app.integrations.database.user_space_repository import user_transaction


def get_call(user_id, identity):
    rows = mysql_service.select(
        "SELECT c.* FROM imagent_call_session c JOIN imagent_user_space s ON s.active_world_id=c.world_id WHERE s.user_id=%s AND c.id=%s",
        (user_id, identity),
    )
    if not rows:
        raise ChatError("CALL_NOT_FOUND", "通话不存在", 404)
    return rows[0]


def start_session(body, snap, proposal, accepted):
    identity = str(uuid4())
    with user_transaction(body.user_id) as cursor:
        world = checked_world(cursor, body.user_id, snap["world"]["id"])
        if world["active_call_id"]:
            raise ChatError("CALL_BUSY", "已经在通话中", 409)
        if proposal:
            commit_appraisal(
                body.user_id, snap, proposal, "call:" + identity, cursor=cursor
            )
        cursor.execute(
            "INSERT INTO imagent_call_session (id,world_id,caller_actor_id,callee_actor_id,client_call_id,status,transport_json,connected_at,ended_at,end_reason) VALUES (%s,%s,%s,%s,%s,%s,'{}',IF(%s,UTC_TIMESTAMP(6),NULL),IF(%s,NULL,UTC_TIMESTAMP(6)),%s)",
            (
                identity,
                world["id"],
                world["user_actor_id"],
                world["primary_actor_id"],
                body.client_call_id,
                "connected" if accepted else "rejected",
                accepted,
                accepted,
                None if accepted else "not_answered",
            ),
        )
        if accepted:
            cursor.execute(
                "UPDATE imagent_world SET active_call_id=%s WHERE id=%s",
                (identity, world["id"]),
            )
            cursor.execute(
                "UPDATE imagent_background_task SET status='cancelled',error_code='CALL_CONNECTED',lease_until=NULL WHERE world_id=%s AND kind='dialogue' AND status='running'",
                (world["id"],),
            )
    return {
        "id": identity,
        "status": "connected" if accepted else "rejected",
        "epoch": 0,
    }


def call_action(identity, body, *, end=False):
    row = get_call(body.user_id, identity)
    with user_transaction(body.user_id) as cursor:
        world = checked_world(cursor, body.user_id, row["world_id"])
        cursor.execute(
            "SELECT * FROM imagent_call_session WHERE id=%s FOR UPDATE", (identity,)
        )
        row = cursor.fetchone()
        if end:
            cursor.execute(
                "UPDATE imagent_call_session SET status='ended',ended_at=COALESCE(ended_at,UTC_TIMESTAMP(6)),generation_epoch=generation_epoch+1,transport_json='{}',end_reason='user_ended' WHERE id=%s AND status='connected'",
                (identity,),
            )
            cursor.execute(
                "UPDATE imagent_world SET active_call_id=NULL WHERE id=%s AND active_call_id=%s",
                (world["id"], identity),
            )
        elif row["status"] == "connected":
            cursor.execute(
                "UPDATE imagent_call_session SET generation_epoch=generation_epoch+1,transport_json=JSON_SET(transport_json,'$.segments',JSON_ARRAY()) WHERE id=%s",
                (identity,),
            )
        return {
            "epoch": row["generation_epoch"] + 1,
            "status": "ended" if end else row["status"],
        }


def require_epoch(user_id, identity, epoch):
    row = get_call(user_id, identity)
    if row["status"] != "connected" or row["generation_epoch"] != epoch:
        raise ChatError("CALL_INTERRUPTED", "本段语音已被中断", 409)
    return row


def claim_voice_turn(user_id, identity, epoch):
    call = get_call(user_id, identity)
    with user_transaction(user_id) as cursor:
        checked_world(cursor, user_id, call["world_id"])
        cursor.execute(
            "SELECT * FROM imagent_call_session WHERE id=%s FOR UPDATE", (identity,)
        )
        call = cursor.fetchone()
        if call["status"] != "connected" or call["generation_epoch"] != epoch:
            raise ChatError("CALL_INTERRUPTED", "旧语音轮次已失效", 409)
        cursor.execute(
            "UPDATE imagent_call_session SET generation_epoch=generation_epoch+1,transport_json=JSON_SET(transport_json,'$.segments',JSON_ARRAY(),'$.lastHeartbeat',UNIX_TIMESTAMP(UTC_TIMESTAMP(6))) WHERE id=%s",
            (identity,),
        )
        call["generation_epoch"] += 1
    return call


def record_caller_utterance(user_id, call, epoch, text, asset):
    with user_transaction(user_id) as cursor:
        world = checked_world(cursor, user_id, call["world_id"])
        require_epoch(user_id, call["id"], epoch)
        append_message(
            cursor,
            world,
            text,
            origin="user",
            dedupe_key="call-input:" + asset["id"],
            channel="call",
            call_id=call["id"],
            extra_parts=[
                {
                    "type": "audio",
                    "assetId": asset["id"],
                    "perception": asset["analysis"],
                }
            ],
        )
        # 来电语音确实被听到，才记录主人物的观察。
        from app.integrations.database.message_store import record_observation

        cursor.execute(
            "SELECT * FROM imagent_message WHERE world_id=%s AND dedupe_key=%s",
            (world["id"], "call-input:" + asset["id"]),
        )
        record_observation(cursor, world, cursor.fetchone(), world["primary_actor_id"])
        enqueue(cursor, world, "memory", "call-input:" + asset["id"], {}, 5)


def store_audio_output(user_id, identity, epoch, segments, expected_version=None):
    row = get_call(user_id, identity)
    with user_transaction(user_id) as cursor:
        world = checked_world(cursor, user_id, row["world_id"])
        if expected_version is not None and world["state_version"] != expected_version:
            raise ChatError("CALL_INTERRUPTED", "人物状态已有新变化，请继续说", 409)
        cursor.execute(
            "SELECT generation_epoch,status FROM imagent_call_session WHERE id=%s FOR UPDATE",
            (identity,),
        )
        row = cursor.fetchone()
        if row["generation_epoch"] != epoch or row["status"] != "connected":
            raise ChatError("CALL_INTERRUPTED", "语音已被中断", 409)
        cursor.execute(
            "UPDATE imagent_call_session SET transport_json=JSON_SET(transport_json,'$.segments',CAST(%s AS JSON)) WHERE id=%s",
            (dump(segments), identity),
        )


def heard(identity, body):
    row = get_call(body.user_id, identity)
    with user_transaction(body.user_id) as cursor:
        world = checked_world(cursor, body.user_id, row["world_id"])
        cursor.execute(
            "SELECT * FROM imagent_call_session WHERE id=%s FOR UPDATE", (identity,)
        )
        row = cursor.fetchone()
        if row["generation_epoch"] != body.epoch or row["status"] != "connected":
            raise ChatError("CALL_INTERRUPTED", "旧语音输出已失效", 409)
        segments = load(row["transport_json"]).get("segments", [])
        segment = next((s for s in segments if s["id"] == body.segment_id), None)
        if not segment:
            raise ChatError("SEGMENT_NOT_FOUND", "语音段不存在", 404)
        key = "call-heard:" + segment["id"]
        cursor.execute(
            "SELECT id FROM imagent_message WHERE world_id=%s AND dedupe_key=%s",
            (world["id"], key),
        )
        if not cursor.fetchone():
            append_message(
                cursor,
                world,
                segment["text"],
                origin="agent",
                dedupe_key=key,
                channel="call",
                call_id=identity,
                extra_parts=[{"type": "audio", "assetId": segment["assetId"]}],
            )
            enqueue(cursor, world, "memory", key, {}, 5)
    return {"success": True}
