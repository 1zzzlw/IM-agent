from langchain_core.tools import BaseTool, tool
from langgraph.config import get_stream_writer

from app.core.path_safety import normalize_relative_path
from app.services.tool_dispatcher import ToolCallResult, tool_request_dispatcher

MAX_WRITE_CHARS = 2_000_000


def _tool_content(success: bool, content: str | None, error: str | None) -> str:
    if not success:
        return f"本地文件工具执行失败：{error or '未知错误'}"
    return content or "操作已完成"


def _emit_completed(workspace_name: str, tool_name: str, result: ToolCallResult) -> None:
    """用与 tool.request 相同的 toolCallId 回传结果，供前端配对工具步骤。"""
    get_stream_writer()({
        "type": "tool.completed",
        "toolCallId": result.tool_call_id,
        "workspaceName": workspace_name,
        "toolName": tool_name,
        "success": result.success,
    })


def build_workspace_tools(*, user_id: str, workspace_name: str) -> list[BaseTool]:
    """工具只携带归属信息；工作区路径与文件真相全部由客户端掌握。"""

    async def _dispatch(operation: str, arguments: dict) -> ToolCallResult:
        return await tool_request_dispatcher.request(
            user_id=user_id,
            workspace_name=workspace_name,
            operation=operation,
            arguments=arguments,
            stream_writer=get_stream_writer(),
        )

    @tool
    async def search_workspace_text(
            pattern: str,
            path: str = "",
            file_glob: str = "",
            use_regex: bool = False,
            max_results: int = 200,
    ) -> str:
        """在工作区中按关键字搜索文本内容，返回 "相对路径:行号: 内容" 形式的命中行。
        pattern 是关键字，use_regex=true 时按正则处理；path 限定搜索的子目录，留空为整个工作区；
        file_glob 按扩展名过滤，例如 ".py"；max_results 上限 200。"""
        if not pattern.strip():
            raise ValueError("搜索关键字不能为空")

        result = await _dispatch(
            "search",
            {
                "pattern": pattern,
                "path": path.strip(),
                "glob": file_glob.strip(),
                "useRegex": use_regex,
                "maxResults": max(1, min(max_results, 200)),
            }
        )

        _emit_completed(workspace_name, "search_workspace_text", result)
        return _tool_content(result.success, result.content, result.error)

    @tool
    async def run_workspace_command(
            command: str,
            arguments: list[str] | None = None,
            timeout_seconds: int = 60,
    ) -> str:
        """在工作区根目录执行受控的开发命令，用来验证改动是否通过。
        只允许白名单内的命令：npm/pnpm/yarn 的 test 或 run <build|test|lint|typecheck>、
        git 的 status/diff/log/show、pytest、npx 的 tsc/eslint/vitest。
        必须拆成 command 和 arguments 两部分，不允许传入整条 shell 字符串。"""
        result = await _dispatch(
            "command",
            {
                "command": command.strip(),
                "args": [str(item) for item in (arguments or [])],
                "timeoutSeconds": max(5, min(timeout_seconds, 110)),
            },
        )
        _emit_completed(workspace_name, "run_workspace_command", result)
        return _tool_content(result.success, result.content, result.error)

    @tool
    async def list_workspace_files(path: str = "", depth: int = 1) -> str:
        """列出工作区目录内容。path 是相对目录路径，留空表示工作区根目录；
        depth 表示向下展开的层数，只支持 1 到 2。目录名以 / 结尾。"""
        result = await _dispatch(
            "list",
            {
                "path": path.strip(),
                "depth": max(1, min(depth, 2)),
            },
        )
        _emit_completed(workspace_name, "list_workspace_files", result)
        return _tool_content(result.success, result.content, result.error)

    @tool
    async def read_workspace_file(
            path: str,
            start_line: int = 1,
            end_line: int = 400,
    ) -> str:
        """读取工作区文本文件。path 必须是相对路径，可指定起止行。"""
        normalized_path = normalize_relative_path(path)
        safe_start = max(1, start_line)
        result = await _dispatch(
            "read",
            {
                "path": normalized_path,
                "startLine": safe_start,
                "endLine": max(safe_start, min(end_line, safe_start + 999)),
            },
        )
        _emit_completed(workspace_name, "read_workspace_file", result)
        return _tool_content(result.success, result.content, result.error)

    @tool
    async def write_workspace_file(path: str, content: str) -> str:
        """覆盖写入工作区文件。path 必须是相对路径。"""
        if len(content) > MAX_WRITE_CHARS:
            raise ValueError("单次写入内容不能超过 200 万字符")

        normalized_path = normalize_relative_path(path)
        result = await _dispatch(
            "write",
            {"path": normalized_path, "content": content},
        )
        _emit_completed(workspace_name, "write_workspace_file", result)
        return _tool_content(result.success, result.content, result.error)

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

        normalized_path = normalize_relative_path(path)
        result = await _dispatch(
            "edit",
            {
                "path": normalized_path,
                "oldText": old_text,
                "newText": new_text,
                "replaceAll": replace_all,
            },
        )
        _emit_completed(workspace_name, "edit_workspace_file", result)
        return _tool_content(result.success, result.content, result.error)

    @tool
    async def delete_workspace_file(path: str) -> str:
        """删除工作区文件。仅在用户明确要求删除时调用。"""
        normalized_path = normalize_relative_path(path)
        result = await _dispatch("delete", {"path": normalized_path})
        _emit_completed(workspace_name, "delete_workspace_file", result)
        return _tool_content(result.success, result.content, result.error)

    return [
        search_workspace_text,
        run_workspace_command,
        list_workspace_files,
        read_workspace_file,
        write_workspace_file,
        edit_workspace_file,
        delete_workspace_file,
    ]
