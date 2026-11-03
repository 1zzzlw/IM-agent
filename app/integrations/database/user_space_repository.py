import json
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from app.integrations.database.mysql import mysql_service


def dialogue_schema_ready() -> bool:
    """查询基础聊天所需表；只检查，不自动建表或创建角色。"""
    tables = (
        "imagent_user_space",
        "imagent_world",
        "imagent_actor",
        "imagent_actor_state",
        "imagent_message",
        "imagent_message_delivery",
        "imagent_experience",
        "imagent_observation",
        "imagent_background_task",
        "imagent_client_event",
        "imagent_model_config",
        "imagent_model_binding",
    )
    placeholders = ",".join(["%s"] * len(tables))
    rows = mysql_service.select(
        "SELECT table_name FROM information_schema.tables "
        f"WHERE table_schema=DATABASE() AND table_name IN ({placeholders})",
        tables,
    )
    return len(rows) == len(tables)


@contextmanager
def user_transaction(user_id: str) -> Iterator[Any]:
    """统一按用户空间、世界、业务行的顺序加锁。推理不持有这个事务。"""
    connection = mysql_service.connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO imagent_user_space (user_id, settings_json) VALUES (%s, %s) "
                "ON DUPLICATE KEY UPDATE user_id = user_id",
                (
                    user_id,
                    json.dumps(
                        {
                            "schemaVersion": 1,
                            "presenceVisibility": {
                                "online": True,
                                "read": True,
                                "typing": True,
                            },
                            "notifications": {"muted": False},
                        }
                    ),
                ),
            )
            cursor.execute(
                "SELECT * FROM imagent_user_space WHERE user_id = %s FOR UPDATE",
                (user_id,),
            )
            yield cursor
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()
