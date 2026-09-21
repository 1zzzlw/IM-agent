from uuid import uuid4
from langchain_core.tools import BaseTool, tool
from langgraph.config import get_stream_writer

from app.services.tool_dispatcher import tool_request_dispatcher
from app.services.workspace_service import WorkspaceContext, workspace_service

MAX_WRITE_CHARS = 2_000_000


def _tool_content(success: bool, content: str | None, error: str | None) -> str:
    if not success:
        return f"本地文件工具执行失败：{error or '未知错误'}"
    return content or "操作已完成"


def build_workspace_tools(workspace: WorkspaceContext) -> list[BaseTool]:
    @tool
    def list_workspace_files(query: str = "", limit: int = 200) -> str:
        """列出工作区文件。query 可按相对路径关键字过滤，limit 范围为 1 到 500。"""
        normalized_query = query.strip().casefold()
        safe_limit = max(1, min(limit, 500))
        relative_paths = sorted(
            path
            for path in workspace_service.list_files(workspace)
            if not normalized_query or normalized_query in path.casefold()
        )
        visible_paths = relative_paths[:safe_limit]
        suffix = ""
        if len(relative_paths) > safe_limit:
            suffix = f"\n……另有 {len(relative_paths) - safe_limit} 个匹配文件未展示"
        content = "\n".join(visible_paths) + suffix if visible_paths else "没有匹配的文件"
        get_stream_writer()({
            "type": "tool.completed",
            "toolCallId": f"tool-{uuid4()}",
            "workspaceName": workspace.name,
            "toolName": "list_workspace_files",
            "summary": normalized_query or "全部文件",
        })
        return content

    @tool
    async def read_workspace_file(
            path: str,
            start_line: int = 1,
            end_line: int = 400,
    ) -> str:
        """请求用户客户端读取工作区文本文件。path 必须是相对路径，可指定起止行。"""
        normalized_path = workspace_service.normalize_relative_path(path)
        safe_start = max(1, start_line)
        result = await tool_request_dispatcher.request(
            user_id=workspace.user_id,
            workspace_name=workspace.name,
            operation="read",
            arguments={
                "path": normalized_path,
                "startLine": safe_start,
                "endLine": max(safe_start, min(end_line, safe_start + 999)),
            },
            stream_writer=get_stream_writer(),
        )
        return _tool_content(result.success, result.content, result.error)

    @tool
    async def write_workspace_file(path: str, content: str) -> str:
        """请求用户客户端覆盖写入工作区文件。path 必须是相对路径。"""
        if len(content) > MAX_WRITE_CHARS:
            raise ValueError("单次写入内容不能超过 200 万字符")

        normalized_path = workspace_service.normalize_relative_path(path)
        result = await tool_request_dispatcher.request(
            user_id=workspace.user_id,
            workspace_name=workspace.name,
            operation="write",
            arguments={"path": normalized_path, "content": content},
            stream_writer=get_stream_writer(),
        )
        response = _tool_content(result.success, result.content, result.error)
        if result.success:
            workspace_service.add_file(workspace, normalized_path)
        return response

    @tool
    async def edit_workspace_file(
            path: str,
            old_text: str,
            new_text: str,
            replace_all: bool = False,
    ) -> str:
        """精确替换工作区文件中的文本。默认要求 old_text 只出现一次。"""
        if not old_text:
            raise ValueError("old_text 不能为空")
        if len(old_text) > MAX_WRITE_CHARS or len(new_text) > MAX_WRITE_CHARS:
            raise ValueError("单次修改文本不能超过 200 万字符")

        normalized_path = workspace_service.normalize_relative_path(path)
        result = await tool_request_dispatcher.request(
            user_id=workspace.user_id,
            workspace_name=workspace.name,
            operation="edit",
            arguments={
                "path": normalized_path,
                "oldText": old_text,
                "newText": new_text,
                "replaceAll": replace_all,
            },
            stream_writer=get_stream_writer(),
        )
        return _tool_content(result.success, result.content, result.error)

    @tool
    async def delete_workspace_file(path: str) -> str:
        """请求用户客户端删除工作区文件。仅在用户明确要求删除时调用。"""
        normalized_path = workspace_service.normalize_relative_path(path)
        result = await tool_request_dispatcher.request(
            user_id=workspace.user_id,
            workspace_name=workspace.name,
            operation="delete",
            arguments={"path": normalized_path},
            stream_writer=get_stream_writer(),
        )
        response = _tool_content(result.success, result.content, result.error)
        if result.success:
            workspace_service.remove_file(workspace, normalized_path)
        return response

    return [
        list_workspace_files,
        read_workspace_file,
        write_workspace_file,
        edit_workspace_file,
        delete_workspace_file,
    ]
