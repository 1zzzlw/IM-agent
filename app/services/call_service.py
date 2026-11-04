"""连续语音通话：短音频轮次、服务端 epoch、防迟到输出和播出确认。

本模块只编排"什么时候接听、这一轮说什么、输出是否仍然有效"；
通话状态、epoch 与消息落库由 call_repository 负责。
"""

import asyncio
import re
from uuid import uuid4

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.agent.person.appraisal import appraise
from app.agent.person.dialogue.prompts import SYSTEM_PROMPT
from app.core.errors import ChatError
from app.core.serialization import data, dump, load
from app.integrations.database.call_repository import (
    call_action,
    claim_voice_turn,
    get_call,
    heard,
    record_caller_utterance,
    require_epoch,
    start_session,
    store_audio_output,
)
from app.integrations.database.mysql import mysql_service
from app.integrations.database.person_store import snapshot
from app.integrations.database.state_commit import commit_appraisal
from app.llm.factory import create_chat_model
from app.llm.structured import text_content
from app.schemas.call import CallAction, CallRequest
from app.services.media_service import analyse_upload, capability_config, synthesize
from app.services.model_config_service import resolve_model
from app.services.run_service import runs


async def start_call(body):
    user_id = body.user_id
    await asyncio.to_thread(capability_config, user_id, "asr")
    await asyncio.to_thread(capability_config, user_id, "tts")
    snap = await asyncio.to_thread(snapshot, user_id)
    if not snap:
        raise ChatError("CHARACTER_REQUIRED", "请先创建人物", 409)
    previous = await asyncio.to_thread(
        mysql_service.select,
        "SELECT * FROM imagent_call_session WHERE world_id=%s AND client_call_id=%s",
        (snap["world"]["id"], body.client_call_id),
    )
    if previous:
        return {
            "id": previous[0]["id"],
            "status": previous[0]["status"],
            "epoch": previous[0]["generation_epoch"],
        }
    relationship = next(
        (
            r
            for r in snap["relations"]
            if r["to_actor_id"] == snap["world"]["user_actor_id"]
        ),
        {},
    )
    contact = data(relationship.get("contact_policy_json"))
    can_ring = snap["context"].get("phone_accessible", True) and not (
        snap["routine"].get("sleeping")
        and (contact.get("do_not_disturb") or snap["context"].get("phone_silent"))
    )
    decision = None
    if can_ring:
        model = create_chat_model(
            await asyncio.to_thread(resolve_model, user_id)
        )
        decision = await appraise(
            model, snap, {"kind": "incoming_call", "content": "对方来电，决定是否接听"}
        )
    accepted = can_ring and decision.decision == "reply"
    result = await asyncio.to_thread(start_session, body, snap, decision, accepted)
    if accepted:
        runs.interrupt(user_id)
    return result


async def call_turn(user_id, identity, epoch, blob):
    row = await asyncio.to_thread(claim_voice_turn, user_id, identity, epoch)
    epoch = row["generation_epoch"]
    asset = await analyse_upload(user_id, blob)
    text = asset["analysis"].get("transcript", "")
    if not text:
        raise ChatError("AUDIO_REQUIRED", "通话需要语音输入", 422)
    snap = await asyncio.to_thread(snapshot, user_id)
    if not snap["context"].get("phone_accessible", True) or snap["routine"].get(
        "sleeping"
    ):
        await asyncio.to_thread(
            call_action, identity, CallAction(user_id=user_id), end=True
        )
        return {"status": "ended", "segments": [], "transcript": text}
    await asyncio.to_thread(record_caller_utterance, user_id, row, epoch, text, asset)
    model = create_chat_model(await asyncio.to_thread(resolve_model, user_id))
    from app.agent.person.retrieval.agent import retrieve

    memories = await retrieve(
        user_id, row["world_id"], snap["world"]["primary_actor_id"], text
    )
    proposal = await appraise(
        model,
        snap,
        {
            "kind": "voice_call",
            "content": text,
            "audio_clues": asset["analysis"].get("emotion"),
        },
        memories,
    )
    await asyncio.to_thread(require_epoch, user_id, identity, epoch)
    version = await asyncio.to_thread(
        commit_appraisal,
        user_id,
        snap,
        proposal,
        f"call:{identity}:{epoch}:{asset['id']}",
        call_identity=identity,
        call_epoch=epoch,
    )
    if proposal.decision in ("leave", "silence"):
        await asyncio.to_thread(
            call_action, identity, CallAction(user_id=user_id), end=True
        )
        return {"status": "ended", "transcript": text, "segments": []}
    history = await asyncio.to_thread(
        mysql_service.select,
        "SELECT origin,parts_json FROM imagent_message WHERE world_id=%s AND channel='call' AND call_id=%s ORDER BY seq DESC LIMIT 16",
        (row["world_id"], identity),
    )
    messages = [
        SystemMessage(
            content=SYSTEM_PROMPT.format(
                name=snap["world"]["display_name"],
                profile=dump(snap["profile"]),
                context=dump(
                    {
                        "emotion": proposal.emotion.model_dump(),
                        "tendency": proposal.tendency,
                        "known": snap["recent"],
                        "memories": memories,
                    }
                ),
            )
        )
    ]
    for old in reversed(history):
        parts = load(old["parts_json"])["parts"]
        content = "\n".join(p.get("text", "") for p in parts)
        messages.append(
            HumanMessage(content=content)
            if old["origin"] == "user"
            else AIMessage(content=content)
        )
    reply = text_content(await model.ainvoke(messages)).strip()
    segments = [s.strip() for s in re.split(r"(?<=[。！？!?])", reply) if s.strip()]
    if len(segments) > 4:
        segments = segments[:3] + ["".join(segments[3:])]
    voice = data(snap["world"]["voice_profile_json"]).get("voice", "alloy")
    generated = []
    for segment in segments:
        await asyncio.to_thread(require_epoch, user_id, identity, epoch)
        audio = await synthesize(user_id, segment, voice, proposal.tendency)
        generated.append({"id": str(uuid4()), "text": segment, "assetId": audio["id"]})
    await asyncio.to_thread(store_audio_output, user_id, identity, epoch, generated, version)
    return {
        "status": "connected",
        "epoch": epoch,
        "transcript": text,
        "segments": generated,
    }
