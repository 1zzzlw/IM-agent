from typing import Any

from app.integrations.database.mysql import mysql_service


def insert_model_config(
    *,
    user_id: str,
    config_name: str,
    provider_name: str,
    model_name: str,
    base_url: str | None,
    model_temperature: float = 1.0,
    is_active: bool = False,
) -> int:
    sql = """
          INSERT INTO ai_model_config (user_id,
                                       config_name,
                                       provider_name,
                                       model_name,
                                       base_url,
                                       model_temperature,
                                       is_active)
          VALUES (%s, %s, %s, %s, %s, %s, %s) \
          """
    return mysql_service.insert(
        sql,
        (
            user_id,
            config_name,
            provider_name,
            model_name,
            base_url,
            model_temperature,
            is_active,
        ),
    )


def select_model_configs(*, user_id: str) -> list[dict[str, Any]]:
    sql = """
        SELECT
            id,
            CAST(user_id AS CHAR) AS user_id,
            config_name,
            provider_name,
            model_name,
            base_url,
            model_temperature,
            is_active,
            created_at,
            updated_at
        FROM ai_model_config
        WHERE user_id = %s
        ORDER BY is_active DESC, created_at DESC
    """
    return mysql_service.select(sql, (user_id,))


def select_model_config_by_id(
    *,
    config_id: int,
    user_id: str,
) -> dict[str, Any] | None:
    sql = """
        SELECT
            id,
            CAST(user_id AS CHAR) AS user_id,
            config_name,
            provider_name,
            model_name,
            base_url,
            model_temperature,
            is_active,
            created_at,
            updated_at
        FROM ai_model_config
        WHERE id = %s
          AND user_id = %s
        LIMIT 1
    """
    rows = mysql_service.select(sql, (config_id, user_id))
    return rows[0] if rows else None


def switch_model_config(*, config_id: int | None, user_id: str) -> int:
    if config_id is None:
        sql = """
            UPDATE ai_model_config
            SET is_active = 0
            WHERE user_id = %s
        """
        return mysql_service.update(sql, (user_id,))

    sql = """
        UPDATE ai_model_config
        SET is_active = CASE WHEN id = %s THEN 1 ELSE 0 END
        WHERE user_id = %s
    """
    return mysql_service.update(sql, (config_id, user_id))


def update_model_config(
    *,
    config_id: int,
    user_id: str,
    config_name: str,
    provider_name: str,
    model_name: str,
    base_url: str | None,
    model_temperature: float,
    is_active: bool,
) -> int:
    sql = """
        UPDATE ai_model_config
        SET
            config_name = %s,
            provider_name = %s,
            model_name = %s,
            base_url = %s,
            model_temperature = %s,
            is_active = %s
        WHERE id = %s
          AND user_id = %s
    """
    return mysql_service.update(
        sql,
        (
            config_name,
            provider_name,
            model_name,
            base_url,
            model_temperature,
            is_active,
            config_id,
            user_id,
        ),
    )


def delete_model_config(*, config_id: int, user_id: str) -> int:
    sql = """
        DELETE FROM ai_model_config
        WHERE id = %s
          AND user_id = %s
    """
    return mysql_service.delete(sql, (config_id, user_id))
