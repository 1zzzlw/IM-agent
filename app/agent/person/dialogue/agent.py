"""对话 Agent 的五个节点：准备上下文、决策、执行工具、角色评价、生成表达。

这一层只负责"怎么想、怎么说"，不负责"谁有权改状态"：
- 读取一律走 snapshot，按当前人物自己的观察范围取数据；
- 写入一律走 commit_appraisal / append_expression / settle_turn，
  由它们统一做世界校验、版本校验与租约校验。
"""

import asyncio

from langgraph.runtime import Runtime

from app.agent.person.appraisal import appraise
from app.agent.person.dialogue.context import dialogue_messages
from app.agent.person.retrieval.prompts import RETRIEVAL_DECISION_PROMPT
from app.agent.person.retrieval.tools import memory_tool
from app.agent.person.state import DialogueContext, DialogueState
from app.agent.tools.local import local_tools
from app.core.errors import ChatError
from app.core.serialization import data
from app.integrations.database.message_repository import append_expression, settle_turn
from app.integrations.database.person_store import saved_appraisal, snapshot
from app.integrations.database.state_commit import commit_appraisal
from app.llm.structured import structured, text_content
from app.schemas.person import RetrievalDecision
from app.services.model_config_service import configured_slots


async def prepare_node(state, runtime: Runtime[DialogueContext]):
    """载入本轮权威快照：资料、情绪、关系、目标和近两天的实际观察。

    世界（角色）已被重置或切换时立即失败，避免把本轮结果写进新角色。
    """
    ctx = runtime.context
    snap = await asyncio.to_thread(snapshot, ctx.user_id)
    if not snap or snap["world"]["id"] != ctx.turn["world"]["id"]:
        raise ChatError("WORLD_CHANGED", "角色已变化", 409)
    slots = await asyncio.to_thread(configured_slots)
    snap["capabilities"] = sorted(slots)
    return {"snapshot": snap}


async def decide_node(state, runtime: Runtime[DialogueContext]):
    """只决定"缺不缺信息、要不要用工具"，不产生聊天正文。

    使用固定服务端 retrieval 槽位，用户切换对话模型不会改变检索判断。
    """
    ctx = runtime.context
    decision = await structured(
        ctx.models["retrieval"],
        RetrievalDecision,
        RETRIEVAL_DECISION_PROMPT,
        {
            "profile": state["snapshot"]["profile"],
            "emotion": state["snapshot"]["emotion"],
            # 只看最近 8 条：更早的内容由长期记忆负责，不在这里重复塞入。
            "history": ctx.turn["history"][-8:],
            "available_local_tools": list(local_tools()),
        },
    )
    return {"decision": decision.model_dump()}


async def retrieval_node(state, runtime: Runtime[DialogueContext]):
    """执行 decide 选中的工具，并把结果变成可供评价使用的上下文。

    慢检索按文档 3.4 处理：1.5 秒内没有结果就先发一句已批准的等待短句
    （先入库再发送，"说过的"因此可追溯）；整体 20 秒超时则退化为无记忆继续判断，
    并在结果里标记 retrieval_timeout，而不是伪装成"想不起来"。
    """
    ctx, decision = runtime.context, state["decision"]
    world = ctx.turn["world"]
    local_result = None
    if decision.get("local_tool", "none") != "none":
        tool = local_tools().get(decision["local_tool"])
        if tool is None:
            raise ChatError("LOCAL_TOOL_DISABLED", "请求的电脑工具未开放")
        local_result = await tool.ainvoke(
            {} if decision["local_tool"] == "clock" else {"path": decision["path"]}
        )
    if not decision["needed"]:
        return {"retrieval": {"local_tool_result": local_result, "memories": []}}
    # 检索与"等待短句"并行：短句只是过渡，不能提前声称想起了尚未核实的细节。
    task = asyncio.create_task(
        memory_tool(ctx.user_id, world["id"], world["primary_actor_id"]).ainvoke(
            {"query": decision["query"] or ctx.turn["user_message"]["content"]}
        )
    )
    try:
        done, _ = await asyncio.wait({task}, timeout=1.5)
        if not done and decision["waiting_text"]:
            text = decision["waiting_text"]
            sent = await asyncio.to_thread(
                append_expression,
                ctx.user_id,
                ctx.turn,
                text,
                "waiting:" + ctx.turn["task_id"],
            )
            ctx.turn["history"].append(sent)
            await ctx.emit(sent)
        result = await asyncio.wait_for(task, timeout=20)
        result["local_tool_result"] = local_result
        return {"retrieval": result}
    except TimeoutError:
        return {"retrieval": {"error": "retrieval_timeout", "memories": []}}
    finally:
        # 无论成功、超时还是本轮被中断，都不留后台协程继续跑。
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def appraisal_node(state, runtime: Runtime[DialogueContext]):
    """形成感受与行为倾向，并通过角色服务提交（版本 CAS + 幂等键）。

    三个容易忽略的细节：
    1. 同一轮重试时 change_key 不变，commit_appraisal 返回首次结果，不再重复施加影响；
    2. 若库里的状态版本仍等于上次提交的版本，可以直接复用上次评价；
    3. 修订轮把信任、亲近与目标变化清零，只重新决定"这次怎么表达"。
    """
    ctx = runtime.context
    applied = bool(state.get("revision_attempts"))
    for _ in range(3):
        snap = await asyncio.to_thread(snapshot, ctx.user_id)
        snap["capabilities"] = state["snapshot"].get("capabilities", [])
        revising = applied
        key = "appraisal:" + ctx.turn["user_message"]["id"]
        if not revising:
            previous = await asyncio.to_thread(
                saved_appraisal, ctx.user_id, snap["world"]["id"], key
            )
            if previous:
                if previous["version"] == snap["world"]["state_version"]:
                    return {"appraisal": previous["appraisal"], "snapshot": snap}
                revising = True
        proposal = await appraise(
            ctx.model,
            snap,
            {
                "type": "conversation",
                "history": ctx.turn["history"][-12:],
                "already_appraised": revising,
            },
            state.get("retrieval"),
        )
        if revising:
            proposal.trust_delta = 0
            proposal.closeness_delta = 0
            proposal.goal_changes = []
            proposal.new_goals = []
            proposal.activity_plan = None
            key += ":revision:" + str(snap["world"]["state_version"])
        try:
            version = await asyncio.to_thread(
                commit_appraisal, ctx.user_id, snap, proposal, key, turn=ctx.turn
            )
            accepted = await asyncio.to_thread(snapshot, ctx.user_id)
            if accepted and accepted["world"]["state_version"] == version:
                return {"appraisal": proposal.model_dump(), "snapshot": accepted}
            applied = True
        except ChatError as exc:
            # 版本冲突说明生活事件或另一轮对话先提交了：重读快照重新评价，不覆盖别人。
            if exc.code != "STATE_CHANGED":
                raise
    raise ChatError("STATE_CHANGED", "人物正在处理新的事情，请稍后再聊", 409)


async def dialogue_node(state: DialogueState, runtime: Runtime[DialogueContext]):
    """把已批准的表达倾向写成自然语言，并在发送前再做一次有效性检查。

    - decision 不是 reply 时只保存"选择"（沉默/延后/离开），不强行生成回复；
    - 生成后状态版本变过，说明人物在"想怎么说"期间经历了别的事：回到评价重来；
    - 分条表达时每段都是独立 message：先入库、再 emit，取消时不会"说过却没记录"。
    """
    ctx = runtime.context
    decision = state.get("appraisal", {}).get("decision", "reply")
    if decision != "reply":
        await asyncio.to_thread(settle_turn, ctx.user_id, ctx.turn, decision)
        return {"outcome": decision}
    messages = dialogue_messages(
        ctx.turn, state["snapshot"], state.get("retrieval"), state.get("appraisal")
    )
    reply = await ctx.model.ainvoke(messages)
    latest = await asyncio.to_thread(snapshot, ctx.user_id)
    if not latest or latest["world"]["id"] != ctx.turn["world"]["id"]:
        raise ChatError("WORLD_CHANGED", "人物已变化", 409)
    if latest["world"]["state_version"] != state["snapshot"]["world"]["state_version"]:
        if state.get("revision_attempts", 0) >= 2:
            raise ChatError("RUN_INTERRUPTED", "人物正在处理新的事情", 409)
        return {
            "outcome": "reappraise",
            "revision_attempts": state.get("revision_attempts", 0) + 1,
        }
    content = text_content(reply).strip()
    if not content:
        raise ChatError("EMPTY_MODEL_REPLY", "模型未返回可显示的内容", 502)
    # 模型用空行分段；最多 4 条，超出的合并到最后一条，避免刷屏式回复。
    segments = [x.strip() for x in content.split("\n\n") if x.strip()]
    if len(segments) > 4:
        segments = segments[:3] + ["\n\n".join(segments[3:])]
    extra = None
    # 媒体适配放在用到时再导入：它依赖 httpx 与多模态客户端，不属于对话主链启动成本。
    from app.services.media_service import generate_image, synthesize

    medium = state.get("appraisal", {}).get("medium", "text")
    # medium 是评价阶段按能力选定的表达通道；缺 tts/image 能力时评价不应选 audio/image。
    if medium == "audio":
        voice = data(state["snapshot"]["world"]["voice_profile_json"]).get(
            "voice", "alloy"
        )
        asset = await synthesize(
            ctx.user_id, content, voice, state["appraisal"].get("tendency", "")
        )
        extra = [{"type": "audio", "assetId": asset["id"]}]
        segments = [content]
    elif medium == "image":
        prompt = state["appraisal"].get("media_prompt") or content
        asset = await generate_image(ctx.user_id, prompt)
        extra = [{"type": "image", "assetId": asset["id"]}]
    for index, text in enumerate(segments):
        # 先提交真实发言，再发公开事件。取消时没有未持久化的“已说过”。
        key = (
            "reply:" + ctx.turn["user_message"]["id"]
            if index == 0
            else f"reply:{ctx.turn['user_message']['id']}:{index}"
        )
        # 传入评价时的 state_version：若发言前状态又被改过，这里会拒绝发送并触发重评。
        sent = await asyncio.to_thread(
            append_expression,
            ctx.user_id,
            ctx.turn,
            text,
            key,
            extra if index == 0 else None,
            state["snapshot"]["world"]["state_version"],
        )
        await ctx.emit(sent)
        if index + 1 < len(segments):
            await asyncio.sleep(0.7)
    await asyncio.to_thread(settle_turn, ctx.user_id, ctx.turn)
    return {"outcome": "replied"}
