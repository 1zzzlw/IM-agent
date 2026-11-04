from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.identity import validate_gateway_user
from app.core.errors import ChatError
from app.integrations.database.mysql import mysql_service
from app.integrations.database.person_store import public_character
from app.integrations.database.user_settings_repository import (
    get_settings,
    save_settings,
)
from app.integrations.database.user_space_repository import user_transaction
from app.schemas.person import (
    CandidateRequest,
    CredentialRequest,
    InteractionRequest,
    SelectCandidateRequest,
    SlotRequest,
    UserSettings,
)
from app.services.creation_service import generate_candidates, select_candidate
from app.services.credential_service import save_credential
from app.services.moment_service import feed, interact

router = APIRouter(
    prefix="/ai-message/person",
    tags=["人物与生活"],
    dependencies=[Depends(validate_gateway_user)],
)
UserId = Annotated[str, Query(alias="userId", min_length=1, max_length=64)]


@router.get("")
def character(user_id: UserId):
    return public_character(user_id)


@router.post("/candidates")
async def candidates(body: CandidateRequest):
    return await generate_candidates(body)


@router.post("/select")
def select(body: SelectCandidateRequest):
    return select_candidate(body)


@router.get("/settings")
def settings(user_id: UserId):
    return get_settings(user_id)


@router.put("/settings")
def update_settings(body: UserSettings):
    return save_settings(body)


@router.get("/moments")
def moments(user_id: UserId):
    return feed(user_id)


@router.post("/moments/{moment_id}/interactions")
def interaction(moment_id: str, body: InteractionRequest):
    return interact(body.user_id, moment_id, body)


@router.put("/credential")
def credential(body: CredentialRequest):
    with user_transaction(body.user_id) as cursor:
        cursor.execute(
            "SELECT id FROM imagent_model_config WHERE id=%s AND owner_user_id=%s FOR UPDATE",
            (body.config_id, body.user_id),
        )
        if not cursor.fetchone():
            raise ChatError("MODEL_CONFIG_NOT_FOUND", "配置不存在或无权修改", 404)
        reference = save_credential(body.api_key)
        cursor.execute(
            "UPDATE imagent_model_config SET credential_ref=%s,version=version+1 WHERE id=%s",
            (reference, body.config_id),
        )
    return {"success": True}


@router.get("/bindings")
def bindings(user_id: UserId):
    rows = mysql_service.select(
        "SELECT slot,config_id FROM imagent_model_binding WHERE scope_key=%s AND slot='dialogue'",
        ("user:" + user_id,),
    )
    return {r["slot"]: r["config_id"] for r in rows}


@router.put("/bindings")
def bind(body: SlotRequest):
    with user_transaction(body.user_id) as cursor:
        if not body.config_id:
            cursor.execute(
                "DELETE FROM imagent_model_binding WHERE scope_key=%s AND slot=%s",
                ("user:" + body.user_id, body.slot),
            )
        else:
            cursor.execute(
                "SELECT id FROM imagent_model_config WHERE id=%s AND (owner_user_id=%s OR owner_user_id IS NULL) AND status='enabled'",
                (body.config_id, body.user_id),
            )
            if not cursor.fetchone():
                raise ChatError("MODEL_CONFIG_NOT_FOUND", "配置不存在", 404)
            cursor.execute(
                "INSERT INTO imagent_model_binding (scope_key,owner_user_id,slot,config_id) VALUES (%s,%s,%s,%s) ON DUPLICATE KEY UPDATE config_id=VALUES(config_id),version=version+1",
                ("user:" + body.user_id, body.user_id, body.slot, body.config_id),
            )
    return {"success": True}


@router.post("/presence")
def presence(body: dict):
    user_id = body.get("userId")
    if not isinstance(user_id, str) or not 0 < len(user_id) <= 64:
        raise ChatError("INVALID_USER", "用户标识无效", 422)
    with user_transaction(user_id) as cursor:
        cursor.execute(
            "UPDATE imagent_user_space SET last_seen_at=UTC_TIMESTAMP(6),settings_json=JSON_SET(settings_json,'$.chatActive',CAST(%s AS JSON),'$.typing',CAST(%s AS JSON)) WHERE user_id=%s",
            (
                people.dump(bool(body.get("chatActive", False))),
                people.dump(bool(body.get("typing", False))),
                user_id,
            ),
        )
    return {"success": True}


@router.post("/read")
def mark_read(body: dict):
    user_id = body.get("userId")
    try:
        sequence = int(body.get("upToSeq", 0))
        if (
            not isinstance(user_id, str)
            or not 0 < len(user_id) <= 64
            or not 0 < sequence < 2**64
        ):
            raise ValueError()
    except (ValueError, TypeError):
        raise ChatError("INVALID_RECEIPT", "已读回执无效", 422)
    with user_transaction(user_id) as cursor:
        world = people.current(user_id, cursor=cursor)
        if not world:
            return {"success": True}
        cursor.execute(
            "SELECT settings_json FROM imagent_user_space WHERE user_id=%s", (user_id,)
        )
        visible = (
            people.load(cursor.fetchone()["settings_json"])
            .get("presenceVisibility", {})
            .get("read", True)
        )
        cursor.execute(
            "UPDATE imagent_message_delivery d JOIN imagent_message m ON m.world_id=d.world_id AND m.id=d.message_id SET d.delivered_at=COALESCE(d.delivered_at,UTC_TIMESTAMP(6)),d.read_at=COALESCE(d.read_at,UTC_TIMESTAMP(6)),d.displayed_read_at=IF(%s,COALESCE(d.displayed_read_at,UTC_TIMESTAMP(6)),NULL) WHERE d.world_id=%s AND d.viewer_actor_id=%s AND m.seq<=%s AND d.hidden_at IS NULL",
            (visible, world["id"], world["user_actor_id"], sequence),
        )
    return {"success": True}


@router.get("/events")
def events(user_id: UserId, after: Annotated[int, Query(ge=0)] = 0):
    # 只返回资源变化通知，客户端按当前权限重新加载，避免历史 payload 泄露撤回动态。
    rows = mysql_service.select(
        "SELECT event_seq,event_type,resource_type,resource_id FROM imagent_client_event WHERE user_id=%s AND event_seq>%s ORDER BY event_seq LIMIT 100",
        (user_id, after),
    )
    return [
        {
            "seq": str(r["event_seq"]),
            "type": r["event_type"],
            "resourceType": r["resource_type"],
            "resourceId": r["resource_id"],
        }
        for r in rows
    ]
