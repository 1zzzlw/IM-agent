"""记忆 Agent：把"人物实际获知的观察"整理成长期记忆与有依据的成长建议。

一次整理分三步：
1. 取尚未消费的 observation（不是原始聊天，也不是世界真相）；
2. 让固定记忆模型输出 MemoryBatch：每条记忆必须引用来源 observation，可修正旧记忆；
3. 保存正文与来源，并把来源标记为已消费（即使这批没有形成记忆也不重复整理）。

"已遗忘"的内容不会被任何一步恢复：来源查询本身就排除了 forgotten 记忆关联的观察。
"""

import asyncio

from app.agent.person.memory.prompts import MEMORY_PROMPT
from app.core.serialization import load
from app.integrations.database.memory_repository import (
    list_memories,
    pending_observations,
    save_batch,
)
from app.llm.structured import structured
from app.schemas.person import MemoryBatch


async def consolidate(user_id, snap, model):
    world = snap["world"]
    sources = await asyncio.to_thread(
        pending_observations, world["id"], world["primary_actor_id"]
    )
    if not sources:
        return
    # 已有记忆一并交给模型，用于判重与 supersedes（修正旧认知而不是堆叠重复条目）。
    existing = await asyncio.to_thread(
        list_memories, world["id"], world["primary_actor_id"], 100
    )
    batch = await structured(
        model,
        MemoryBatch,
        MEMORY_PROMPT,
        {
            "sources": [
                {"id": o["id"], "content": load(o["perceived_json"])} for o in sources
            ],
            "profile": snap["profile"],
            "existing": [{"id": r["id"], "content": r["content"]} for r in existing],
        },
    )
    await asyncio.to_thread(
        save_batch, user_id, world["id"], world["primary_actor_id"], sources, batch
    )
