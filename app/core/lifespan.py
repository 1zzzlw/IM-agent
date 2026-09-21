import asyncio
from loguru import logger
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.integrations.database.mysql import mysql_service
from app.integrations.redis.client import redis_client
from app.integrations.nacos import nacos_service
from app.integrations.vector_store.client import chroma_client
from app.integrations.vector_store.project_code_store import project_code_store

from app.embedding.service import embedding_service


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    # 初始化数据库连接
    await connection_mysql()
    # 初始化Redis的连接
    await connection_redis()
    init_chroma_service()
    init_embedding_service()
    try:
        # 注册服务到Nacos
        await nacos_service.register()
    except Exception:
        await asyncio.to_thread(mysql_service.close)
        raise

    logger.info("项目启动成功")
    try:
        yield
    finally:
        try:
            await nacos_service.deregister()
            project_code_store.close()
            chroma_client.close()
            embedding_service.close()
        finally:
            await asyncio.to_thread(mysql_service.close)
            await asyncio.to_thread(redis_client.close)
            logger.info("项目关闭成功")


async def connection_mysql():
    try:
        await asyncio.to_thread(mysql_service.initialize)
        logger.info("数据库连接成功")
    except Exception:
        raise Exception("数据库连接失败")


async def connection_redis():
    try:
        await asyncio.to_thread(redis_client.initialize)
        logger.info("redis连接成功")
    except Exception:
        raise Exception("redis连接失败")


def init_embedding_service():
    try:
        embedding_service.initialize()
        logger.info("embedding服务初始化成功")
    except Exception:
        raise Exception("embedding服务初始化失败")


def init_chroma_service():
    try:
        chroma_client.initialize()
        project_code_store.initialize()
        logger.info("chroma服务初始化成功")
    except Exception:
        raise Exception("chroma服务初始化失败")
