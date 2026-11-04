from uuid import uuid4

from app.core.errors import ChatError
from app.core.serialization import digest, dump, envelope, load
from app.integrations.database.character_repository import checked_world
from app.integrations.database.mysql import mysql_service
from app.integrations.database.person_store import current
from app.integrations.database.user_space_repository import user_transaction


def apply_admin_operation(body):
    with user_transaction(body.user_id) as cursor:
        cursor.execute(
            "SELECT request_hash,result_json FROM imagent_admin_operation WHERE operator_user_id='server-admin' AND request_key=%s",
            (body.request_id,),
        )
        existing = cursor.fetchone()
        fingerprint = digest(body.model_dump())
        if existing:
            if existing["request_hash"] != fingerprint:
                raise ChatError("REQUEST_CONFLICT", "管理员请求 ID 已用于其他操作", 409)
            return load(existing["result_json"])
        actor_world = current(body.user_id, cursor=cursor)
        if not actor_world:
            raise ChatError("CHARACTER_REQUIRED", "当前没有人物", 409)
        world = checked_world(cursor, body.user_id, actor_world["id"])
        result = {"success": True, "worldId": world["id"]}
        change_id = None
        if body.action == "history_clear":
            cursor.execute(
                "UPDATE imagent_message_delivery SET hidden_at=UTC_TIMESTAMP(6) WHERE world_id=%s AND viewer_actor_id=%s",
                (world["id"], world["user_actor_id"]),
            )
        elif body.action == "recreate":
            cursor.execute(
                "UPDATE imagent_world SET status='archived',archived_at=UTC_TIMESTAMP(6),active_call_id=NULL WHERE id=%s",
                (world["id"],),
            )
            cursor.execute(
                "UPDATE imagent_user_space SET active_world_id=NULL WHERE user_id=%s",
                (body.user_id,),
            )
        else:
            cursor.execute(
                "SELECT emotion_json FROM imagent_actor_state WHERE world_id=%s AND actor_id=%s",
                (world["id"], world["primary_actor_id"]),
            )
            from app.core.serialization import data

            emotion = data(cursor.fetchone()["emotion_json"])
            emotion["valence"] = float(emotion.get("valence", 0)) * (1 - body.relief)
            emotion["reason"] = "管理员对过往影响进行缓解，事实仍保留"
            if body.relief == 1:
                emotion["name"] = "平静"
            version = world["state_version"] + 1
            cursor.execute(
                "UPDATE imagent_actor_state SET emotion_json=%s,version=%s WHERE world_id=%s AND actor_id=%s",
                (envelope(emotion), version, world["id"], world["primary_actor_id"]),
            )
            for eid in body.experience_ids:
                cursor.execute(
                    "SELECT id FROM imagent_experience WHERE world_id=%s AND id=%s",
                    (world["id"], eid),
                )
                if not cursor.fetchone():
                    raise ChatError(
                        "EXPERIENCE_NOT_FOUND", "干预经历不属于当前世界", 404
                    )
            if body.experience_ids:
                cursor.execute(
                    "SELECT context_json FROM imagent_actor_state WHERE world_id=%s AND actor_id=%s",
                    (world["id"], world["primary_actor_id"]),
                )
                context = data(cursor.fetchone()["context_json"])
                impacts = context.get("relieved_impacts", {})
                impacts.update({eid: body.relief for eid in body.experience_ids})
                context["relieved_impacts"] = impacts
                cursor.execute(
                    "UPDATE imagent_actor_state SET context_json=%s WHERE world_id=%s AND actor_id=%s",
                    (envelope(context), world["id"], world["primary_actor_id"]),
                )
            for name, value in (("trust", body.trust), ("closeness", body.closeness)):
                if value is not None:
                    cursor.execute(
                        "UPDATE imagent_relationship SET assessment_json=JSON_SET(assessment_json,%s,%s),version=%s WHERE world_id=%s AND from_actor_id=%s AND to_actor_id=%s",
                        (
                            "$.data." + name,
                            value,
                            version,
                            world["id"],
                            world["primary_actor_id"],
                            world["user_actor_id"],
                        ),
                    )
            cursor.execute(
                "UPDATE imagent_world SET state_version=%s WHERE id=%s",
                (version, world["id"]),
            )
            change_id = str(uuid4())
            cursor.execute(
                "INSERT INTO imagent_state_change (id,world_id,origin_type,change_key,request_hash,before_version,after_version,changes_json,appraisal_json) VALUES (%s,%s,'admin',%s,%s,%s,%s,%s,%s)",
                (
                    change_id,
                    world["id"],
                    "admin:" + body.request_id,
                    fingerprint,
                    version - 1,
                    version,
                    dump({"emotion": emotion}),
                    dump({"reason": body.reason}),
                ),
            )
        if body.action != "history_clear":
            kinds = (
                "kind <> 'memory_consumed'"
                if body.action == "recreate"
                else "kind='dialogue'"
            )
            cursor.execute(
                "UPDATE imagent_background_task SET status='cancelled',lease_until=NULL,error_code='ADMIN_OPERATION' WHERE world_id=%s AND status IN ('queued','running') AND "
                + kinds,
                (world["id"],),
            )
            cursor.execute(
                "UPDATE imagent_call_session SET status='ended',ended_at=UTC_TIMESTAMP(6),generation_epoch=generation_epoch+1,end_reason='admin_operation' WHERE world_id=%s AND status='connected'",
                (world["id"],),
            )
            cursor.execute(
                "UPDATE imagent_world SET active_call_id=NULL WHERE id=%s",
                (world["id"],),
            )
        cursor.execute(
            "INSERT INTO imagent_admin_operation (id,operator_user_id,target_user_id,world_id,kind,reason,request_key,request_hash,parameters_json,state_change_id,result_json,status) VALUES (%s,'server-admin',%s,%s,%s,%s,%s,%s,%s,%s,%s,'completed')",
            (
                str(uuid4()),
                body.user_id,
                world["id"],
                body.action,
                body.reason,
                body.request_id,
                fingerprint,
                dump(body.model_dump()),
                change_id,
                dump(result),
            ),
        )
    return result


def save_public_model(body, reference):
    metadata = body.model_dump()
    fingerprint = digest(
        {**metadata, "credentialDigest": digest(body.api_key) if body.api_key else None}
    )
    connection = mysql_service.connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT request_hash,result_json FROM imagent_admin_operation WHERE operator_user_id='server-admin' AND request_key=%s FOR UPDATE",
                (body.request_id,),
            )
            previous = cursor.fetchone()
            if previous:
                if previous["request_hash"] != fingerprint:
                    raise ChatError("REQUEST_CONFLICT", "请求 ID 已被其他操作使用", 409)
                return load(previous["result_json"])
            identity = str(uuid4())
            cursor.execute(
                "INSERT INTO imagent_model_config (id,name,provider,model_name,base_url,credential_ref,parameters_json,capabilities_json) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    identity,
                    body.config_name,
                    body.provider_name,
                    body.model_name,
                    body.base_url,
                    reference,
                    envelope({"temperature": body.model_temperature}),
                    dump({"source": "admin_configured", "slot": body.slot}),
                ),
            )
            cursor.execute(
                "INSERT INTO imagent_model_binding (scope_key,slot,config_id) VALUES ('system',%s,%s) ON DUPLICATE KEY UPDATE config_id=VALUES(config_id),version=version+1",
                (body.slot, identity),
            )
            result = {"configId": identity, "slot": body.slot}
            cursor.execute(
                "INSERT INTO imagent_admin_operation (id,operator_user_id,kind,reason,request_key,request_hash,parameters_json,result_json,status) VALUES (%s,'server-admin','model_default',%s,%s,%s,%s,%s,'completed')",
                (
                    str(uuid4()),
                    body.reason,
                    body.request_id,
                    fingerprint,
                    dump(metadata),
                    dump(result),
                ),
            )
        connection.commit()
        return result
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()
