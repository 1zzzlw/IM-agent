"""把"这一刻能说出口的内容"拼成对话模型的消息序列。

这里只做上下文装配，不查询数据库、不判断人物状态：
- 人物资料取自主 AI 的快照（不是整份世界真相）；
- 历史消息已在仓储层按可见性、条数和正文总量裁剪；
- 记忆与评价结果作为上下文片段提供给模型，不直接变成事实。
"""

import json

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.agent.person.dialogue.prompts import SYSTEM_PROMPT


def dialogue_messages(turn, snap=None, retrieval=None, appraisal=None):
    """仅提供当前主角色自己的设定和确实可见的对话，不遍历世界真相。"""
    world = turn["world"]
    profile = world["private_profile_json"]
    if isinstance(profile, str):
        profile = json.loads(profile)
    if snap:
        profile = snap["profile"]
    # 仓储已按当前角色的可见范围、条数和正文总量裁剪，沿用同一份快照。
    messages = []
    for item in turn["history"]:
        kind = HumanMessage if item["role"] == "user" else AIMessage
        messages.append(kind(content=item["content"]))
    return [
        SystemMessage(
            content=SYSTEM_PROMPT.format(
                name=snap["world"]["display_name"] if snap else world["display_name"],
                profile=json.dumps(profile, ensure_ascii=False),
                context=json.dumps(
                    {
                        "state": {
                            "emotion": snap["emotion"],
                            "routine": snap["routine"],
                            "context": snap["context"],
                        }
                        if snap
                        else {},
                        "known_recent": snap["recent"] if snap else [],
                        "memories": retrieval or {},
                        "appraisal": appraisal or {},
                    },
                    ensure_ascii=False,
                    default=str,
                ),
            )
        ),
        *messages,
    ]
