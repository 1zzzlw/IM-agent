import json
from fastapi import HTTPException, Request


async def validate_gateway_user(request: Request):
    """兼容开发直连；经网关转发时校验 userId 与可信 user-info 一致。"""
    identity = request.headers.get("user-info")
    if identity is None:
        return
    try:
        raw_id = json.loads(identity)["id"]
        user_id = str(raw_id)
        if (
            raw_id is None
            or isinstance(raw_id, bool)
            or not user_id
            or len(user_id) > 64
        ):
            raise ValueError("invalid identity")
    except (ValueError, TypeError, KeyError):
        raise HTTPException(status_code=401, detail="网关登录信息无效")
    claimed = request.query_params.get("userId")
    if request.method in ("POST", "PUT", "PATCH"):
        body = await request.json()
        if isinstance(body, dict):
            claimed = body.get("userId", body.get("user_id", claimed))
    if claimed is not None and str(claimed) != user_id:
        raise HTTPException(status_code=403, detail="不能访问其他用户的 AI 资料")
