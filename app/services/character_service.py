import json
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from app.agent.person.dialogue.prompts import SYSTEM_PROMPT


def dialogue_messages(turn):
    """仅提供当前主角色自己的设定和确实可见的对话，不遍历世界真相。"""
    world = turn["world"]
    profile = world["private_profile_json"]
    if isinstance(profile, str):
        profile = json.loads(profile)
    # 仓储已按当前角色的可见范围、条数和正文总量裁剪，沿用同一份快照。
    messages = []
    for item in turn["history"]:
        kind = HumanMessage if item["role"] == "user" else AIMessage
        messages.append(kind(content=item["content"]))
    return [
        SystemMessage(
            content=SYSTEM_PROMPT.format(
                name=world["display_name"],
                profile=json.dumps(profile, ensure_ascii=False),
            )
        ),
        *messages,
    ]
