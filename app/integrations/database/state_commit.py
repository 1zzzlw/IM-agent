"""角色服务：``commit_appraisal`` 是人物状态的唯一写入入口。

一次提交的顺序固定为：锁用户空间→锁世界 → 校验租约（对话/通话）
→ 幂等键查重 → 用 baseVersion 做 CAS → 写 actor_state / relationship / goal
→ state_version + 1 → 写 state_change 流水 → 可选的约定排期。

模型只提交"建议"，这里负责校验、幂等和落库；失败时抛 ChatError，
由上层重读快照后重新评价，绝不覆盖别人的新状态。
"""

from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app.core.errors import ChatError
from app.core.serialization import data, digest, dump, envelope
from app.integrations.database.character_repository import checked_world
from app.integrations.database.person_store import enqueue, record_experience
from app.integrations.database.task_repository import validate_run
from app.integrations.database.user_space_repository import user_transaction


def _write_actor_state(cursor, world, proposal, appraisal, context, display, version):
    """保存情绪、运行上下文与对外状态；真实作息不在这里改。"""
    cursor.execute(
        "UPDATE imagent_actor_state SET emotion_json=%s,context_json=%s,display_state_json=%s,version=%s WHERE world_id=%s AND actor_id=%s",
        (
            envelope(proposal["emotion"]),
            envelope(context),
            dump(display),
            version,
            world["id"],
            world["primary_actor_id"],
        ),
    )


def _write_relationship(cursor, snap, world, appraisal, version):
    """主 AI 对用户的关系增量：单轮变化有上限，避免一句话就跳变。"""
    rel = next(
        (x for x in snap["relations"] if x["to_actor_id"] == world["user_actor_id"]),
        {},
    )
    assessment = data(rel.get("assessment_json"))
    for field, delta in (
        ("trust", appraisal.trust_delta),
        ("closeness", appraisal.closeness_delta),
    ):
        assessment[field] = max(0, min(1, float(assessment.get(field, 0.1)) + delta))
    assessment["reason"] = appraisal.relationship_reason
    cursor.execute(
        "INSERT INTO imagent_relationship (id,world_id,from_actor_id,to_actor_id,relation_label,assessment_json,contact_policy_json,last_interaction_at,version) VALUES (%s,%s,%s,%s,%s,%s,%s,UTC_TIMESTAMP(6),%s) ON DUPLICATE KEY UPDATE relation_label=VALUES(relation_label),assessment_json=VALUES(assessment_json),contact_policy_json=VALUES(contact_policy_json),last_interaction_at=VALUES(last_interaction_at),version=VALUES(version)",
        (
            str(uuid4()),
            world["id"],
            world["primary_actor_id"],
            world["user_actor_id"],
            appraisal.relationship_label,
            envelope(assessment),
            envelope({"do_not_disturb": appraisal.do_not_disturb}),
            version,
        ),
    )


def _write_goals(cursor, world, appraisal, version):
    """推进已有目标，并按标题去重新增目标（聊天不能冒充已完成活动）。"""
    for change in appraisal.goal_changes:
        cursor.execute(
            "UPDATE imagent_goal SET status=%s,progress_json=%s,version=%s WHERE world_id=%s AND actor_id=%s AND id=%s",
            (
                change.status,
                envelope({"progress": change.progress, "reason": change.reason}),
                version,
                world["id"],
                world["primary_actor_id"],
                change.goal_id,
            ),
        )
    for title in appraisal.new_goals:
        if title.strip():
            cursor.execute(
                "INSERT INTO imagent_goal (id,world_id,actor_id,title,description,progress_json,version) SELECT %s,%s,%s,%s,%s,%s,%s WHERE NOT EXISTS (SELECT 1 FROM imagent_goal WHERE world_id=%s AND actor_id=%s AND title=%s AND status='active')",
                (
                    str(uuid4()),
                    world["id"],
                    world["primary_actor_id"],
                    title[:200],
                    title,
                    envelope({"progress": 0, "reason": appraisal.reason}),
                    version,
                    world["id"],
                    world["primary_actor_id"],
                    title[:200],
                ),
            )


def _write_state_change(
    cursor, world, experience_id, key, proposal, context, display, version
):
    """写变化流水：回答"为什么变了、依据哪条经历、改了哪一版"。"""
    cursor.execute(
        "INSERT INTO imagent_state_change (id,world_id,source_experience_id,origin_type,change_key,request_hash,before_version,after_version,changes_json,appraisal_json) VALUES (%s,%s,%s,'agent',%s,%s,%s,%s,%s,%s)",
        (
            str(uuid4()),
            world["id"],
            experience_id,
            key,
            digest(proposal),
            version - 1,
            version,
            dump({"emotion": proposal["emotion"], "context": context, "display": display}),
            dump(proposal),
        ),
    )


def _schedule_activity(cursor, world, plan, key):
    """把双方已同意的约定写成 life_event + 到期任务；未同意的不算共同经历。

    这里是"提交评价"的一部分，所以放在同一事务里；真正到点后的推进
    在 ``activity_repository.arrive_shared_activity``。
    """
    if not plan.mutually_agreed:
        return
    now = datetime.now(timezone.utc)
    start = max(now, plan.starts_at.astimezone(timezone.utc))
    if start > now + timedelta(days=366):
        return
    event_id = str(uuid4())
    end = start + timedelta(minutes=plan.duration_minutes)
    participants = [world["primary_actor_id"], world["user_actor_id"]]
    cursor.execute(
        "INSERT INTO imagent_life_event (id,world_id,kind,title,initiator_actor_id,participant_ids_json,scheduled_start_at,scheduled_end_at,next_step_at,progress_json) VALUES (%s,%s,'shared_activity',%s,%s,%s,%s,%s,%s,%s)",
        (
            event_id,
            world["id"],
            plan.title,
            world["primary_actor_id"],
            dump(participants),
            start.replace(tzinfo=None),
            end.replace(tzinfo=None),
            start.replace(tzinfo=None),
            dump({"agreed": True, "duration_minutes": plan.duration_minutes}),
        ),
    )
    enqueue(
        cursor,
        world,
        "appointment",
        event_id,
        {"eventId": event_id},
        max(0, int((start - now).total_seconds())),
    )
    record_experience(
        cursor,
        world,
        "agreement:" + key,
        {
            "text": f"双方约定：{plan.title}",
            "startsAt": start.isoformat(),
            "durationMinutes": plan.duration_minutes,
        },
        participants,
        event_id=event_id,
        realm="shared_virtual",
    )


def commit_appraisal(
    user_id,
    snap,
    appraisal,
    key,
    *,
    turn=None,
    context_patch=None,
    experience_id=None,
    cursor=None,
    call_identity=None,
    call_epoch=None,
):
    """校验并提交一次状态变化。

    key 是幂等键（同一件事重试必须相同），所以重试不会重复扣信任或重复推进目标；
    baseVersion 不匹配则抛 STATE_CHANGED，由上层重读快照后重新评价，绝不覆盖新状态。
    """
    proposal = appraisal.model_dump()
    with user_transaction(user_id) if cursor is None else nullcontext(cursor) as cursor:
        world = checked_world(cursor, user_id, snap["world"]["id"])
        if turn:
            validate_run(cursor, turn)
        if call_identity:
            cursor.execute(
                "SELECT id FROM imagent_call_session WHERE world_id=%s AND id=%s AND status='connected' AND generation_epoch=%s",
                (world["id"], call_identity, call_epoch),
            )
            if not cursor.fetchone():
                raise ChatError("CALL_INTERRUPTED", "旧语音轮次已结束", 409)
        cursor.execute(
            "SELECT after_version FROM imagent_state_change WHERE world_id=%s AND change_key=%s",
            (world["id"], key),
        )
        prior = cursor.fetchone()
        if prior:
            return prior["after_version"]
        if world["state_version"] != snap["world"]["state_version"]:
            raise ChatError("STATE_CHANGED", "人物状态有新变化，需要重新判断", 409)
        version = world["state_version"] + 1
        context = {**snap["context"], **(context_patch or {})}
        context["last_decision"] = appraisal.decision
        display = {
            "schemaVersion": 1,
            "visible": appraisal.display_visible,
            "status": appraisal.display_status,
        }
        _write_actor_state(cursor, world, proposal, appraisal, context, display, version)
        _write_relationship(cursor, snap, world, appraisal, version)
        _write_goals(cursor, world, appraisal, version)
        cursor.execute(
            "UPDATE imagent_world SET state_version=%s WHERE id=%s",
            (version, world["id"]),
        )
        if appraisal.activity_plan:
            _schedule_activity(cursor, world, appraisal.activity_plan, key)
        _write_state_change(
            cursor, world, experience_id, key, proposal, context, display, version
        )
        return version
