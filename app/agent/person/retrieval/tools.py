from langchain_core.tools import tool

from app.agent.person.retrieval.agent import retrieve


def memory_tool(user_id, world_id, actor_id):
    @tool
    async def recall(query: str) -> dict:
        """从当前人物自己的长期记忆中寻找相关经历；查询不能改变持有人。"""
        return await retrieve(user_id, world_id, actor_id, query)

    return recall
