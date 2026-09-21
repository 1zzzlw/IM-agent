import pymysql
from typing import Any

from dbutils.pooled_db import PooledDB
from pymysql.cursors import DictCursor

from app.core.config import config


class MysqlService:
    def __init__(self) -> None:
        self._pool: PooledDB | None = None

    def initialize(self) -> None:
        if self._pool is not None:
            return

        pool = PooledDB(
            creator=pymysql,
            mincached=config.database.min_cached,
            maxcached=config.database.max_cached,
            maxconnections=config.database.max_connections,
            blocking=config.database.blocking,
            ping=1,
            host=config.database.host,
            port=config.database.port,
            user=config.database.user,
            password=config.database.password,
            database=config.database.name,
            charset=config.database.charset,
            cursorclass=DictCursor,
            autocommit=False,
            connect_timeout=config.database.connect_timeout,
        )
        try:
            connection = pool.connection()
            try:
                connection.ping(reconnect=False)
            finally:
                connection.close()
        except Exception:
            pool.close()
            raise

        self._pool = pool

    def connection(self) -> Any:
        if self._pool is None:
            raise RuntimeError("MySQL 连接池尚未初始化")
        return self._pool.connection()

    def close(self) -> None:
        pool = self._pool
        self._pool = None
        if pool is not None:
            pool.close()

    def select(
            self,
            sql: str,
            params: tuple[Any, ...] = (),
    ) -> list[dict[str, Any]]:
        connection = self.connection()
        try:
            with connection.cursor() as cursor:
                cursor.execute(sql, params)
                return list(cursor.fetchall())
        finally:
            connection.close()

    def insert(self, sql: str, params: tuple[Any, ...]) -> int:
        connection = self.connection()
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

    def _execute_write(self, sql: str, params: tuple[Any, ...]) -> int:
        connection = self.connection()
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


def get_mysql_connection() -> Any:
    return mysql_service.connection()
