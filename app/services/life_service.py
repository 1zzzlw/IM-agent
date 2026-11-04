import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

from app.agent.event.graph import event_graph
from app.agent.event.state import EventContext
from app.core.serialization import load
from app.integrations.database.life_repository import commit_life_event
from app.integrations.database.person_store import snapshot
from app.services.run_service import runs


async def advance_life(user_id, model, task):
    snap = await asyncio.to_thread(snapshot, user_id)
    if not snap or snap["world"]["id"] != task["world_id"]:
        return
    snap["local_time"] = datetime.now(
        ZoneInfo(snap["world"]["residence_timezone"] or "Asia/Shanghai")
    ).isoformat()
    payload = load(task["payload_json"])
    finishing = task["kind"] == "event_end"
    state = {"snapshot": snap, "finishing": finishing}
    if finishing:
        state["proposal"] = payload["proposal"]
    else:
        # 当前活动是生成约束，合理的外因仍可能打断；不能因 busy 就屏蔽整个世界。
        state["snapshot"]["ongoing_activity"] = snap["context"].get("activity")
    result = await event_graph.ainvoke(state, context=EventContext(model=model))
    if result["proposal"]["action"] == "continue":
        return
    interrupted = await asyncio.to_thread(commit_life_event, user_id, task, snap, result)
    if interrupted:
        runs.interrupt(user_id)
