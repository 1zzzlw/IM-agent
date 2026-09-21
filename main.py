from fastapi import FastAPI

from app.api.ai_message import router as chat_router
from app.api.conversation import router as conversation_router
from app.api.model_config import router as config_router
from app.api.workspace import router as workspace_router
from app.core.lifespan import lifespan


app = FastAPI(
    title="imAgent",
    version="0.1.0",
    description="一个简单的 Agent 项目",
    lifespan=lifespan,
)

app.include_router(chat_router)
app.include_router(conversation_router)
app.include_router(config_router)
app.include_router(workspace_router)

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        port=9090,
        log_level="info",
        reload=True
    )
