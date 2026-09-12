from fastapi import APIRouter

router = APIRouter(prefix="/ai-config", tags=["AI 配置接口"])

@router.get("/getConfig")
def get_config():
    pass

@router.put("switchModel")
def switch_model():
    pass

@router.post("add_model")
def add_model():
    pass