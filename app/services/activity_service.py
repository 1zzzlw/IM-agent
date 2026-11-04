import asyncio

from langchain_core.messages import HumanMessage, SystemMessage

from app.agent.person.appraisal import appraise
from app.core.serialization import dump, load
from app.integrations.database.activity_repository import arrive_shared_activity
from app.llm.structured import text_content


async def arrive(user_id, task, snap, model):
    from app.integrations.database.mysql import mysql_service

    rows = await asyncio.to_thread(
        mysql_service.select,
        "SELECT * FROM imagent_life_event WHERE world_id=%s AND id=%s AND status='planned'",
        (task["world_id"], load(task["payload_json"])["eventId"]),
    )
    if not rows:
        return
    event = rows[0]
    appraisal = await appraise(
        model,
        snap,
        {
            "kind": "appointment_due",
            "title": event["title"],
            "planned": event["scheduled_start_at"],
        },
    )
    message = ""
    if appraisal.decision == "reply":
        message = text_content(
            await model.ainvoke(
                [
                    SystemMessage(
                        content="你是"
                        + snap["world"]["display_name"]
                        + "。按人物性格和当前情绪，为已经约好的活动发送一句自然的邀请，只输出要说的话。"
                    ),
                    HumanMessage(
                        content=dump(
                            {
                                "profile": snap["profile"],
                                "tendency": appraisal.tendency,
                                "title": event["title"],
                            }
                        )
                    ),
                ]
            )
        )
    await asyncio.to_thread(arrive_shared_activity, user_id, task, snap, event, appraisal, message)
