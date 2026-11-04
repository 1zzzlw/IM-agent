"""人物读模型与基础写入：快照、经历/观察、任务入队。

这一层只做"人物知道什么、发生过什么"的存取，不判断行为、不提交状态变化：
- ``snapshot`` 是四个 Agent 共同的输入，已经按主 AI 的实际知情范围裁剪；
- ``record_experience`` 是事实链的起点（经过 + 每个知情者的观察）；
- ``enqueue`` 只登记后台任务，不执行。

状态提交在 ``state_commit.commit_appraisal``；用户设置见 ``user_settings_repository``。
"""

from datetime import datetime, timezone
from uuid import uuid4

from app.core.serialization import data, digest, dump, load
from app.integrations.database.mysql import mysql_service


def current(user_id, cursor=None):
    """当前用户正在相处的世界 + 主 AI 的稳定资料。"""
    sql = (
        "SELECT w.*,a.display_name,a.private_profile_json,a.public_profile_json,a.voice_profile_json,a.residence_timezone,a.age_years,a.birthday_month_day "
        "FROM imagent_user_space s JOIN imagent_world w ON w.id=s.active_world_id AND w.user_id=s.user_id "
        "JOIN imagent_actor a ON a.id=w.primary_actor_id AND a.world_id=w.id WHERE s.user_id=%s AND w.status='active'"
    )
    if cursor is None:
        rows = mysql_service.select(sql, (user_id,))
    else:
        cursor.execute(sql, (user_id,))
        rows = cursor.fetchall()
    return rows[0] if rows else None


def snapshot(user_id):
    """人物此刻的完整读模型：资料、状态、关系、目标、近两天实际观察。

    只返回主 AI 自己的观察，不提供初始往事模板、未获知的世界事件或其他人物的内心。
    """
    world = current(user_id)
    if not world:
        return None
    wid, actor = world["id"], world["primary_actor_id"]
    states = mysql_service.select(
        "SELECT * FROM imagent_actor_state WHERE world_id=%s AND actor_id=%s",
        (wid, actor),
    )
    state = states[0] if states else {}
    relations = mysql_service.select(
        "SELECT r.*,a.display_name FROM imagent_relationship r JOIN imagent_actor a ON a.world_id=r.world_id AND a.id=r.to_actor_id WHERE r.world_id=%s AND r.from_actor_id=%s",
        (wid, actor),
    )
    goals = mysql_service.select(
        "SELECT * FROM imagent_goal WHERE world_id=%s AND actor_id=%s AND status IN ('active','paused')",
        (wid, actor),
    )
    # 已遗忘内容关联的观察不再进入"近况"，避免从原始记录里把遗忘的东西捞回来。
    observations = mysql_service.select(
        "SELECT id,experience_id,perceived_json,obtained_at FROM imagent_observation WHERE world_id=%s AND observer_actor_id=%s "
        "AND method <> 'initial_seed' AND obtained_at>DATE_SUB(UTC_TIMESTAMP(6),INTERVAL 2 DAY) "
        "AND NOT EXISTS (SELECT 1 FROM imagent_memory_source ms JOIN imagent_memory m ON m.id=ms.memory_id WHERE ms.observation_id=imagent_observation.id AND m.status='forgotten') "
        "ORDER BY obtained_at DESC,id DESC LIMIT 12",
        (wid, actor),
    )
    actors = mysql_service.select(
        "SELECT id,display_name,private_profile_json FROM imagent_actor WHERE world_id=%s AND status='present' AND actor_type='virtual'",
        (wid,),
    )
    space = mysql_service.select(
        "SELECT settings_json,last_seen_at FROM imagent_user_space WHERE user_id=%s",
        (user_id,),
    )[0]
    settings = load(space["settings_json"])
    visibility = settings.get("presenceVisibility", {})
    recent_online = (
        space["last_seen_at"]
        and (
            datetime.now(timezone.utc).replace(tzinfo=None) - space["last_seen_at"]
        ).total_seconds()
        < 25
    )
    presence = {}
    if visibility.get("online", True):
        presence["online"] = bool(recent_online)
    if visibility.get("typing", True):
        presence["typing"] = bool(recent_online and settings.get("typing"))
    if visibility.get("read", True):
        read = mysql_service.select(
            "SELECT MAX(m.seq) AS seq FROM imagent_message m JOIN imagent_message_delivery d ON d.world_id=m.world_id AND d.message_id=m.id WHERE m.world_id=%s AND m.origin='agent' AND d.viewer_actor_id=%s AND d.displayed_read_at IS NOT NULL",
            (wid, world["user_actor_id"]),
        )
        presence["read_seq"] = str(read[0]["seq"]) if read[0]["seq"] else None
    # 不提供初始往事模板、未获知世界事件或其他人物的内心。
    return {
        "world": world,
        "state": state,
        "emotion": data(state.get("emotion_json")),
        "routine": data(state.get("routine_json")),
        "context": data(state.get("context_json")),
        "display": load(state.get("display_state_json")),
        "profile": data(world["private_profile_json"]),
        "relations": relations,
        "goals": goals,
        "user_presence": presence,
        "recent": [
            {
                "id": x["id"],
                "experienceId": x["experience_id"],
                "content": load(x["perceived_json"]),
                "time": x["obtained_at"],
            }
            for x in observations
        ],
        "actors": [{"id": x["id"], "name": x["display_name"]} for x in actors],
    }


def record_experience(
    cursor,
    world,
    key,
    actual,
    participants,
    *,
    event_id=None,
    kind="life",
    realm="virtual_world",
    observers=None,
):
    """追加一条不可变经过，并为观察者写各自的 observation。

    发言只证明"说过"，不证明内容属实（perceived_json 与 actual_json 同源，
    但含义是"这个人获知了这段内容"）；同一 world+dedupe_key 重试不会重复写。
    """
    cursor.execute(
        "SELECT id FROM imagent_experience WHERE world_id=%s AND dedupe_key=%s",
        (world["id"], key),
    )
    old = cursor.fetchone()
    if old:
        return old["id"]
    eid = str(uuid4())
    cursor.execute(
        "INSERT INTO imagent_experience (id,world_id,event_id,kind,realm,participant_ids_json,actual_json,occurred_at,dedupe_key,payload_hash) VALUES (%s,%s,%s,%s,%s,%s,%s,UTC_TIMESTAMP(6),%s,%s)",
        (
            eid,
            world["id"],
            event_id,
            kind,
            realm,
            dump(participants),
            dump(actual),
            key,
            digest(actual),
        ),
    )
    for observer in observers if observers is not None else participants:
        cursor.execute(
            "INSERT INTO imagent_observation (id,world_id,experience_id,observer_actor_id,method,perceived_json,obtained_at) VALUES (%s,%s,%s,%s,%s,%s,UTC_TIMESTAMP(6))",
            (
                str(uuid4()),
                world["id"],
                eid,
                observer,
                "initial_seed" if kind == "initial" else "direct",
                dump(actual),
            ),
        )
    return eid


def enqueue(cursor, world, kind, key, payload, delay=0):
    """登记一个可恢复的后台任务；同一 dedupe_key 不会重复投递。"""
    cursor.execute(
        "INSERT INTO imagent_background_task (id,user_id,world_id,kind,dedupe_key,request_hash,payload_json,result_json,run_after) VALUES (%s,%s,%s,%s,%s,%s,%s,'{}',DATE_ADD(UTC_TIMESTAMP(6),INTERVAL %s SECOND)) ON DUPLICATE KEY UPDATE id=id",
        (
            str(uuid4()),
            world["user_id"],
            world["id"],
            kind,
            key,
            digest(payload),
            dump(payload),
            delay,
        ),
    )


def public_character(user_id):
    """对外展示的人物资料：只含公开字段与该角色愿意展示的状态。"""
    world = current(user_id)
    if not world:
        return {"exists": False}
    rows = mysql_service.select(
        "SELECT display_state_json FROM imagent_actor_state WHERE world_id=%s AND actor_id=%s",
        (world["id"], world["primary_actor_id"]),
    )
    display = load(rows[0]["display_state_json"]) if rows else {}
    return {
        "exists": True,
        "worldId": world["id"],
        "name": world["display_name"],
        "requiresCreation": load(world["initial_snapshot_json"]).get("mode")
        == "basic_dialogue",
        "profile": data(world["public_profile_json"]),
        "status": display.get("status") if display.get("visible") else None,
    }


def event_people(world_id):
    return mysql_service.select(
        "SELECT id,display_name,private_profile_json FROM imagent_actor WHERE world_id=%s AND actor_type='virtual' AND status='present'",
        (world_id,),
    )


def recent_observations(world_id, actor_id, limit=8):
    return mysql_service.select(
        "SELECT perceived_json FROM imagent_observation WHERE world_id=%s AND observer_actor_id=%s AND method <> 'initial_seed' AND obtained_at>DATE_SUB(UTC_TIMESTAMP(6),INTERVAL 2 DAY) ORDER BY obtained_at DESC LIMIT %s",
        (world_id, actor_id, limit),
    )


def saved_appraisal(user_id, world_id, key):
    """按幂等键取回已经提交过的评价，用于同轮重试时复用。"""
    rows = mysql_service.select(
        "SELECT c.appraisal_json,c.after_version FROM imagent_state_change c JOIN imagent_world w ON w.id=c.world_id JOIN imagent_user_space s ON s.active_world_id=w.id AND s.user_id=w.user_id WHERE s.user_id=%s AND c.world_id=%s AND c.change_key=%s",
        (user_id, world_id, key),
    )
    return (
        {
            "appraisal": load(rows[0]["appraisal_json"]),
            "version": rows[0]["after_version"],
        }
        if rows
        else None
    )


def actor_relations(world_id, actor_id):
    return mysql_service.select(
        "SELECT r.to_actor_id,a.display_name,r.relation_label,r.assessment_json FROM imagent_relationship r JOIN imagent_actor a ON a.world_id=r.world_id AND a.id=r.to_actor_id WHERE r.world_id=%s AND r.from_actor_id=%s",
        (world_id, actor_id),
    )
