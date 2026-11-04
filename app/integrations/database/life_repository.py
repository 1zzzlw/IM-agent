from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app.core.errors import ChatError
from app.core.serialization import data, dump, envelope, load
from app.integrations.database.character_repository import checked_world
from app.integrations.database.creation_repository import add_actor
from app.integrations.database.message_store import append_message
from app.integrations.database.moment_repository import publish
from app.integrations.database.person_store import enqueue, record_experience
from app.integrations.database.state_commit import commit_appraisal
from app.integrations.database.user_space_repository import user_transaction
from app.schemas.person import Appraisal


def commit_life_event(user_id, task, snap, result):
    """把一次事件编排结果落库：经过、观察、关系、状态影响、主动表达与后续任务。

    开始阶段（event_end 未触发）只写"事情已经发生"与当前进程，不提前写结束后才
    知道的细节；结束阶段补齐经过并把 phase 推进到 impact_applied。
    所有写入都在同一事务内，且先校验任务租约与 world.state_version。
    """
    proposal, experience, impact = (
        result["proposal"],
        result["experience"],
        result["impact"],
    )
    appraisal = Appraisal.model_validate(impact["appraisal"])
    finishing = task["kind"] == "event_end"
    with user_transaction(user_id) as cursor:
        world = checked_world(cursor, user_id, task["world_id"])
        cursor.execute(
            "SELECT id FROM imagent_background_task WHERE id=%s AND status='running' AND lease_owner=%s AND lease_until>UTC_TIMESTAMP(6)",
            (task["id"], task["lease_owner"]),
        )
        if not cursor.fetchone():
            raise ChatError("RUN_EXPIRED", "生活任务已失效", 409)
        cursor.execute(
            "SELECT id FROM imagent_experience WHERE world_id=%s AND dedupe_key=%s",
            (world["id"], "event:" + task["id"]),
        )
        if cursor.fetchone():
            return False
        if world["state_version"] != snap["world"]["state_version"]:
            raise ChatError("STATE_CHANGED", "状态已变化", 409)
        valid_actors = {a["id"] for a in snap["actors"]} | {world["user_actor_id"]}
        participants = list(set(proposal["participant_ids"]) & valid_actors)
        if not participants:
            participants = [world["primary_actor_id"]]
        for speech in experience["dialogue"]:
            if speech["speaker_id"] not in participants:
                raise ValueError("事件对话包含非参与人物")
        event_id = (
            load(task["payload_json"]).get("eventId") if finishing else str(uuid4())
        )
        if not finishing:
            if world["primary_actor_id"] in participants:
                cursor.execute(
                    "SELECT id FROM imagent_life_event WHERE world_id=%s AND status='running' AND JSON_CONTAINS(participant_ids_json,JSON_QUOTE(%s))",
                    (world["id"], world["primary_actor_id"]),
                )
                for old in cursor.fetchall():
                    cursor.execute(
                        "UPDATE imagent_life_event SET status='cancelled',ended_at=UTC_TIMESTAMP(6),next_step_at=NULL,progress_json=JSON_SET(progress_json,'$.interruptedBy',%s) WHERE id=%s",
                        (event_id, old["id"]),
                    )
                    cursor.execute(
                        "UPDATE imagent_background_task SET status='cancelled',lease_until=NULL WHERE world_id=%s AND kind='event_end' AND dedupe_key=%s AND status IN ('queued','running')",
                        (world["id"], old["id"]),
                    )
            cursor.execute(
                "INSERT INTO imagent_life_event (id,world_id,kind,title,initiator_actor_id,participant_ids_json,phase,status,started_at,next_step_at,progress_json) VALUES (%s,%s,'random',%s,%s,%s,'experiencing','running',UTC_TIMESTAMP(6),DATE_ADD(UTC_TIMESTAMP(6),INTERVAL %s MINUTE),%s)",
                (
                    event_id,
                    world["id"],
                    proposal["title"],
                    participants[0],
                    dump(participants),
                    proposal["duration_minutes"],
                    dump({"proposal": proposal, "starting": experience}),
                ),
            )
            enqueue(
                cursor,
                world,
                "event_end",
                event_id,
                {"eventId": event_id, "proposal": proposal},
                proposal["duration_minutes"] * 60,
            )
        else:
            cursor.execute(
                "SELECT status FROM imagent_life_event WHERE world_id=%s AND id=%s",
                (world["id"], event_id),
            )
            previous_event = cursor.fetchone()
            if not previous_event or previous_event["status"] != "running":
                return False
            cursor.execute(
                "UPDATE imagent_life_event SET status='completed',phase='impact_applied',ended_at=UTC_TIMESTAMP(6),next_step_at=NULL,progress_json=JSON_SET(progress_json,'$.ending',CAST(%s AS JSON)) WHERE id=%s AND world_id=%s",
                (dump(experience), event_id, world["id"]),
            )
        primary_knows = (
            world["primary_actor_id"] in participants
            or proposal["perceived_by_primary"]
        )
        observers = set(participants)
        if primary_knows:
            observers.add(world["primary_actor_id"])
        eid = record_experience(
            cursor,
            world,
            "event:" + task["id"],
            experience,
            participants,
            event_id=event_id,
            observers=list(observers),
            realm="shared_virtual"
            if world["user_actor_id"] in participants
            else "virtual_world",
        )
        if experience["new_companion"]:
            npc = experience["new_companion"]
            npc_id = add_actor(
                cursor,
                world["id"],
                npc["name"],
                {"description": npc["description"]},
                zone=snap["world"]["residence_timezone"] or "Asia/Shanghai",
                age=npc.get("age"),
                birthday=npc.get("birthday"),
            )
            acquaintances = set(participants)
            if primary_knows:
                acquaintances.add(world["primary_actor_id"])
            for acquaintance in acquaintances:
                cursor.execute(
                    "INSERT INTO imagent_relationship (id,world_id,from_actor_id,to_actor_id,relation_label,assessment_json,contact_policy_json) VALUES (%s,%s,%s,%s,%s,'{}','{}')",
                    (
                        str(uuid4()),
                        world["id"],
                        acquaintance,
                        npc_id,
                        npc["relation"],
                    ),
                )
                cursor.execute(
                    "INSERT INTO imagent_relationship (id,world_id,from_actor_id,to_actor_id,relation_label,assessment_json,contact_policy_json) VALUES (%s,%s,%s,%s,%s,'{}','{}')",
                    (str(uuid4()), world["id"], npc_id, acquaintance, npc["relation"]),
                )
        for relation in experience["relation_changes"]:
            source, target, label = (
                relation.get("from_actor_id"),
                relation.get("to_actor_id"),
                relation.get("label", "")[:64],
            )
            if (
                source in participants
                and target in participants
                and source != target
                and label
            ):
                cursor.execute(
                    "INSERT INTO imagent_relationship (id,world_id,from_actor_id,to_actor_id,relation_label,assessment_json,contact_policy_json) VALUES (%s,%s,%s,%s,%s,'{}','{}') ON DUPLICATE KEY UPDATE relation_label=VALUES(relation_label),version=version+1",
                    (str(uuid4()), world["id"], source, target, label),
                )
        interrupted = False
        if primary_knows:
            patch = {}
            if world["primary_actor_id"] in participants:
                until = (
                    datetime.now(timezone.utc)
                    + timedelta(minutes=proposal["duration_minutes"])
                ).isoformat()
                patch = {
                    "activity": proposal["activity"] or proposal["title"],
                    "location": proposal["location"],
                    "phone_accessible": proposal["phone_accessible"],
                    "phone_silent": proposal["phone_silent"],
                    "privacy_available": proposal["privacy_available"],
                    "busy_until": None if finishing else until,
                }
                if finishing:
                    patch.update(
                        activity="休息", phone_accessible=True, privacy_available=True
                    )
                cursor.execute(
                    "UPDATE imagent_actor_state SET routine_json=%s WHERE world_id=%s AND actor_id=%s",
                    (
                        envelope({"sleeping": proposal["sleeping"] and not finishing}),
                        world["id"],
                        world["primary_actor_id"],
                    ),
                )
            commit_appraisal(
                user_id,
                snap,
                appraisal,
                "life:" + task["id"],
                cursor=cursor,
                context_patch=patch,
                experience_id=eid,
            )
            if (
                finishing
                and world["primary_actor_id"] in participants
                and experience.get("life_changes")
            ):
                profile = data(world["private_profile_json"])
                profile["current_life"] = {
                    **profile.get("current_life", {}),
                    **experience["life_changes"],
                }
                cursor.execute(
                    "UPDATE imagent_actor SET private_profile_json=%s WHERE world_id=%s AND id=%s",
                    (envelope(profile), world["id"], world["primary_actor_id"]),
                )
            interrupted = appraisal.decision == "leave" or (
                bool(patch) and (not patch["phone_accessible"] or proposal["sleeping"])
            )
            if interrupted:
                cursor.execute(
                    "UPDATE imagent_background_task SET status='cancelled',error_code='LIFE_INTERRUPTION',lease_until=NULL WHERE world_id=%s AND kind='dialogue' AND status='running'",
                    (world["id"],),
                )
                cursor.execute(
                    "UPDATE imagent_call_session SET status='ended',ended_at=UTC_TIMESTAMP(6),end_reason='life_interruption',generation_epoch=generation_epoch+1 WHERE world_id=%s AND status='connected'",
                    (world["id"],),
                )
                cursor.execute(
                    "UPDATE imagent_world SET active_call_id=NULL WHERE id=%s",
                    (world["id"],),
                )
            if impact["proactive_message"].strip():
                append_message(
                    cursor,
                    world,
                    impact["proactive_message"],
                    origin="agent",
                    dedupe_key="proactive:" + task["id"],
                )
            if impact["moment_text"].strip():
                moment_id = publish(
                    cursor,
                    world,
                    impact["moment_text"],
                    impact["audience_ids"],
                    "life:" + task["id"],
                    eid,
                    impact["comments"],
                    result.get("image_asset"),
                )
                if impact["image_prompt"]:
                    enqueue(
                        cursor,
                        world,
                        "moment_image",
                        moment_id,
                        {"momentId": moment_id, "prompt": impact["image_prompt"]},
                    )
            enqueue(cursor, world, "memory", "life:" + task["id"], {}, 3)
    return interrupted
