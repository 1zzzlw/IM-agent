"""运行服务的电脑工具；文件访问仅在显式配置的目录内进行。"""

import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from langchain_core.tools import tool

from app.core.errors import ChatError


def local_tools():
    @tool
    def clock() -> str:
        """读取服务端当前时间（上海时区）。"""
        return datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()

    def resolve(relative):
        configured = os.environ.get("IMAGENT_LOCAL_FILES_ROOT")
        if not configured:
            raise ChatError("LOCAL_TOOL_DISABLED", "尚未配置服务端文件工具目录")
        root = Path(configured).resolve(strict=True)
        path = (root / relative).resolve(strict=True)
        if not path.is_relative_to(root):
            raise ChatError("LOCAL_TOOL_DENIED", "文件不在授权目录内", 403)
        return path

    @tool
    def list_files(path: str = "") -> list[str]:
        """列出授权目录内文件，path 是相对路径。"""
        directory = resolve(path)
        if not directory.is_dir():
            raise ChatError("NOT_DIRECTORY", "指定位置不是目录")
        return [
            p.name + ("/" if p.is_dir() else "")
            for p in sorted(directory.iterdir())[:100]
        ]

    @tool
    def read_text(path: str) -> str:
        """读取授权目录内的 UTF-8 文本，最多 32 KB。"""
        file = resolve(path)
        if (
            not file.is_file()
            or file.stat().st_size > 32768
            or file.suffix.lower() not in (".txt", ".md", ".json", ".csv")
        ):
            raise ChatError("LOCAL_FILE_UNSUPPORTED", "只支持 32 KB 内的普通文本文件")
        return file.read_text(encoding="utf-8")

    result = {"clock": clock}
    if os.environ.get("IMAGENT_LOCAL_FILES_ROOT"):
        result.update(list_files=list_files, read_text=read_text)
    return result
