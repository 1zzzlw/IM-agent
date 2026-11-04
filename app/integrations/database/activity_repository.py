"""约定的"到点"处理：用户是否在场、活动是否真的开始、错过时如何记录。

安排约定（写入 life_event + 到期任务）属于状态提交的一部分，见
``state_commit._schedule_activity``；这里只处理任务到期后的实际到达。
"""

from datetime import datetime, timedelta, timezone

from app.core.serialization import load
from app.integrations.database.character_repository import checked_world
from app.integrations.database.message_store import append_message
from app.integrations.database.person_store import enqueue, record_experience
from app.integrations.database.state_commit import commit_appraisal
from app.integrations.database.user_space_repository import user_transaction
from app.schemas.person import LifeProposal


def arrive_shared_activity(user_id, task, snap, event, appraisal, message):
    """约定时间到：结合用户是否在线/正在聊天，决定真正开始还是记为错过。"""
    with user_transaction(user_id) as cursor:
        world = checked_world(cursor, user_id, task["world_id"])
        cursor.execute(
            "SELECT status FROM imagent_life_event WHERE world_id=%s AND id=%s FOR UPDATE",
            (world["id"], event["id"]),
        )
        current_event = cursor.fetchone()
        if not current_event or current_event["status"] != "planned":
            return
        cursor.execute(
            "SELECT last_seen_at,settings_json FROM imagent_user_space WHERE user_id=%s",
            (user_id,),
        )
        space = cursor.fetchone()
        settings = load(space["settings_json"])
        active = (
            space["last_seen_at"]
            and (
                datetime.now(timezone.utc).replace(tzinfo=None) - space["last_seen_at"]
            ).total_seconds()
            < 25
            and settings.get("chatActive", False)
        )
        minutes = load(event["progress_json"])["duration_minutes"]
        patch = {}
        if active and appraisal.decision == "reply":
            patch = {
                "activity": event["title"],
                "busy_until": (
                    datetime.now(timezone.utc) + timedelta(minutes=minutes)
                ).isoformat(),
            }
        commit_appraisal(
            user_id,
            snap,
            appraisal,
            "appointment:" + event["id"],
            cursor=cursor,
            context_patch=patch,
        )
        if message.strip():
            append_message(
                cursor,
                world,
                message,
                origin="agent",
                dedupe_key="appointment-invite:" + event["id"],
            )
        if appraisal.decision != "reply" or not active:
            cursor.execute(
                "UPDATE imagent_life_event SET status='missed',next_step_at=NULL,progress_json=JSON_SET(progress_json,'$.outcome','未实际共同参与') WHERE id=%s",
                (event["id"],),
            )
            return
        # 同一主 AI 同时只能有一个进行中的活动：开始新的一起活动，取消旧的。
        cursor.execute(
            "SELECT id FROM imagent_life_event WHERE world_id=%s AND id<>%s AND status='running' AND JSON_CONTAINS(participant_ids_json,JSON_QUOTE(%s))",
            (world["id"], event["id"], world["primary_actor_id"]),
        )
        for previous in cursor.fetchall():
            cursor.execute(
                "UPDATE imagent_life_event SET status='cancelled',ended_at=UTC_TIMESTAMP(6),next_step_at=NULL WHERE id=%s",
                (previous["id"],),
            )
            cursor.execute(
                "UPDATE imagent_background_task SET status='cancelled',lease_until=NULL WHERE world_id=%s AND kind='event_end' AND dedupe_key=%s AND status IN ('queued','running')",
                (world["id"], previous["id"]),
            )
        cursor.execute(
            "UPDATE imagent_life_event SET status='running',phase='experiencing',started_at=UTC_TIMESTAMP(6),next_step_at=DATE_ADD(UTC_TIMESTAMP(6),INTERVAL %s MINUTE) WHERE id=%s",
            (minutes, event["id"]),
        )
        proposal = LifeProposal(
            action="event",
            title=event["title"],
            activity=event["title"],
            duration_minutes=minutes,
            participant_ids=[world["primary_actor_id"], world["user_actor_id"]],
        )
        enqueue(
            cursor,
            world,
            "event_end",
            event["id"],
            {"eventId": event["id"], "proposal": proposal.model_dump()},
            minutes * 60,
        )
        record_experience(
            cursor,
            world,
            "activity-start:" + event["id"],
            {"text": "开始共同活动：" + event["title"]},
            [world["primary_actor_id"], world["user_actor_id"]],
            event_id=event["id"],
            realm="shared_virtual",
        )
        enqueue(cursor, world, "memory", "activity:" + event["id"], {}, 3)
