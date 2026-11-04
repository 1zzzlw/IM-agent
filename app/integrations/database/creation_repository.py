from uuid import uuid4

from app.core.errors import ChatError
from app.core.serialization import digest, dump, envelope, load
from app.integrations.database.person_store import current, record_experience
from app.integrations.database.user_space_repository import user_transaction


def save_candidates(body, batch):
    task_id, batch_id = str(uuid4()), str(uuid4())
    previews = []
    with user_transaction(body.user_id) as cursor:
        cursor.execute(
            "SELECT active_world_id FROM imagent_user_space WHERE user_id=%s",
            (body.user_id,),
        )
        active = cursor.fetchone()["active_world_id"]
        existing = current(body.user_id, cursor=cursor) if active else None
        if (
            existing
            and load(existing["initial_snapshot_json"]).get("mode") != "basic_dialogue"
        ):
            raise ChatError("CHARACTER_EXISTS", "人物已创建，请刷新", 409)
        cursor.execute(
            "UPDATE imagent_character_candidate SET status='discarded' WHERE user_id=%s AND status='ready'",
            (body.user_id,),
        )
        cursor.execute(
            "INSERT INTO imagent_background_task (id,user_id,kind,visibility,dedupe_key,request_hash,payload_json,result_json,status) VALUES (%s,%s,'candidate','user',%s,%s,'{}','{}','succeeded')",
            (task_id, body.user_id, batch_id, digest(body.wish)),
        )
        for item in batch.candidates:
            identity = str(uuid4())
            preview = {
                "name": item.name,
                "introduction": item.introduction,
                "appearance": item.appearance,
                "voice": body.voice or item.voice,
            }
            bundle = item.model_dump()
            bundle["voice"] = body.voice or item.voice
            cursor.execute(
                "INSERT INTO imagent_character_candidate (id,user_id,batch_id,generation_task_id,wish_text,meeting_mode,public_preview_json,generated_bundle_json) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    identity,
                    body.user_id,
                    batch_id,
                    task_id,
                    body.wish,
                    body.meeting_mode,
                    dump(preview),
                    dump(bundle),
                ),
            )
            previews.append({"id": identity, **preview})
    return {"batchId": batch_id, "candidates": previews}


def add_actor(
    cursor,
    world_id,
    name,
    profile,
    public=None,
    *,
    kind="virtual",
    zone="Asia/Shanghai",
    age=None,
    birthday=None,
    voice="alloy",
):
    identity = str(uuid4())
    cursor.execute(
        "INSERT INTO imagent_actor (id,world_id,actor_type,display_name,public_profile_json,private_profile_json,voice_profile_json,residence_timezone,age_years,birthday_month_day) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (
            identity,
            world_id,
            kind,
            name,
            envelope(public or {}),
            envelope(profile),
            envelope({"voice": voice}),
            zone,
            age,
            birthday,
        ),
    )
    cursor.execute(
        "INSERT INTO imagent_actor_state (world_id,actor_id,emotion_json,routine_json,display_state_json,context_json) VALUES (%s,%s,%s,%s,%s,%s)",
        (
            world_id,
            identity,
            envelope({"name": "平静", "valence": 0, "arousal": 0.3}),
            envelope({"sleeping": False}),
            dump({"visible": True, "status": "在线"}),
            envelope(
                {
                    "activity": "休息",
                    "phone_accessible": True,
                    "phone_silent": False,
                    "privacy_available": True,
                }
            ),
        ),
    )
    return identity


def select_candidate(body):
    with user_transaction(body.user_id) as cursor:
        cursor.execute(
            "SELECT * FROM imagent_character_candidate WHERE id=%s AND user_id=%s FOR UPDATE",
            (body.candidate_id, body.user_id),
        )
        row = cursor.fetchone()
        if not row:
            raise ChatError("CANDIDATE_NOT_FOUND", "候选不存在", 404)
        cursor.execute(
            "SELECT active_world_id FROM imagent_user_space WHERE user_id=%s",
            (body.user_id,),
        )
        active = cursor.fetchone()["active_world_id"]
        if row["status"] == "selected" and active == row["selected_world_id"]:
            return {"worldId": active}
        existing = current(body.user_id, cursor=cursor) if active else None
        provisional = (
            existing
            and load(existing["initial_snapshot_json"]).get("mode") == "basic_dialogue"
        )
        if active and not provisional:
            raise ChatError("CHARACTER_EXISTS", "每位用户只能有一位人物", 409)
        if row["status"] != "ready":
            raise ChatError("CANDIDATE_EXPIRED", "候选已失效，请重新生成", 409)
        item = load(row["generated_bundle_json"])
        wid = active if provisional else str(uuid4())
        if not provisional:
            cursor.execute(
                "INSERT INTO imagent_world (id,user_id,initial_snapshot_json,last_tick_at) VALUES (%s,%s,%s,UTC_TIMESTAMP(6))",
                (wid, body.user_id, row["generated_bundle_json"]),
            )
        profile = {
            k: item[k]
            for k in (
                "personality",
                "interests",
                "background",
                "knowledge",
                "appearance",
            )
        }
        if provisional:
            primary, user_actor = (
                existing["primary_actor_id"],
                existing["user_actor_id"],
            )
            cursor.execute(
                "UPDATE imagent_actor SET display_name=%s,private_profile_json=%s,public_profile_json=%s,voice_profile_json=%s,residence_timezone=%s,age_years=%s,birthday_month_day=%s WHERE id=%s AND world_id=%s",
                (
                    item["name"],
                    envelope(profile),
                    envelope(load(row["public_preview_json"])),
                    envelope({"voice": item["voice"]}),
                    item["timezone"],
                    item["age"],
                    item["birthday"],
                    primary,
                    wid,
                ),
            )
            cursor.execute(
                "UPDATE imagent_world SET initial_snapshot_json=%s,state_version=state_version+1 WHERE id=%s",
                (row["generated_bundle_json"], wid),
            )
            cursor.execute(
                "UPDATE imagent_background_task SET status='cancelled',lease_until=NULL,error_code='CHARACTER_INITIALIZED' WHERE world_id=%s AND status IN ('queued','running') AND kind IN ('dialogue','life','event_end')",
                (wid,),
            )
        else:
            primary = add_actor(
                cursor,
                wid,
                item["name"],
                profile,
                load(row["public_preview_json"]),
                zone=item["timezone"],
                age=item["age"],
                birthday=item["birthday"],
                voice=item["voice"],
            )
            user_actor = add_actor(cursor, wid, "你", {}, kind="user")
        world = {
            "id": wid,
            "user_id": body.user_id,
            "primary_actor_id": primary,
            "user_actor_id": user_actor,
        }
        for companion in item["companions"]:
            cid = add_actor(
                cursor,
                wid,
                companion["name"],
                {"description": companion["description"]},
                zone=item["timezone"],
                age=companion.get("age"),
                birthday=companion.get("birthday"),
            )
            for source, target in ((primary, cid), (cid, primary)):
                cursor.execute(
                    "INSERT INTO imagent_relationship (id,world_id,from_actor_id,to_actor_id,relation_label,assessment_json,contact_policy_json) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                    (
                        str(uuid4()),
                        wid,
                        source,
                        target,
                        companion["relation"],
                        envelope({"trust": 0.5}),
                        envelope({}),
                    ),
                )
        for index, past in enumerate(item["past"]):
            eid = record_experience(
                cursor,
                world,
                f"seed:{index}",
                {"text": past, "origin": "initial_life"},
                [primary],
                kind="initial",
                observers=[primary],
            )
            cursor.execute(
                "SELECT id FROM imagent_observation WHERE world_id=%s AND experience_id=%s AND observer_actor_id=%s",
                (wid, eid, primary),
            )
            oid = cursor.fetchone()["id"]
            mid = str(uuid4())
            cursor.execute(
                "INSERT INTO imagent_memory (id,world_id,owner_actor_id,kind,epistemic_type,content,subject_actor_ids_json,cognition_json) VALUES (%s,%s,%s,'experience','observed',%s,%s,%s)",
                (
                    mid,
                    wid,
                    primary,
                    past,
                    dump([primary]),
                    envelope({"importance": 0.8, "confidence": 1, "recalls": 0}),
                ),
            )
            cursor.execute(
                "INSERT INTO imagent_memory_source (world_id,owner_actor_id,memory_id,observation_id) VALUES (%s,%s,%s,%s)",
                (wid, primary, mid, oid),
            )
            # 初始模板不能成为遗忘后重建旧记忆的入口。
            cursor.execute(
                "INSERT INTO imagent_background_task (id,user_id,world_id,kind,dedupe_key,request_hash,payload_json,result_json,status) VALUES (%s,%s,%s,'memory_consumed',%s,%s,'{}','{}','succeeded')",
                (str(uuid4()), body.user_id, wid, oid, digest(oid)),
            )
        for title in item["goals"]:
            cursor.execute(
                "INSERT INTO imagent_goal (id,world_id,actor_id,title,description,progress_json) VALUES (%s,%s,%s,%s,%s,%s)",
                (
                    str(uuid4()),
                    wid,
                    primary,
                    title[:200],
                    title,
                    envelope({"progress": 0}),
                ),
            )
        cursor.execute(
            "UPDATE imagent_world SET primary_actor_id=%s,user_actor_id=%s,status='active' WHERE id=%s",
            (primary, user_actor, wid),
        )
        cursor.execute(
            "UPDATE imagent_user_space SET active_world_id=%s WHERE user_id=%s",
            (wid, body.user_id),
        )
        cursor.execute(
            "UPDATE imagent_character_candidate SET status='selected',selected_world_id=%s WHERE id=%s",
            (wid, row["id"]),
        )
        cursor.execute(
            "UPDATE imagent_character_candidate SET status='discarded' WHERE user_id=%s AND status='ready'",
            (body.user_id,),
        )
    return {"worldId": wid}
