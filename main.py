from pathlib import Path

from dotenv import load_dotenv

# 在导入模型和 tracing 模块前加载配置，兼容 IDE、python main.py 和 uvicorn 启动。
load_dotenv(Path(__file__).resolve().with_name(".env"), override=False)

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.api.admin import router as admin_router
from app.api.ai_message import router as chat_router
from app.api.call import router as call_router
from app.api.media import router as media_router
from app.api.model_config import router as config_router
from app.api.person import router as person_router
from app.core.errors import ChatError
from app.core.lifespan import lifespan

app = FastAPI(
    title="Lumina-Agent · 微光",
    version="0.1.0",
    description="个性化人物、长期记忆与持续生活服务",
    lifespan=lifespan,
)

app.include_router(chat_router)
app.include_router(config_router)
app.include_router(person_router)
app.include_router(media_router)
app.include_router(call_router)
app.include_router(admin_router)


@app.exception_handler(ChatError)
async def chat_error_handler(_request, exc):
    return JSONResponse(
        status_code=exc.status,
        content={"detail": {"code": exc.code, "message": exc.message}},
    )


if __name__ == "__main__":
    import uvicorn

    from app.core.config import config

    uvicorn.run(
        "main:app",
        host=config.app.host,
        port=config.app.port,
        log_level="info",
        reload=True,
    )
