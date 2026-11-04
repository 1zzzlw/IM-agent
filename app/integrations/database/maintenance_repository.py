"""随正常运行维护淡忘与生日，不从原始记录恢复人物已经遗忘的细节。"""

from datetime import datetime
from zoneinfo import ZoneInfo

from app.agent.person.memory.algorithms.recall import strength
from app.core.serialization import data, envelope
from app.integrations.database.character_repository import checked_world
from app.integrations.database.person_store import enqueue, record_experience
from app.integrations.database.user_space_repository import user_transaction


def maintain_world(user_id, world_id):
    with user_transaction(user_id) as cursor:
        world = checked_world(cursor, user_id, world_id)
        cursor.execute(
            "SELECT a.*,s.context_json FROM imagent_actor a JOIN imagent_actor_state s ON s.actor_id=a.id AND s.world_id=a.world_id WHERE a.world_id=%s AND a.actor_type='virtual' AND a.status='present'",
            (world_id,),
        )
        for actor in cursor.fetchall():
            now = datetime.now(ZoneInfo(actor["residence_timezone"] or "Asia/Shanghai"))
            context = data(actor["context_json"])
            if (
                actor["age_years"] is not None
                and actor["birthday_month_day"] == now.strftime("%m-%d")
                and context.get("birthday_year") != now.year
                and actor["created_at"].date() != now.date()
            ):
                context["birthday_year"] = now.year
                cursor.execute(
                    "UPDATE imagent_actor SET age_years=age_years+1 WHERE id=%s",
                    (actor["id"],),
                )
                cursor.execute(
                    "UPDATE imagent_actor_state SET context_json=%s WHERE world_id=%s AND actor_id=%s",
                    (envelope(context), world_id, actor["id"]),
                )
                eid = record_experience(
                    cursor,
                    world,
                    f"birthday:{actor['id']}:{now.year}",
                    {"text": f"今天过生日，长大一岁，现年{actor['age_years'] + 1}岁"},
                    [actor["id"]],
                )
                if actor["id"] == world["primary_actor_id"]:
                    enqueue(cursor, world, "memory", eid, {}, 1)
        cursor.execute(
            "SELECT id,cognition_json,created_at,last_accessed_at,status FROM imagent_memory WHERE world_id=%s AND owner_actor_id=%s AND status IN ('active','faded') LIMIT 1000",
            (world_id, world["primary_actor_id"]),
        )
        for memory in cursor.fetchall():
            cognition = data(memory["cognition_json"])
            heat = strength(cognition, memory["created_at"], memory["last_accessed_at"])
            if float(cognition.get("importance", 0.5)) < 0.15 and heat < 0.0001:
                cursor.execute(
                    "UPDATE imagent_memory SET status='forgotten',content=NULL,revision=revision+1,index_status='pending' WHERE id=%s",
                    (memory["id"],),
                )
            elif heat < 0.2 and memory["status"] == "active":
                cursor.execute(
                    "UPDATE imagent_memory SET status='faded' WHERE id=%s",
                    (memory["id"],),
                )
