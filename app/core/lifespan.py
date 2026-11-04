import asyncio
from contextlib import asynccontextmanager

from loguru import logger

from app.core.config import config
from app.embedding.service import embedding_service
from app.integrations.database.mysql import mysql_service
from app.integrations.nacos import nacos_service
from app.integrations.redis.client import redis_client
from app.integrations.vector_store.client import chroma_client
from app.services.background_service import background_service


@asynccontextmanager
async def lifespan(_app):
    registered = False
    try:
        await asyncio.to_thread(mysql_service.initialize)
        if config.redis.enabled:
            await asyncio.to_thread(redis_client.initialize)
        if config.embedding.name and config.embedding.base_url:
            try:
                await asyncio.to_thread(chroma_client.initialize)
                embedding_service.initialize()
            except Exception as exc:
                logger.warning(
                    "语义索引不可用，使用关键词召回 error_type={}", type(exc).__name__
                )
        if config.nacos.enabled:
            await nacos_service.register()
            registered = True
        await background_service.start()
        logger.info("人物服务与后台生活已启动")
        yield
    finally:
        await background_service.close()
        if registered:
            await nacos_service.deregister()
        chroma_client.close()
        embedding_service.model = None
        await asyncio.to_thread(redis_client.close)
        await asyncio.to_thread(mysql_service.close)
