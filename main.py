from fastapi import FastAPI
from contextlib import asynccontextmanager

from app.api.ai_message import router as chat_router
from app.api.ai_config import router as config_router
from app.services.nacos_service import nacos_service


@asynccontextmanager
async def lifespan(app: FastAPI):
    await nacos_service.register()
    print("项目启动成功")
    try:
        yield
    finally:
        await nacos_service.deregister()
        print("项目关闭成功")


app = FastAPI(
    title="imAgent",
    version="0.1.0",
    description="一个简单的 Agent 项目",
    lifespan=lifespan,
)

app.include_router(chat_router)
app.include_router(config_router)

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        port=9090,
        log_level="info",
        reload=True
    )
