"""事件 Agent 的三个节点实现。每个节点只产出"建议"，写库统一在 life_repository。"""

import asyncio

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.runtime import Runtime

from app.agent.event.prompts import EXPERIENCE_PROMPT, GENERATE_PROMPT, IMPACT_PROMPT
from app.agent.event.state import EventContext
from app.agent.person.appraisal import appraisal_context
from app.core.serialization import data, dump, load
from app.integrations.database.person_store import (
    actor_relations,
    event_people,
    recent_observations,
)
from app.llm.structured import structured, text_content
from app.schemas.person import EventExperience, LifeImpact, LifeProposal


async def generate(state, runtime: Runtime[EventContext]):
    """事情产生：结合当地时间、活动、目标与关系提出一个情境，允许 continue。"""
    snap = state["snapshot"]
    public_life = appraisal_context(snap)
    # 外界事件不以用户私聊秘密为依据；转述必须由人物实际说出。
    public_life.pop("recent_known", None)
    public_life.pop("user_presence", None)
    proposal = await structured(
        runtime.context.model,
        LifeProposal,
        GENERATE_PROMPT,
        {**public_life, "actors": snap["actors"], "local_time": snap.get("local_time")},
    )
    return {"proposal": proposal.model_dump()}


async def experience(state, runtime: Runtime[EventContext]):
    """事件经历：按参与者的各自观察生成现场对话，再汇总这一阶段的经过。

    每位人物只拿到自己的资料、自己的观察和"现场已经说过的话"，
    因此伙伴的私聊、用户私下告诉主 AI 的秘密不会被人人共享。
    """
    snap, proposal = state["snapshot"], state["proposal"]
    people = await asyncio.to_thread(event_people, snap["world"]["id"])
    people = [p for p in people if p["id"] in proposal["participant_ids"]]
    dialogue = []
    # 每位人物只得到自己的观察和现场已说的话，不共享他人的私聊上下文。
    for person in people[:4]:
        known = await asyncio.to_thread(
            recent_observations, snap["world"]["id"], person["id"]
        )
        relations = await asyncio.to_thread(
            actor_relations, snap["world"]["id"], person["id"]
        )
        response = await runtime.context.model.ainvoke(
            [
                SystemMessage(
                    content="你扮演事件中的这一位人物，只知道自己的设定、提供的实际观察和现场已说的话。其他人物没有说出的事、用户未转述的秘密你都不知道。根据性格决定说一句话，也可空字符串保持沉默。不要旁白、不要代替别人说话。"
                ),
                HumanMessage(
                    content=dump(
                        {
                            "name": person["display_name"],
                            "profile": data(person["private_profile_json"]),
                            "own_relations": relations,
                            "own_known": [load(o["perceived_json"]) for o in known],
                            "situation": {
                                "title": proposal["title"],
                                "location": proposal["location"],
                                "activity": proposal["activity"],
                            },
                            "heard_here": dialogue,
                        }
                    )
                ),
            ]
        )
        spoken = text_content(response).strip()[:500]
        if spoken:
            dialogue.append({"speaker_id": person["id"], "text": spoken})
    result = await structured(
        runtime.context.model,
        EventExperience,
        EXPERIENCE_PROMPT,
        {
            "stage": "ending" if state.get("finishing") else "starting",
            "proposal": {k: v for k, v in proposal.items() if k != "reason"},
            "actual_dialogue": dialogue,
            "actors": snap["actors"],
        },
    )
    value = result.model_dump()
    value["dialogue"] = dialogue
    return {"experience": value}


async def impact(state, runtime: Runtime[EventContext]):
    """最终影响：判断这次经历意味着什么，可提出主动消息、朋友圈与目标变化。

    主 AI 没有获知的事件（既不是参与者、perceived_by_primary 也为假）不产生
    任何心理影响，只保留为世界里的经过。
    """
    primary = state["snapshot"]["world"]["primary_actor_id"]
    if (
            not state["proposal"]["perceived_by_primary"]
            and primary not in state["proposal"]["participant_ids"]
    ):
        return {"impact": LifeImpact().model_dump()}
    result = await structured(
        runtime.context.model,
        LifeImpact,
        IMPACT_PROMPT,
        {
            **appraisal_context(state["snapshot"]),
            "proposal": state["proposal"],
            "actual": state["experience"],
            "actors": state["snapshot"]["actors"],
            "stage": "ending" if state.get("finishing") else "starting",
        },
    )
    return {"impact": result.model_dump()}
