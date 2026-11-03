import json
from uuid import uuid4

from app.core.errors import ChatError


def lock_current_world(cursor, user_id: str, *, create: bool = False):
    cursor.execute(
        "SELECT active_world_id FROM imagent_user_space WHERE user_id=%s", (user_id,)
    )
    space = cursor.fetchone()
    world_id = space["active_world_id"]
    if not world_id:
        if not create:
            return None
        world_id, actor_id, user_actor_id = (str(uuid4()) for _ in range(3))
        snapshot = {"schemaVersion": 1, "mode": "basic_dialogue", "displayName": "微光"}
        cursor.execute(
            "INSERT INTO imagent_world (id,user_id,initial_snapshot_json) VALUES (%s,%s,%s)",
            (world_id, user_id, json.dumps(snapshot, ensure_ascii=False)),
        )
        for identity, kind, name in (
            (actor_id, "virtual", "微光"),
            (user_actor_id, "user", "你"),
        ):
            profile = {
                "schemaVersion": 1,
                "data": {"description": "温和、坦诚，愿意倾听的 AI 对话伙伴"}
                if kind == "virtual"
                else {},
            }
            cursor.execute(
                "INSERT INTO imagent_actor (id,world_id,actor_type,display_name,public_profile_json,private_profile_json,voice_profile_json,residence_timezone) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,'Asia/Shanghai')",
                (
                    identity,
                    world_id,
                    kind,
                    name,
                    json.dumps({"schemaVersion": 1, "data": {}}),
                    json.dumps(profile, ensure_ascii=False),
                    json.dumps({"schemaVersion": 1, "data": {}}),
                ),
            )
            cursor.execute(
                "INSERT INTO imagent_actor_state (world_id,actor_id,emotion_json,routine_json,display_state_json,context_json) VALUES (%s,%s,%s,%s,%s,%s)",
                (
                    world_id,
                    identity,
                    '{"schemaVersion":1,"data":{}}',
                    '{"schemaVersion":1,"data":{}}',
                    '{"schemaVersion":1,"visible":false,"status":"unknown","text":""}',
                    '{"schemaVersion":1,"data":{}}',
                ),
            )
        cursor.execute(
            "UPDATE imagent_world SET primary_actor_id=%s,user_actor_id=%s,status='active' WHERE id=%s",
            (actor_id, user_actor_id, world_id),
        )
        cursor.execute(
            "UPDATE imagent_user_space SET active_world_id=%s WHERE user_id=%s",
            (world_id, user_id),
        )
    cursor.execute(
        "SELECT * FROM imagent_world WHERE id=%s AND user_id=%s FOR UPDATE",
        (world_id, user_id),
    )
    world = cursor.fetchone()
    if not world or world["status"] != "active":
        raise ChatError("WORLD_NOT_ACTIVE", "当前角色暂不可用，请联系管理员", 409)
    cursor.execute(
        "SELECT display_name,private_profile_json FROM imagent_actor WHERE world_id=%s AND id=%s AND status='present'",
        (world_id, world["primary_actor_id"]),
    )
    actor = cursor.fetchone()
    if not actor:
        raise ChatError("CHARACTER_UNAVAILABLE", "当前角色暂不可用", 409)
    return {**world, **actor}
