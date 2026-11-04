"""历史消息查询：只返回"当前用户在当前世界、自己视图里还能看到"的聊天。

可见性由 message_delivery.hidden_at 决定；已遗忘记忆关联的消息不再出现在
人物的对话上下文里（但用户界面仍按自己的视图显示）。
"""

from app.integrations.database.message_store import message_text, public_message
from app.integrations.database.mysql import mysql_service

VISIBLE_MESSAGES_SQL = """
           SELECT m.*
           FROM imagent_user_space s
                    JOIN imagent_world w ON w.id = s.active_world_id AND w.user_id = s.user_id
                    JOIN imagent_message m ON m.world_id = w.id
                    JOIN imagent_message_delivery d
                         ON d.world_id = m.world_id AND d.message_id = m.id AND d.viewer_actor_id = %s
           WHERE s.user_id = %s
             AND w.status = 'active'
             AND m.channel = 'chat'
             AND d.hidden_at IS NULL
             AND ((m.origin = 'user' AND m.sender_actor_id = w.user_actor_id)
               OR (m.origin = 'agent' AND m.sender_actor_id = w.primary_actor_id)) \
           """


def select_ai_messages(
    *, user_id: str, limit: int = 500, before_seq: int | None = None
):
    """按世界内 seq 倒序取一页，再翻转成正序返回。"""
    # 找到用户当前的世界，它代表用户当前 AI 所属的生活世界。用户还没有人物或当前世界时，返回空列表。
    rows = mysql_service.select(
        "SELECT active_world_id FROM imagent_user_space WHERE user_id=%s", (user_id,)
    )
    if not rows or not rows[0]["active_world_id"]:
        return []

    # 找到用户在这个世界里的身份：user_actor_id 是聊天里的“我”，不是账号 user_id。
    worlds = mysql_service.select(
        "SELECT user_actor_id FROM imagent_world WHERE id=%s AND user_id=%s",
        (rows[0]["active_world_id"], user_id),
    )
    if not worlds:
        return []

    sql, params = VISIBLE_MESSAGES_SQL, [worlds[0]["user_actor_id"], user_id]
    if before_seq is not None:
        sql += " AND m.seq < %s"
        params.append(before_seq)
    rows = mysql_service.select(sql + " ORDER BY m.seq DESC LIMIT %s", (*params, limit))
    return [
        public_message(row, user_id)
        for row in reversed(rows)
        if message_text(row["parts_json"])
    ]
