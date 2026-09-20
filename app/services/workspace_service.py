from dataclasses import dataclass
from pathlib import PurePosixPath, PureWindowsPath
from threading import RLock


class WorkspaceValidationError(ValueError):
    pass


class WorkspaceNotFoundError(LookupError):
    pass


@dataclass
class WorkspaceContext:
    user_id: str
    name: str
    file_paths: set[str]


class WorkspaceService:
    def __init__(self):
        self._workspaces: dict[tuple[str, str], WorkspaceContext] = {}
        self._lock = RLock()

    def register(
            self,
            *,
            user_id: str,
            workspace_name: str,
            file_paths: list[str],
    ) -> WorkspaceContext:
        normalized_user_id = user_id.strip()
        normalized_name = workspace_name.strip()
        if not normalized_user_id or not normalized_name:
            raise WorkspaceValidationError("用户 ID 和工作区名称不能为空")

        normalized_files = {
            self.normalize_relative_path(file_path)
            for file_path in file_paths
        }
        workspace = WorkspaceContext(
            user_id=normalized_user_id,
            name=normalized_name,
            file_paths=normalized_files,
        )
        with self._lock:
            self._workspaces[(workspace.user_id, workspace.name)] = workspace
        return workspace

    def get(self, *, user_id: str, workspace_name: str) -> WorkspaceContext:
        with self._lock:
            workspace = self._workspaces.get((user_id, workspace_name))
        if workspace is None:
            raise WorkspaceNotFoundError("工作区未注册或服务已重启，请重新选择工作目录")
        return workspace

    def add_file(self, workspace: WorkspaceContext, relative_path: str) -> None:
        normalized_path = self.normalize_relative_path(relative_path)
        with self._lock:
            workspace.file_paths.add(normalized_path)

    def remove_file(self, workspace: WorkspaceContext, relative_path: str) -> None:
        normalized_path = self.normalize_relative_path(relative_path)
        with self._lock:
            workspace.file_paths.discard(normalized_path)

    def list_files(self, workspace: WorkspaceContext) -> tuple[str, ...]:
        with self._lock:
            return tuple(workspace.file_paths)

    @staticmethod
    def normalize_relative_path(raw_path: str) -> str:
        normalized_value = raw_path.strip().replace("\\", "/")
        posix_path = PurePosixPath(normalized_value)
        windows_path = PureWindowsPath(raw_path)
        if (
                not normalized_value
                or posix_path.as_posix() == "."
                or posix_path.is_absolute()
                or windows_path.is_absolute()
                or windows_path.drive
                or ".." in posix_path.parts
        ):
            raise WorkspaceValidationError(f"工作区文件必须使用安全的相对路径：{raw_path}")
        return posix_path.as_posix()


workspace_service = WorkspaceService()
