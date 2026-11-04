"""JSON 字段的统一读写约定。

业务表里的 JSON 列一律使用 ``{schemaVersion, data}`` 信封：
- ``dump`` 负责紧凑序列化（中文不转义，datetime 转字符串）；
- ``load`` 兼容"字符串 / 已解析对象 / NULL"三种读法；
- ``data`` 只取信封里的 data，供读取业务字段；
- ``envelope`` 写入时统一加版本号；
- ``digest`` 给幂等键与重试核对用摘要。

这些函数不碰数据库、不依赖任何业务模块，因此放在 core，谁都可以用。
"""

import hashlib
import json


def dump(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def load(value):
    return json.loads(value) if isinstance(value, str) else value or {}


def data(value):
    value = load(value)
    return value.get("data", value)


def envelope(value) -> str:
    return dump({"schemaVersion": 1, "data": value})


def digest(value) -> str:
    return hashlib.sha256(dump(value).encode()).hexdigest()
