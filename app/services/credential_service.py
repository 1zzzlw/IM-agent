"""后台调用使用服务端加密凭据；数据库只保存引用，公开 DTO 不返回密钥。"""

import os
import re
from pathlib import Path
from uuid import uuid4

from cryptography.fernet import Fernet

from app.core.errors import ChatError
from app.core.paths import RESOURCES_DIR

ROOT = Path(
    os.environ.get("IMAGENT_RUNTIME_DIR", RESOURCES_DIR / "storage" / "runtime")
)


def cipher():
    ROOT.mkdir(parents=True, exist_ok=True)
    key = os.environ.get("IMAGENT_SECRET_KEY")
    if key:
        return Fernet(key.encode())
    path = ROOT / "secret.key"
    try:
        with path.open("xb") as stream:
            stream.write(Fernet.generate_key())
    except FileExistsError:
        pass
    return Fernet(path.read_bytes())


def save_credential(key):
    identity = str(uuid4())
    encrypted = cipher().encrypt(key.encode())
    (ROOT / (identity + ".key.enc")).write_bytes(encrypted)
    return "vault:" + identity


def discard_unreferenced(reference):
    """仅清理本次创建而未被任何模型引用的加密文件。"""
    identity = reference.removeprefix("vault:")
    if not reference.startswith("vault:") or not re.fullmatch(
        r"[a-f0-9-]{36}", identity
    ):
        return
    from app.integrations.database.mysql import mysql_service

    if not mysql_service.select(
        "SELECT id FROM imagent_model_config WHERE credential_ref=%s LIMIT 1",
        (reference,),
    ):
        (ROOT / (identity + ".key.enc")).unlink(missing_ok=True)


def read_credential(reference):
    if reference.startswith("env:"):
        return os.environ.get(reference[4:])
    identity = reference.removeprefix("vault:")
    if not reference.startswith("vault:") or not re.fullmatch(
        r"[a-f0-9-]{36}", identity
    ):
        return None
    try:
        return cipher().decrypt((ROOT / (identity + ".key.enc")).read_bytes()).decode()
    except Exception as exc:
        raise ChatError(
            "MODEL_CREDENTIAL_UNAVAILABLE", "服务端模型凭据不可用，请重新保存", 503
        ) from exc
