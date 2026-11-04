"""提取 Agent：在人物自己的记忆中按"相关性 + 重要性 + 记忆温度"召回。

两条召回通道合并使用：
- 向量检索（Chroma）只在 ``revision`` 与 MySQL 一致时才算数，避免拿旧正文的相似度；
- 关键词/二元组匹配兜底，Embedding 未配置或向量库不可用时仍能工作。

选中记忆后会回写 last_accessed_at 并增加 recalls：唤起作用本身也是记忆机制的一部分。
返回结果带 epistemic_type 与 confidence，让评价节点区分"亲眼见过"和"听说的推测"。
"""

import asyncio

from app.agent.person.memory.algorithms.recall import rank
from app.integrations.database.memory_repository import list_memories, reinforce
from app.integrations.vector_store.memory_store import search


async def retrieve(user_id, world_id, actor_id, query):
    rows, (hits, mode) = await asyncio.gather(
        asyncio.to_thread(list_memories, world_id, actor_id),
        asyncio.to_thread(search, world_id, actor_id, query),
    )
    # 向量命中必须回表核对版本：正文改过（revision 变了）但索引未同步时不能当依据。
    semantic = {
        r["id"]: hits[r["id"]][0]
        for r in rows
        if r["id"] in hits and hits[r["id"]][1] == r["revision"]
    }
    selected = rank(query, rows, semantic)
    await asyncio.to_thread(
        reinforce, user_id, world_id, actor_id, [r["id"] for r in selected]
    )
    return {
        "mode": mode,
        "memories": [
            {
                "id": r["id"],
                "content": r["content"],
                "epistemicType": r["epistemic_type"],
                "confidence": r["cognition"].get("confidence", 0.5),
            }
            for r in selected
        ],
        "found": bool(selected),
    }
