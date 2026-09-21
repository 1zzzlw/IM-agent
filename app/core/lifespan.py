from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.integrations.nacos import nacos_service


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    await nacos_service.register()
    print("项目启动成功")
    try:
        yield
    finally:
        await nacos_service.deregister()
        print("项目关闭成功")
