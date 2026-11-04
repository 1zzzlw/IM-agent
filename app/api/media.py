from typing import Annotated

from fastapi import APIRouter, Depends, Form, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import Field

from app.api.identity import validate_gateway_user
from app.schemas.base import ApiModel
from app.services.media_service import (
    MAX_BYTES,
    analyse_upload,
    owned_media,
)

router = APIRouter(
    prefix="/ai-message/media",
    tags=["媒体"],
    dependencies=[Depends(validate_gateway_user)],
)


class GenerateMediaRequest(ApiModel):
    user_id: str = Field(min_length=1, max_length=64)
    text: str = Field(min_length=1, max_length=3000)


@router.post("")
async def upload(user_id: Annotated[str, Form(alias="userId")], file: UploadFile):
    blob = await file.read(MAX_BYTES + 1)
    return await analyse_upload(user_id, blob)


@router.get("/{identity}")
def download(identity: str, user_id: Annotated[str, Query(alias="userId")]):
    row, path = owned_media(user_id, identity)
    return FileResponse(
        path,
        media_type=row["mime_type"],
        headers={"Cache-Control": "private, no-store"},
    )
