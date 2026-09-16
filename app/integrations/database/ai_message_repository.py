from typing import Any

from app.integrations.database.mysql import mysql_service


def insert_ai_message(
        *,
        conversation_id: str,
        user_id: str,
        role: str,
        message_type: int,
        content: str,
        image_url: str | None = None,
        personality_id: str | None = None,
        config_id: int | None = None,
) -> int:
    sql = """
          INSERT INTO ai_message (conversation_id,
                                  user_id,
                                  role,
                                  message_type,
                                  content,
                                  image_url,
                                  personality_id,
                                  config_id)
          VALUES (%s, %s, %s, %s, %s, %s, %s, %s) \
          """
    return mysql_service.insert(
        sql,
        (
            conversation_id,
            user_id,
            role,
            message_type,
            content,
            image_url,
            personality_id,
            config_id,
        ),
    )


def select_ai_messages(
        *,
        user_id: str,
        conversation_id: str | None = None,
        limit: int = 500,
) -> list[dict[str, Any]]:
    columns = """
          SELECT CAST(id AS CHAR) AS id,
                 conversation_id,
                 CAST(user_id AS CHAR) AS user_id,
                 role,
                 message_type,
                 content,
                 image_url,
                 CAST(personality_id AS CHAR) AS personality_id,
                 config_id,
                 send_time AS created_at
          FROM ai_message
    """
    if conversation_id is None:
        sql = columns + """
            WHERE user_id = %s
            ORDER BY send_time DESC, id DESC
            LIMIT %s
        """
        rows = mysql_service.select(sql, (user_id, limit))
    else:
        sql = columns + """
            WHERE user_id = %s
              AND conversation_id = %s
            ORDER BY send_time DESC, id DESC
            LIMIT %s
        """
        rows = mysql_service.select(sql, (user_id, conversation_id, limit))

    rows.reverse()
    return rows


def update_ai_message(
        *,
        message_id: int,
        user_id: str,
        content: str,
) -> int:
    sql = """
          UPDATE ai_message
          SET content = %s
          WHERE id = %s
            AND user_id = %s \
          """
    return mysql_service.update(sql, (content, message_id, user_id))


def delete_ai_message(*, message_id: int, user_id: str) -> int:
    sql = """
          DELETE
          FROM ai_message
          WHERE id = %s
            AND user_id = %s \
          """
    return mysql_service.delete(sql, (message_id, user_id))
