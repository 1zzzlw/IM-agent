from typing import Any

from app.integrations.database.mysql import get_mysql_connection, mysql_service


def insert_ai_conversation(
    *,
    conversation_id: str,
    user_id: str,
    title: str,
) -> int:
    sql = """
        INSERT INTO ai_conversation (id, user_id, title)
        VALUES (%s, %s, %s)
    """
    return mysql_service.update(sql, (conversation_id, user_id, title))


def select_ai_conversations(*, user_id: str) -> list[dict[str, Any]]:
    sql = """
        SELECT
            id,
            CAST(user_id AS CHAR) AS user_id,
            title,
            created_at,
            updated_at
        FROM ai_conversation
        WHERE user_id = %s
        ORDER BY updated_at DESC, created_at DESC
    """
    return mysql_service.select(sql, (user_id,))


def select_ai_conversation(
    *,
    conversation_id: str,
    user_id: str,
) -> dict[str, Any] | None:
    sql = """
        SELECT
            id,
            CAST(user_id AS CHAR) AS user_id,
            title,
            created_at,
            updated_at
        FROM ai_conversation
        WHERE id = %s
          AND user_id = %s
        LIMIT 1
    """
    rows = mysql_service.select(sql, (conversation_id, user_id))
    return rows[0] if rows else None


def touch_ai_conversation(*, conversation_id: str, user_id: str) -> int:
    sql = """
        UPDATE ai_conversation
        SET updated_at = CURRENT_TIMESTAMP
        WHERE id = %s
          AND user_id = %s
    """
    return mysql_service.update(sql, (conversation_id, user_id))


def rename_ai_conversation(
    *,
    conversation_id: str,
    user_id: str,
    title: str,
) -> int:
    sql = """
        UPDATE ai_conversation
        SET title = %s
        WHERE id = %s
          AND user_id = %s
    """
    return mysql_service.update(sql, (title, conversation_id, user_id))


def delete_ai_conversation(*, conversation_id: str, user_id: str) -> int:
    """多次删除操作时，需要手动获得连接进行选择性删除"""
    connection = get_mysql_connection()
    try:
        with connection.cursor() as cursor:
            # 该 sql 语句加上了行锁 ‘FOR UPDATE’ 如果这三条sql单独执行，那么该行锁就失效了
            cursor.execute(
                """
                SELECT id
                FROM ai_conversation
                WHERE id = %s
                  AND user_id = %s
                FOR UPDATE
                """,
                (conversation_id, user_id),
            )
            if cursor.fetchone() is None:
                connection.rollback()
                return 0

            cursor.execute(
                """
                DELETE
                FROM ai_message
                WHERE conversation_id = %s
                  AND user_id = %s
                """,
                (conversation_id, user_id),
            )
            affected_rows = cursor.execute(
                """
                DELETE FROM ai_conversation
                WHERE id = %s
                  AND user_id = %s
                """,
                (conversation_id, user_id),
            )

        connection.commit()
        return affected_rows
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
