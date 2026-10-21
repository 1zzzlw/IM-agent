from pathlib import PurePosixPath, PureWindowsPath


class PathValidationError(ValueError):
    """工作区相对路径不满足安全约束。"""


def normalize_relative_path(raw_path: str) -> str:
    """
    规范化工作区内的相对路径，并拒绝任何可能逃逸工作区的形式。

    参数:
        raw_path: 客户端或模型给出的原始路径
    返回:
        POSIX 风格的相对路径
    异常:
        PathValidationError: 路径为空、为绝对路径、带盘符或包含 ".."
    """
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
        raise PathValidationError(f"工作区文件必须使用安全的相对路径：{raw_path}")
    return posix_path.as_posix()
