from typing import Annotated

from fastapi import APIRouter, Depends, Form, Query, UploadFile

from app.api.identity import validate_gateway_user
from app.services.call_service import (
    CallAction,
    CallRequest,
    call_action,
    call_turn,
    get_call,
    heard,
    start_call,
)
from app.services.media_service import MAX_BYTES

router = APIRouter(
    prefix="/ai-message/calls",
    tags=["语音通话"],
    dependencies=[Depends(validate_gateway_user)],
)


@router.post("")
async def start(body: CallRequest):
    return await start_call(body)


@router.get("/{identity}")
def status(identity: str, user_id: Annotated[str, Query(alias="userId")]):
    call = get_call(user_id, identity)
    from app.integrations.database.mysql import mysql_service

    mysql_service.update(
        "UPDATE imagent_call_session SET transport_json=JSON_SET(transport_json,'$.lastHeartbeat',UNIX_TIMESTAMP(UTC_TIMESTAMP(6))) WHERE id=%s AND status='connected'",
        (identity,),
    )
    return {
        "id": call["id"],
        "status": call["status"],
        "epoch": call["generation_epoch"],
    }


@router.post("/{identity}/end")
def end(identity: str, body: CallAction):
    return call_action(identity, body, end=True)


@router.post("/{identity}/interrupt")
def interrupt(identity: str, body: CallAction):
    return call_action(identity, body)


@router.post("/{identity}/heard")
def played(identity: str, body: CallAction):
    return heard(identity, body)


@router.post("/{identity}/turn")
async def turn(
    identity: str,
    user_id: Annotated[str, Form(alias="userId")],
    epoch: Annotated[int, Form(ge=0)],
    file: UploadFile,
):
    return await call_turn(user_id, identity, epoch, await file.read(MAX_BYTES + 1))
