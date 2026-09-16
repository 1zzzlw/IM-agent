import pymysql
from typing import Any

from pymysql.connections import Connection
from pymysql.cursors import DictCursor

from config import config


def get_mysql_connection() -> Connection:
    return pymysql.connect(
        host=config.database.host,
        port=config.database.port,
        user=config.database.user,
        password=config.database.password,
        database=config.database.name,
        charset=config.database.charset,
        cursorclass=DictCursor,
        autocommit=False,
        connect_timeout=5,
    )


class MysqlService:
    def select(
            self,
            sql: str,
            params: tuple[Any, ...] = (),
    ) -> list[dict[str, Any]]:
        connection = get_mysql_connection()
        try:
            with connection.cursor() as cursor:
                cursor.execute(sql, params)
                return list(cursor.fetchall())
        finally:
            connection.close()

    def insert(self, sql: str, params: tuple[Any, ...]) -> int:
        connection = get_mysql_connection()
        try:
            with connection.cursor() as cursor:
                cursor.execute(sql, params)
                row_id = cursor.lastrowid
            connection.commit()
            return int(row_id)
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def update(self, sql: str, params: tuple[Any, ...]) -> int:
        return self._execute_write(sql, params)

    def delete(self, sql: str, params: tuple[Any, ...]) -> int:
        return self._execute_write(sql, params)

    @staticmethod
    def _execute_write(sql: str, params: tuple[Any, ...]) -> int:
        connection = get_mysql_connection()
        try:
            with connection.cursor() as cursor:
                affected_rows = cursor.execute(sql, params)
            connection.commit()
            return affected_rows
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()


mysql_service = MysqlService()
