"""对话 Agent 的 LangGraph 编排：把一次交流拆成五个可独立验证的节点。

对应 docs/架构设计.md 3.3 的目标流程，顺序固定为
``prepare → decide → (retrieval) → appraisal → dialogue``：

- prepare：读取当前人物快照，并确认本轮仍属于同一个世界；
- decide：只判断"要不要补旧记忆 / 要不要用服务端电脑工具"，不产出聊天正文；
- retrieval：执行工具（真正的提取 Agent 在工具内部），慢检索时可以先发等待短句；
- appraisal：形成感受、行为选择与表达倾向，并由角色服务统一提交状态变化；
- dialogue：把已批准的表达倾向写成自然语言，逐条持久化后再对外发送。

节点数量不等于 Agent 数量：提取逻辑封装在工具里，appraisal 是三个 Agent 共用的
角色评价流程，都不是"第五个 Agent"。任何节点拿到的记忆与工具结果都只是上下文，
不能直接改写人物状态；状态只能经 commit_appraisal 提交。
"""

from langgraph.graph import END, START, StateGraph

from app.agent.person.dialogue.agent import (
    appraisal_node,
    decide_node,
    dialogue_node,
    prepare_node,
    retrieval_node,
)
from app.agent.person.state import DialogueContext, DialogueState


def build_dialogue_graph():
    graph = StateGraph(DialogueState, context_schema=DialogueContext)
    graph.add_node("prepare", prepare_node)
    graph.add_node("decide", decide_node)
    graph.add_node("retrieval", retrieval_node)
    graph.add_node("appraisal", appraisal_node)
    graph.add_node("dialogue", dialogue_node)
    graph.add_edge(START, "prepare")
    graph.add_edge("prepare", "decide")
    # 只有确实缺旧信息或要用电脑工具时才绕道 retrieval；上下文足够就直接评价。
    graph.add_conditional_edges(
        "decide",
        lambda state: (
            "retrieval"
            if state["decision"]["needed"]
            or state["decision"].get("local_tool", "none") != "none"
            else "appraisal"
        ),
    )
    graph.add_edge("retrieval", "appraisal")
    graph.add_edge("appraisal", "dialogue")
    # 表达期间人物状态被生活事件改过时，回到评价重新判断（次数由节点内的上限控制），
    # 避免用旧评价去写已经不合时宜的回复。
    graph.add_conditional_edges(
        "dialogue",
        lambda state: "appraisal" if state.get("outcome") == "reappraise" else END,
    )
    return graph.compile()


dialogue_graph = build_dialogue_graph()
