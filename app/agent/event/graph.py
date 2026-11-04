"""随机事件 Agent 的三阶段工作流：事情产生 → 事件经历 → 最终影响。

``generate`` 只提出"此刻可能发生什么"，允许返回 continue 表示没有值得发生的新事
（平淡是合法结果，不强制每个时间窗口制造剧情）。真正成立的事件会走到
``experience``，按参与者各自的实际观察生成对话；最后 ``impact`` 形成感受、
主动消息、朋友圈与目标变化。开始与结束两次运行共用这张图，靠 ``finishing`` 区分。
"""

from langgraph.graph import END, START, StateGraph

from app.agent.event.nodes import experience, generate, impact
from app.agent.event.state import EventContext, EventState


def build_event_graph():
    graph = StateGraph(EventState, context_schema=EventContext)
    graph.add_node("generate", generate)
    graph.add_node("experience", experience)
    graph.add_node("impact", impact)
    # 结束阶段（event_end 任务）跳过着事情产生，直接续写已有事件的经过。
    graph.add_conditional_edges(
        START, lambda s: "experience" if s.get("finishing") else "generate"
    )
    graph.add_conditional_edges(
        "generate",
        lambda s: END if s["proposal"]["action"] == "continue" else "experience",
    )
    graph.add_edge("experience", "impact")
    graph.add_edge("impact", END)
    return graph.compile()


event_graph = build_event_graph()
