from datetime import datetime, timezone
from uuid import uuid4

from app.core.errors import ChatError
from app.core.serialization import data, envelope, load
from app.integrations.database.character_repository import checked_world
from app.integrations.database.mysql import mysql_service
from app.integrations.database.person_store import enqueue
from app.integrations.database.user_space_repository import user_transaction


def validate_run(cursor, turn):
    """本轮对话/通话的租约必须仍有效；这是迟到结果写库前的最后一道校验。"""
    cursor.execute(
        "SELECT id FROM imagent_background_task WHERE id=%s AND lease_owner=%s AND status='running' AND lease_until>UTC_TIMESTAMP(6)",
        (turn["task_id"], turn["attempt_id"]),
    )
    if not cursor.fetchone():
        raise ChatError("RUN_INTERRUPTED", "本轮交流已经结束", 409)


def claim():
    connection = mysql_service.connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE imagent_background_task SET status='failed',error_code='ATTEMPTS_EXHAUSTED',lease_owner=NULL,lease_until=NULL WHERE status='running' AND kind<>'dialogue' AND lease_until<UTC_TIMESTAMP(6) AND attempts>=5"
            )
            cursor.execute(
                "SELECT t.* FROM imagent_background_task t WHERE t.kind IN ('memory','life','event_end','pending_reply','interaction','appointment','moment_image') AND ((t.status='queued' AND (t.run_after IS NULL OR t.run_after<=UTC_TIMESTAMP(6))) OR (t.status='running' AND t.lease_until<UTC_TIMESTAMP(6))) AND t.attempts<5 AND NOT EXISTS (SELECT 1 FROM imagent_background_task active WHERE active.world_id=t.world_id AND active.kind=t.kind AND active.id<>t.id AND active.status='running' AND active.lease_until>UTC_TIMESTAMP(6)) ORDER BY t.run_after,t.created_at LIMIT 1 FOR UPDATE SKIP LOCKED"
            )
            row = cursor.fetchone()
            if not row:
                return None
            owner = str(uuid4())
            cursor.execute(
                "UPDATE imagent_background_task SET status='running',attempts=attempts+1,lease_owner=%s,lease_until=DATE_ADD(UTC_TIMESTAMP(6),INTERVAL 180 SECOND) WHERE id=%s",
                (owner, row["id"]),
            )
            row["lease_owner"] = owner
            row["attempts"] += 1
        connection.commit()
        return row
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


def finish(task, error=None, retry=False):
    mysql_service.update(
        "UPDATE imagent_background_task SET status=%s,error_code=%s,lease_owner=NULL,lease_until=NULL,run_after=DATE_ADD(UTC_TIMESTAMP(6),INTERVAL %s SECOND) WHERE id=%s AND lease_owner=%s AND status='running'",
        (
            "queued" if retry else "failed" if error else "succeeded",
            error,
            min(300, 15 * 2 ** task["attempts"]),
            task["id"],
            task["lease_owner"],
        ),
    )


def schedule_lives():
    worlds = mysql_service.select(
        "SELECT w.id,w.user_id,s.settings_json FROM imagent_world w JOIN imagent_user_space s ON s.active_world_id=w.id WHERE w.status='active'"
    )
    for row in worlds:
        with user_transaction(row["user_id"]) as cursor:
            world = checked_world(cursor, row["user_id"], row["id"])
            cursor.execute(
                "SELECT created_at FROM imagent_background_task WHERE world_id=%s AND kind='life' ORDER BY created_at DESC LIMIT 1",
                (world["id"],),
            )
            latest = cursor.fetchone()
            window = max(
                5,
                min(1440, int(load(row["settings_json"]).get("lifeWindowMinutes", 60))),
            )
            previous = latest["created_at"] if latest else world["created_at"]
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            if (now - previous).total_seconds() >= window * 60:
                enqueue(cursor, world, "life", str(uuid4()), {})
            cursor.execute(
                "UPDATE imagent_world SET last_tick_at=UTC_TIMESTAMP(6) WHERE id=%s",
                (world["id"],),
            )
            cursor.execute(
                "UPDATE imagent_call_session SET status='ended',ended_at=UTC_TIMESTAMP(6),end_reason='connection_lost',generation_epoch=generation_epoch+1 WHERE world_id=%s AND status='connected' AND UNIX_TIMESTAMP(UTC_TIMESTAMP(6))-COALESCE(JSON_EXTRACT(transport_json,'$.lastHeartbeat')+0,UNIX_TIMESTAMP(connected_at))>45",
                (world["id"],),
            )
            cursor.execute(
                "UPDATE imagent_world w JOIN imagent_call_session c ON c.id=w.active_call_id SET w.active_call_id=NULL WHERE w.id=%s AND c.status<>'connected'",
                (world["id"],),
            )


def resume_worlds():
    """将计划整体向后平移停机空档，不产生空档内的故事。"""
    rows = mysql_service.select(
        "SELECT user_id,id FROM imagent_world WHERE status='active'"
    )
    for row in rows:
        with user_transaction(row["user_id"]) as cursor:
            world = checked_world(cursor, row["user_id"], row["id"])
            if world["last_tick_at"]:
                gap = max(
                    0,
                    (
                        datetime.now(timezone.utc).replace(tzinfo=None)
                        - world["last_tick_at"]
                    ).total_seconds(),
                )
                cursor.execute(
                    "UPDATE imagent_background_task SET run_after=DATE_ADD(run_after,INTERVAL %s SECOND) WHERE world_id=%s AND kind IN ('life','event_end','appointment') AND status='queued'",
                    (int(gap), world["id"]),
                )
                cursor.execute(
                    "UPDATE imagent_life_event SET next_step_at=DATE_ADD(next_step_at,INTERVAL %s SECOND) WHERE world_id=%s AND status='running'",
                    (int(gap), world["id"]),
                )
                cursor.execute(
                    "SELECT actor_id,context_json FROM imagent_actor_state WHERE world_id=%s",
                    (world["id"],),
                )
                from datetime import timedelta

                for actor in cursor.fetchall():
                    context = data(actor["context_json"])
                    if context.get("busy_until"):
                        context["busy_until"] = (
                            datetime.fromisoformat(context["busy_until"])
                            + timedelta(seconds=gap)
                        ).isoformat()
                        cursor.execute(
                            "UPDATE imagent_actor_state SET context_json=%s WHERE world_id=%s AND actor_id=%s",
                            (envelope(context), world["id"], actor["actor_id"]),
                        )
            cursor.execute(
                "UPDATE imagent_world SET last_tick_at=UTC_TIMESTAMP(6) WHERE id=%s",
                (world["id"],),
            )
            cursor.execute(
                "UPDATE imagent_background_task SET status='cancelled',lease_until=NULL,error_code='SERVICE_RESTARTED' WHERE world_id=%s AND kind='dialogue' AND status='running'",
                (world["id"],),
            )
            cursor.execute(
                "UPDATE imagent_call_session SET status='ended',ended_at=UTC_TIMESTAMP(6),generation_epoch=generation_epoch+1,end_reason='service_restarted',transport_json='{}' WHERE world_id=%s AND status='connected'",
                (world["id"],),
            )
            cursor.execute(
                "UPDATE imagent_world SET active_call_id=NULL WHERE id=%s",
                (world["id"],),
            )
