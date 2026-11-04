import json
from typing import Any
from uuid import uuid4

from app.integrations.database.mysql import mysql_service
from app.integrations.database.user_space_repository import (
    user_transaction as _transaction,
)


class ModelConfigInUseError(ValueError):
    pass


class ModelConfigUnavailableError(ValueError):
    pass


def insert_model_config(
    *,
    user_id: str,
    config_name: str,
    provider_name: str,
    model_name: str,
    base_url: str | None,
    model_temperature: float = 1.0,
) -> str:
    config_id = str(uuid4())
    with _transaction(user_id) as cursor:
        cursor.execute(
            """
            INSERT INTO imagent_model_config
                (id, owner_user_id, name, provider, model_name, base_url,
                 parameters_json, capabilities_json)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                config_id,
                user_id,
                config_name,
                provider_name,
                model_name,
                base_url,
                json.dumps(
                    {"schemaVersion": 1, "data": {"temperature": model_temperature}}
                ),
                json.dumps({"schemaVersion": 1, "source": "unverified"}),
            ),
        )
    return config_id


_MODEL_COLUMNS = """
    SELECT c.id, %s AS user_id, c.name AS config_name,
           c.provider AS provider_name, c.model_name, c.base_url,
           COALESCE(JSON_EXTRACT(c.parameters_json, '$.data.temperature') + 0, 1) AS model_temperature,
           (b.config_id IS NOT NULL) AS is_active,
           (c.owner_user_id IS NULL) AS is_public,
           c.credential_ref, c.created_at, c.updated_at
    FROM imagent_model_config c
    LEFT JOIN imagent_model_binding b
      ON b.config_id = c.id AND b.scope_key = %s AND b.slot = 'dialogue'
"""


def select_model_configs(*, user_id: str) -> list[dict[str, Any]]:
    return mysql_service.select(
        _MODEL_COLUMNS
        + """
        WHERE (c.owner_user_id = %s OR c.owner_user_id IS NULL)
          AND c.status = 'enabled'
        ORDER BY is_active DESC, c.created_at DESC, c.id
        """,
        (user_id, "user:" + user_id, user_id),
    )


def select_model_config_by_id(
    *, config_id: str, user_id: str | None
) -> dict[str, Any] | None:
    rows = mysql_service.select(
        _MODEL_COLUMNS
        + """
        WHERE c.id = %s AND (c.owner_user_id = %s OR c.owner_user_id IS NULL)
          AND c.status = 'enabled'
        """,
        (user_id, "user:" + user_id if user_id else "system", config_id, user_id),
    )
    return rows[0] if rows else None


def select_model_binding(*, scope_key: str, slot: str) -> str | None:
    """只选择绑定，不过滤停用模型；失效配置应报错而不是悄悄降级。"""
    rows = mysql_service.select(
        "SELECT config_id FROM imagent_model_binding "
        "WHERE slot=%s AND scope_key=%s LIMIT 1",
        (slot, scope_key),
    )
    return rows[0]["config_id"] if rows else None


def switch_model_config(*, config_id: str | None, user_id: str) -> None:
    with _transaction(user_id) as cursor:
        if config_id is None:
            cursor.execute(
                "DELETE FROM imagent_model_binding WHERE scope_key = %s AND slot = 'dialogue'",
                ("user:" + user_id,),
            )
            return
        cursor.execute(
            "SELECT id FROM imagent_model_config "
            "WHERE id = %s AND (owner_user_id = %s OR owner_user_id IS NULL) "
            "AND status = 'enabled' FOR UPDATE",
            (config_id, user_id),
        )
        if cursor.fetchone() is None:
            raise ModelConfigUnavailableError("模型配置不存在或已停用")
        cursor.execute(
            """
            INSERT INTO imagent_model_binding
                (scope_key, owner_user_id, slot, config_id, version)
            VALUES (%s, %s, 'dialogue', %s, 1)
            ON DUPLICATE KEY UPDATE config_id = %s, version = version + 1
            """,
            ("user:" + user_id, user_id, config_id, config_id),
        )


def update_model_config(
    *,
    config_id: str,
    user_id: str,
    config_name: str,
    provider_name: str,
    model_name: str,
    base_url: str | None,
    model_temperature: float,
) -> int:
    with _transaction(user_id) as cursor:
        return cursor.execute(
            """
            UPDATE imagent_model_config
            SET name = %s, provider = %s, model_name = %s, base_url = %s,
                parameters_json = JSON_SET(parameters_json, '$.data.temperature', %s),
                version = version + 1
            WHERE id = %s AND owner_user_id = %s
            """,
            (
                config_name,
                provider_name,
                model_name,
                base_url,
                model_temperature,
                config_id,
                user_id,
            ),
        )


def delete_model_config(*, config_id: str, user_id: str) -> int:
    with _transaction(user_id) as cursor:
        cursor.execute(
            "SELECT id FROM imagent_model_config WHERE id = %s AND owner_user_id = %s FOR UPDATE",
            (config_id, user_id),
        )
        if cursor.fetchone() is None:
            return 0
        # 旧版允许个人绑定辅助/媒体模型；这些绑定已不参与运行，也不再阻止删除。
        cursor.execute(
            "DELETE FROM imagent_model_binding WHERE scope_key=%s AND config_id=%s AND slot<>'dialogue'",
            ("user:" + user_id, config_id),
        )
        cursor.execute(
            "SELECT scope_key FROM imagent_model_binding WHERE config_id = %s LIMIT 1",
            (config_id,),
        )
        if cursor.fetchone() is not None:
            raise ModelConfigInUseError("此配置仍被使用，请先切换或解除绑定后再删除")
        return cursor.execute(
            "DELETE FROM imagent_model_config WHERE id = %s AND owner_user_id = %s",
            (config_id, user_id),
        )
