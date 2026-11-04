import asyncio
import hashlib
import os
from pathlib import Path
from uuid import uuid4

import httpx

from app.core.errors import ChatError
from app.core.paths import RESOURCES_DIR
from app.core.serialization import data, dump
from app.integrations.database.mysql import mysql_service
from app.integrations.database.person_store import current
from app.integrations.database.user_space_repository import user_transaction
from app.llm.factory import create_chat_model
from app.services.model_config_service import resolve_model

ROOT = (
    Path(os.environ.get("IMAGENT_RUNTIME_DIR", RESOURCES_DIR / "storage" / "runtime"))
    / "media"
)
MAX_BYTES = 20 * 1024 * 1024


async def analyse_upload(user_id, blob):
    from app.llm.media import media_connection
    from app.multimodal.input import audio_emotion, describe_image, transcribe_audio

    if len(blob) > MAX_BYTES:
        raise ChatError("MEDIA_TOO_LARGE", "媒体不能超过 20 MB", 413)
    kind, mime = media_kind(blob)
    if kind == "image":
        effective = await asyncio.to_thread(capability_config, user_id, "vision")
        description = await describe_image(
            create_chat_model(effective), blob, mime
        )
        analysis = {"description": description, "source": "vision"}
    else:
        suffix = {
            "audio/wav": "wav",
            "audio/webm": "webm",
            "audio/ogg": "ogg",
            "audio/mpeg": "mp3",
        }[mime]
        effective = await asyncio.to_thread(capability_config, user_id, "asr")
        text = await transcribe_audio(media_connection(effective), blob, mime, suffix)
        analysis = {"transcript": text, "emotion": None, "emotionSource": "unavailable"}
        try:
            effective = await asyncio.to_thread(
                capability_config, user_id, "audio_emotion"
            )
            emotion = await audio_emotion(media_connection(effective), blob, suffix)
            analysis.update(emotion=emotion, emotionSource="audio_model")
        except (ChatError, httpx.HTTPError, KeyError, ValueError):
            analysis["emotionWarning"] = "声音情绪能力未配置或当前模型不支持该音频格式"
    return await asyncio.to_thread(store_media, user_id, blob, analysis=analysis)


async def synthesize(user_id, text, voice="alloy", instructions=""):
    from app.llm.media import media_connection
    from app.multimodal.output import speech

    effective = await asyncio.to_thread(capability_config, user_id, "tts")
    blob = await speech(media_connection(effective), text, voice, instructions)
    return await asyncio.to_thread(
        store_media, user_id, blob, analysis={"text": text, "voice": voice}
    )


async def generate_image(user_id, prompt):
    from app.llm.media import media_connection
    from app.multimodal.output import image

    effective = await asyncio.to_thread(capability_config, user_id, "image")
    world = await asyncio.to_thread(current, user_id)
    if not world:
        raise ChatError("CHARACTER_REQUIRED", "请先创建人物", 409)
    profile = data(world["private_profile_json"])
    reference_id = profile.get("appearance_reference")
    reference = None
    if reference_id:
        row, path = await asyncio.to_thread(owned_media, user_id, reference_id)
        reference = (await asyncio.to_thread(path.read_bytes), row["mime_type"])
    blob = await image(
        media_connection(effective),
        f"同一人物外貌：{profile.get('appearance', '')}。场景：{prompt}",
        reference,
    )
    asset = await asyncio.to_thread(
        store_media, user_id, blob, analysis={"description": prompt, "generated": True}
    )
    if not reference_id:
        await asyncio.to_thread(_save_reference, user_id, world["id"], asset["id"])
    return asset


def media_kind(blob):
    if blob.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image", "image/png"
    if blob.startswith(b"\xff\xd8\xff"):
        return "image", "image/jpeg"
    if blob[:4] == b"RIFF" and blob[8:12] == b"WEBP":
        return "image", "image/webp"
    if blob[:4] == b"RIFF" and blob[8:12] == b"WAVE":
        return "audio", "audio/wav"
    if blob.startswith(b"\x1aE\xdf\xa3"):
        return "audio", "audio/webm"
    if blob.startswith(b"OggS"):
        return "audio", "audio/ogg"
    if blob.startswith(b"ID3") or blob[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
        return "audio", "audio/mpeg"
    raise ChatError(
        "MEDIA_FORMAT_UNSUPPORTED", "支持 PNG、JPEG、WebP、WAV、WebM、Ogg、MP3", 415
    )


def store_media(user_id, blob, *, analysis=None, mime=None):
    if not blob or len(blob) > MAX_BYTES:
        raise ChatError("MEDIA_TOO_LARGE", "媒体不能为空且不能超过 20 MB", 413)
    kind, actual_mime = media_kind(blob)
    identity = str(uuid4())
    ROOT.mkdir(parents=True, exist_ok=True)
    path = ROOT / identity
    path.write_bytes(blob)
    try:
        with user_transaction(user_id) as cursor:
            world = current(user_id, cursor=cursor)
            cursor.execute(
                "INSERT INTO imagent_media_asset (id,user_id,world_id,kind,mime_type,storage_key,size_bytes,sha256,metadata_json,analysis_json,status) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'{}',%s,'ready')",
                (
                    identity,
                    user_id,
                    world["id"] if world else None,
                    kind,
                    actual_mime,
                    identity,
                    len(blob),
                    hashlib.sha256(blob).hexdigest(),
                    dump(analysis or {}),
                ),
            )
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return {
        "id": identity,
        "kind": kind,
        "mimeType": actual_mime,
        "analysis": analysis or {},
    }


def owned_media(user_id, identity):
    rows = mysql_service.select(
        "SELECT * FROM imagent_media_asset WHERE id=%s AND user_id=%s AND status='ready'",
        (identity, user_id),
    )
    if not rows:
        raise ChatError("MEDIA_NOT_FOUND", "媒体不存在", 404)
    row = rows[0]
    if row["storage_key"] != row["id"]:
        raise ChatError("MEDIA_NOT_FOUND", "媒体地址无效", 404)
    path = ROOT / row["id"]
    if not path.is_file():
        raise ChatError("MEDIA_NOT_FOUND", "媒体文件缺失", 404)
    return row, path


def capability_config(user_id, slot):
    return resolve_model(user_id, slot=slot)


def _save_reference(user_id, world_id, asset_id):
    from app.integrations.database.character_repository import checked_world

    with user_transaction(user_id) as cursor:
        world = checked_world(cursor, user_id, world_id)
        cursor.execute(
            "UPDATE imagent_actor SET private_profile_json=JSON_SET(private_profile_json,'$.data.appearance_reference',%s) WHERE world_id=%s AND id=%s AND JSON_EXTRACT(private_profile_json,'$.data.appearance_reference') IS NULL",
            (asset_id, world_id, world["primary_actor_id"]),
        )
