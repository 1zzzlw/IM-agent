import asyncio

from app.core.errors import ChatError
from app.core.serialization import load
from app.integrations.database.creation_repository import (
    add_actor,
    save_candidates,
    select_candidate,
)
from app.integrations.database.person_store import current
from app.llm.factory import create_chat_model
from app.llm.structured import structured
from app.schemas.person import CandidateBatch
from app.services.model_config_service import resolve_model


async def generate_candidates(body):
    existing = await asyncio.to_thread(current, body.user_id)
    if (
        existing
        and load(existing["initial_snapshot_json"]).get("mode") != "basic_dialogue"
    ):
        raise ChatError(
            "CHARACTER_EXISTS", "已有相处中的人物，重新创建需由管理员处理", 409
        )
    model = create_chat_model(
        await asyncio.to_thread(resolve_model, body.user_id, body.config)
    )
    batch = await structured(
        model,
        CandidateBatch,
        "按用户简单愿望创建 2 到 3 个完整虚拟人物候选。外貌、生活世界、已有往事、长期目标、知识范围、虚拟伙伴都要自洽，人物具有缺点与独立性。"
        "用户与人物刚认识，不预设恋爱或高信任。introduction 只包含姓名、身份、所在地等表面信息，不能暴露性格、爱好、隐秘往事或目标。人物可生活于现代、架空或幻想世界，时区为 IANA 时区。",
        {"wish": body.wish, "meeting": body.meeting_mode, "voice": body.voice},
    )
    return await asyncio.to_thread(save_candidates, body, batch)
