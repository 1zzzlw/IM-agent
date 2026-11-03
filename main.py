from pathlib import Path

from dotenv import load_dotenv

# 在导入模型和 tracing 模块前加载配置，兼容 IDE、python main.py 和 uvicorn 启动。
load_dotenv(Path(__file__).resolve().with_name(".env"), override=False)

from fastapi import FastAPI

from app.api.ai_message import router as chat_router
from app.api.model_config import router as config_router
from app.core.lifespan import lifespan

app = FastAPI(
    title="Lumina-Agent · 微光",
    version="0.1.0",
    description="个性化虚拟人物 Agent 服务，已接通基础文字对话，人物核心机制逐步实现",
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
