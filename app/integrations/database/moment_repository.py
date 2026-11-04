from uuid import uuid4

from app.core.errors import ChatError
from app.core.serialization import digest, dump, load
from app.integrations.database.character_repository import checked_world
from app.integrations.database.mysql import mysql_service
from app.integrations.database.person_store import current, enqueue, record_experience
from app.integrations.database.user_space_repository import user_transaction


def publish(
    cursor,
    world,
    text,
    audience,
    key,
    experience_id=None,
    comments=None,
    image_asset=None,
):
    mid = str(uuid4())
    cursor.execute(
        "SELECT id FROM imagent_moment WHERE world_id=%s AND dedupe_key=%s",
        (world["id"], key),
    )
    previous = cursor.fetchone()
    if previous:
        return previous["id"]
    cursor.execute(
        "SELECT id FROM imagent_actor WHERE world_id=%s AND status='present'",
        (world["id"],),
    )
    valid = {x["id"] for x in cursor.fetchall()}
    viewers = set(audience) & valid | {world["primary_actor_id"]}
    parts = [{"type": "text", "text": text}]
    if image_asset:
        parts.append({"type": "image", "assetId": image_asset})
    cursor.execute(
        "INSERT INTO imagent_moment (id,world_id,author_actor_id,parts_json,source_experience_id,expression_json,dedupe_key,published_at) VALUES (%s,%s,%s,%s,%s,'{}',%s,UTC_TIMESTAMP(6))",
        (
            mid,
            world["id"],
            world["primary_actor_id"],
            dump({"parts": parts}),
            experience_id,
            key,
        ),
    )
    for viewer in viewers:
        cursor.execute(
            "INSERT INTO imagent_moment_audience (world_id,moment_id,viewer_actor_id,granted_at) VALUES (%s,%s,%s,UTC_TIMESTAMP(6))",
            (world["id"], mid, viewer),
        )
    record_experience(
        cursor,
        world,
        "post:" + mid,
        {
            "kind": "published_post",
            "text": text,
            "meaning": "said_not_verified_fact",
            "momentId": mid,
        },
        [world["primary_actor_id"]],
        observers=[world["primary_actor_id"]],
        realm="shared_virtual",
    )
    for i, comment in enumerate(comments or []):
        actor = comment["speaker_id"]
        if actor not in viewers or actor in (
            world["user_actor_id"],
            world["primary_actor_id"],
        ):
            continue
        cursor.execute(
            "INSERT INTO imagent_moment_interaction (id,world_id,moment_id,actor_id,kind,content,dedupe_key,payload_hash) VALUES (%s,%s,%s,%s,'comment',%s,%s,%s)",
            (
                str(uuid4()),
                world["id"],
                mid,
                actor,
                comment["text"],
                f"generated:{i}",
                digest(comment),
            ),
        )
        record_experience(
            cursor,
            world,
            f"moment:{mid}:{i}",
            {
                "kind": "comment",
                "text": comment["text"],
                "speakerId": actor,
                "momentText": text,
            },
            [actor],
            observers=[world["primary_actor_id"], actor],
        )
    return mid


def feed(user_id, limit=30):
    world = current(user_id)
    if not world:
        return []
    rows = mysql_service.select(
        "SELECT m.*,a.display_name FROM imagent_moment m JOIN imagent_moment_audience v ON v.world_id=m.world_id AND v.moment_id=m.id JOIN imagent_actor a ON a.id=m.author_actor_id WHERE m.world_id=%s AND m.status='published' AND v.viewer_actor_id=%s AND v.revoked_at IS NULL ORDER BY m.published_at DESC,m.id DESC LIMIT %s",
        (world["id"], world["user_actor_id"], limit),
    )
    result = []
    for row in rows:
        interactions = mysql_service.select(
            "SELECT i.id,i.actor_id,a.display_name AS name,i.kind,i.content FROM imagent_moment_interaction i JOIN imagent_actor a ON a.id=i.actor_id AND a.world_id=i.world_id WHERE i.world_id=%s AND i.moment_id=%s AND i.active=TRUE ORDER BY i.created_at,i.id",
            (world["id"], row["id"]),
        )
        result.append(
            {
                "id": row["id"],
                "name": row["display_name"],
                "parts": load(row["parts_json"]),
                "publishedAt": row["published_at"].isoformat() + "Z",
                "interactions": interactions,
                "liked": any(
                    i["actor_id"] == world["user_actor_id"] and i["kind"] == "like"
                    for i in interactions
                ),
            }
        )
    return result


def interact(user_id, moment_id, body):
    with user_transaction(user_id) as cursor:
        world = current(user_id, cursor=cursor)
        if not world:
            raise ChatError("CHARACTER_REQUIRED", "请先创建人物", 409)
        world = checked_world(cursor, user_id, world["id"])
        cursor.execute(
            "SELECT m.id,m.parts_json FROM imagent_moment m JOIN imagent_moment_audience v ON v.world_id=m.world_id AND v.moment_id=m.id WHERE m.world_id=%s AND m.id=%s AND m.status='published' AND v.viewer_actor_id=%s AND v.revoked_at IS NULL FOR UPDATE",
            (world["id"], moment_id, world["user_actor_id"]),
        )
        moment = cursor.fetchone()
        if not moment:
            raise ChatError("MOMENT_NOT_FOUND", "动态不可见或已撤回", 404)
        if body.kind == "comment" and not body.content.strip():
            raise ChatError("EMPTY_COMMENT", "评论不能为空", 422)
        key = "like" if body.kind == "like" else body.request_id
        cursor.execute(
            "SELECT id,payload_hash,revision FROM imagent_moment_interaction WHERE world_id=%s AND moment_id=%s AND actor_id=%s AND dedupe_key=%s",
            (world["id"], moment_id, world["user_actor_id"], key),
        )
        old = cursor.fetchone()
        fingerprint = digest({"content": body.content, "active": body.active})
        if old and body.kind == "like" and old["payload_hash"] == fingerprint:
            return {"success": True}
        if old and body.kind == "comment":
            if old["payload_hash"] != fingerprint:
                raise ChatError("REQUEST_CONFLICT", "相同请求不能使用不同内容", 409)
            return {"success": True}
        revision = old["revision"] + 1 if old else 1
        cursor.execute(
            "INSERT INTO imagent_moment_interaction (id,world_id,moment_id,actor_id,kind,content,active,dedupe_key,payload_hash) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE active=VALUES(active),payload_hash=VALUES(payload_hash),revision=revision+1",
            (
                str(uuid4()),
                world["id"],
                moment_id,
                world["user_actor_id"],
                body.kind,
                body.content if body.kind == "comment" else None,
                body.active,
                key,
                fingerprint,
            ),
        )
        eid = record_experience(
            cursor,
            world,
            f"interaction:{moment_id}:{key}:{revision}",
            {
                "kind": body.kind,
                "text": body.content,
                "moment": load(moment["parts_json"]),
                "active": body.active,
                "speakerId": world["user_actor_id"],
            },
            [world["primary_actor_id"], world["user_actor_id"]],
            realm="shared_virtual",
        )
        enqueue(cursor, world, "interaction", eid, {"experienceId": eid})
        enqueue(cursor, world, "memory", eid, {}, 5)
    return {"success": True}


def attach_image(user_id, world_id, moment_id, asset_id):
    with user_transaction(user_id) as cursor:
        checked_world(cursor, user_id, world_id)
        cursor.execute(
            "SELECT parts_json,status FROM imagent_moment WHERE world_id=%s AND id=%s FOR UPDATE",
            (world_id, moment_id),
        )
        row = cursor.fetchone()
        if not row or row["status"] != "published":
            return
        parts = load(row["parts_json"])
        if any(p.get("type") == "image" for p in parts["parts"]):
            return
        parts["parts"].append({"type": "image", "assetId": asset_id})
        cursor.execute(
            "UPDATE imagent_moment SET parts_json=%s,version=version+1 WHERE id=%s AND world_id=%s",
            (dump(parts), moment_id, world_id),
        )
