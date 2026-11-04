"""MySQL 保存正文、来源和有效性，向量结果必须在这里重新验证。"""

from datetime import datetime, timezone
from uuid import uuid4

from app.core.serialization import data, digest, dump, envelope
from app.integrations.database.character_repository import checked_world
from app.integrations.database.mysql import mysql_service
from app.integrations.database.user_space_repository import user_transaction


def list_memories(world_id, actor_id, limit=1000):
    rows = mysql_service.select(
        "SELECT * FROM imagent_memory WHERE world_id=%s AND owner_actor_id=%s AND status IN ('active','faded') AND content IS NOT NULL AND (valid_until IS NULL OR valid_until>UTC_TIMESTAMP(6)) ORDER BY updated_at DESC LIMIT %s",
        (world_id, actor_id, limit),
    )
    for row in rows:
        row["cognition"] = data(row["cognition_json"])
    return rows


def pending_observations(world_id, actor_id):
    # 即使一次没有形成记忆，也用 task 去重标记消耗过的来源。
    return mysql_service.select(
        "SELECT o.id,o.perceived_json,o.obtained_at FROM imagent_observation o WHERE o.world_id=%s AND o.observer_actor_id=%s AND NOT EXISTS (SELECT 1 FROM imagent_background_task t WHERE t.world_id=o.world_id AND t.kind='memory_consumed' AND t.dedupe_key=o.id) ORDER BY o.obtained_at,o.id LIMIT 30",
        (world_id, actor_id),
    )


def save_batch(user_id, world_id, actor_id, observations, batch):
    allowed = {x["id"] for x in observations}
    if any(not set(item.source_ids) <= allowed for item in batch.memories) or (
        batch.growth and not set(batch.growth.source_ids) <= allowed
    ):
        raise ValueError("模型引用了未提供的观察")
    ids = []
    with user_transaction(user_id) as cursor:
        world = checked_world(cursor, user_id, world_id)
        if actor_id != world["primary_actor_id"]:
            raise ValueError("invalid memory owner")
        for oid in allowed:
            cursor.execute(
                "SELECT id FROM imagent_background_task WHERE world_id=%s AND kind='memory_consumed' AND dedupe_key=%s",
                (world_id, oid),
            )
            if cursor.fetchone():
                return []  # 已有并发整理提交，重新领取尚未消费的观察即可。
        for item in batch.memories:
            if not set(item.source_ids) <= allowed:
                raise ValueError("模型引用了未提供的观察")
            fingerprint = digest(
                {"content": item.content, "sources": sorted(item.source_ids)}
            )
            content_key = digest(" ".join(item.content.split()).casefold())
            cursor.execute(
                "SELECT id FROM imagent_memory WHERE world_id=%s AND owner_actor_id=%s AND status IN ('active','faded') AND (JSON_UNQUOTE(JSON_EXTRACT(cognition_json,'$.data.fingerprint'))=%s OR JSON_UNQUOTE(JSON_EXTRACT(cognition_json,'$.data.contentKey'))=%s)",
                (world_id, actor_id, fingerprint, content_key),
            )
            duplicate = cursor.fetchone()
            if duplicate:
                for oid in set(item.source_ids):
                    cursor.execute(
                        "INSERT INTO imagent_memory_source (world_id,owner_actor_id,memory_id,observation_id) VALUES (%s,%s,%s,%s) ON DUPLICATE KEY UPDATE memory_id=memory_id",
                        (world_id, actor_id, duplicate["id"], oid),
                    )
                cursor.execute(
                    "UPDATE imagent_memory SET last_accessed_at=UTC_TIMESTAMP(6),status='active' WHERE id=%s",
                    (duplicate["id"],),
                )
                continue
            previous = item.supersedes_id
            if previous:
                cursor.execute(
                    "SELECT id FROM imagent_memory WHERE id=%s AND world_id=%s AND owner_actor_id=%s AND status IN ('active','faded') FOR UPDATE",
                    (previous, world_id, actor_id),
                )
                if not cursor.fetchone():
                    previous = None
                else:
                    cursor.execute(
                        "UPDATE imagent_memory SET status='superseded',valid_until=UTC_TIMESTAMP(6),revision=revision+1,index_status='pending' WHERE id=%s",
                        (previous,),
                    )
            mid = str(uuid4())
            cognition = {
                "importance": item.importance,
                "confidence": item.confidence,
                "recalls": 0,
                "fingerprint": fingerprint,
                "contentKey": content_key,
                "algorithm": "recall-v1",
            }
            cursor.execute(
                "INSERT INTO imagent_memory (id,world_id,owner_actor_id,kind,epistemic_type,content,subject_actor_ids_json,cognition_json,supersedes_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    mid,
                    world_id,
                    actor_id,
                    item.kind,
                    item.epistemic_type,
                    item.content,
                    dump([actor_id]),
                    envelope(cognition),
                    previous,
                ),
            )
            for oid in set(item.source_ids):
                cursor.execute(
                    "INSERT INTO imagent_memory_source (world_id,owner_actor_id,memory_id,observation_id) VALUES (%s,%s,%s,%s)",
                    (world_id, actor_id, mid, oid),
                )
            ids.append(mid)
        for oid in allowed:
            cursor.execute(
                "INSERT INTO imagent_background_task (id,user_id,world_id,kind,dedupe_key,request_hash,payload_json,result_json,status) VALUES (%s,%s,%s,'memory_consumed',%s,%s,'{}','{}','succeeded') ON DUPLICATE KEY UPDATE id=id",
                (str(uuid4()), user_id, world_id, oid, digest(oid)),
            )
        if batch.growth:
            growth = batch.growth
            now = datetime.now(timezone.utc)
            from app.agent.person.memory.reflection import growth_update

            profile = growth_update(
                data(world["private_profile_json"]), growth, allowed, now
            )
            if profile is not None:
                version = world["state_version"] + 1
                cursor.execute(
                    "UPDATE imagent_actor SET private_profile_json=%s WHERE world_id=%s AND id=%s",
                    (envelope(profile), world_id, actor_id),
                )
                cursor.execute(
                    "UPDATE imagent_world SET state_version=%s WHERE id=%s",
                    (version, world_id),
                )
                cursor.execute(
                    "INSERT INTO imagent_state_change (id,world_id,origin_type,change_key,request_hash,before_version,after_version,changes_json,appraisal_json) VALUES (%s,%s,'agent',%s,%s,%s,%s,%s,%s)",
                    (
                        str(uuid4()),
                        world_id,
                        "growth:" + digest(sorted(growth.source_ids)),
                        digest(growth.model_dump()),
                        version - 1,
                        version,
                        dump({"development": growth.note}),
                        dump({"sourceIds": growth.source_ids}),
                    ),
                )
    return ids


def reinforce(user_id, world_id, actor_id, ids):
    with user_transaction(user_id) as cursor:
        checked_world(cursor, user_id, world_id)
        for mid in ids:
            cursor.execute(
                "UPDATE imagent_memory SET last_accessed_at=UTC_TIMESTAMP(6),status='active',cognition_json=JSON_SET(cognition_json,'$.data.recalls',COALESCE(JSON_EXTRACT(cognition_json,'$.data.recalls')+0,0)+1) WHERE id=%s AND world_id=%s AND owner_actor_id=%s AND status IN ('active','faded') AND content IS NOT NULL",
                (mid, world_id, actor_id),
            )
