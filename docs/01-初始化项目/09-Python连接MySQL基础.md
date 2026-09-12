# Python 连接 MySQL 基础

这篇文档以当前 `imAgent` 项目为例，完成下面这条最小链路：

```text
.env 数据库配置
    ↓
config.py 读取配置
    ↓
PyMySQL 创建连接
    ↓
执行 INSERT / SELECT
    ↓
commit 提交或 rollback 回滚
```

当前项目的 FastAPI 接口和 `query()` 都是同步函数，因此第一版使用同步的 `PyMySQL`。先把数据库读写跑通，暂时不引入 SQLAlchemy、异步连接池和仓储接口。

## 1. 准备 MySQL

如果电脑还没有 MySQL，可以安装 MySQL Server 8.x。Windows 官方安装说明：

- <https://dev.mysql.com/doc/refman/8.0/en/windows-installation.html>

安装时需要记住：

- 地址：通常是 `127.0.0.1`
- 端口：通常是 `3306`
- 用户名：例如 `root`
- 密码：安装 MySQL 时设置的密码
- 数据库名：当前项目使用 `zzz-im-server`

如果你已经能通过 Navicat 连接，并且已经导入 `zzz-im-server.sql`，这一章可以跳过。

可以在 Navicat 查询窗口执行下面的语句确认数据库存在：

```sql
SHOW DATABASES;

USE `zzz-im-server`;

SHOW TABLES;
```

观察结果：能够看到 `ai_message`、`ai_model_config` 等表。

## 2. 安装 PyMySQL

项目目前使用 `uv` 管理依赖。在项目根目录执行：

```powershell
cd E:\AgentProject\imAgent
uv add pymysql
```

如果 MySQL 账号使用 `caching_sha2_password`，连接时报认证依赖错误，可以改为：

```powershell
uv add "pymysql[rsa]"
```

验证安装：

```powershell
uv run python -c "import pymysql; print(pymysql.__version__)"
```

PyMySQL 官方安装说明：<https://pymysql.readthedocs.io/en/latest/user/installation.html>

## 3. 把数据库配置放进 `.env`

可以，而且数据库密码应该放在 `.env`，不要直接写在 Python 文件里。

在项目根目录的 `.env` 中增加：

```dotenv
DATABASE__HOST=127.0.0.1
DATABASE__PORT=3306
DATABASE__USER=root
DATABASE__PASSWORD=你的MySQL密码
DATABASE__NAME=zzz-im-server
DATABASE__CHARSET=utf8mb4
```

当前 `.gitignore` 已经忽略 `.env`，不要把真实密码提交到 Git。

注意：`.env` 只是普通文本文件，并不是加密文件。生产环境应该通过服务器环境变量或密钥管理服务提供密码。

## 4. 在 `config.py` 中读取配置

当前 `config.py` 已经使用了 `pydantic-settings`，并配置了：

```python
env_nested_delimiter="__"
```

因此 `DATABASE__HOST` 会自动对应 `database.host`。

在 `config.py` 中增加数据库配置类：

```python
class DatabaseConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 3306
    user: str = "root"
    password: str = ""
    name: str = "zzz-im-server"
    charset: str = "utf8mb4"
```

然后在 `Settings` 类中增加一个字段：

```python
class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        extra="ignore",
    )

    app: AppConfig = Field(default_factory=AppConfig)
    nacos: NacosConfig = Field(default_factory=NacosConfig)
    chat: ModelConfig = Field(default_factory=ModelConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
```

验证配置是否被读取。不要打印数据库密码：

```powershell
uv run python -c "from config import config; print(config.database.host, config.database.port, config.database.name)"
```

预期输出类似：

```text
127.0.0.1 3306 zzz-im-server
```

运行命令时应位于项目根目录，否则 `env_file=".env"` 可能找不到该文件。

## 5. 创建数据库连接模块

按照当前项目的目录规划，数据库属于外部系统接入，可以创建：

```text
app/
└── integrations/
    └── database/
        ├── __init__.py
        └── mysql.py
```

`app/integrations/database/mysql.py`：

```python
import pymysql
from pymysql.connections import Connection
from pymysql.cursors import DictCursor

from config import config


def get_mysql_connection() -> Connection:
    return pymysql.connect(
        host=config.database.host,
        port=config.database.port,
        user=config.database.user,
        password=config.database.password,
        database=config.database.name,
        charset=config.database.charset,
        cursorclass=DictCursor,
        autocommit=False,
        connect_timeout=5,
    )
```

这里几个关键参数的作用：

- `database`：连接后默认使用哪个数据库。
- `charset="utf8mb4"`：正常保存中文和 Emoji。
- `DictCursor`：查询结果返回字典，而不是元组。
- `autocommit=False`：插入、修改和删除后必须主动调用 `commit()`。

## 6. 测试数据库连接

先不要急着接入聊天接口，创建一个临时测试文件 `tests/test_mysql_connection.py`：

```python
from app.integrations.database.mysql import get_mysql_connection


connection = get_mysql_connection()

try:
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1 AS result")
        row = cursor.fetchone()
        print(row)
finally:
    connection.close()
```

运行：

```powershell
uv run python tests/test_mysql_connection.py
```

预期输出：

```text
{'result': 1}
```

看到这个结果只能证明 Python 成功连接 MySQL，还没有验证业务表是否正确。

## 7. 插入一条 AI 消息

先写一个最小函数：

```python
from app.integrations.database.mysql import get_mysql_connection


def insert_ai_message(
    conversation_id: str,
    user_id: int,
    role: str,
    message_type: int,
    content: str,
    image_url: str | None = None,
    personality_id: int | None = None,
    config_id: int | None = None,
) -> int:
    sql = """
        INSERT INTO ai_message (
            conversation_id,
            user_id,
            role,
            message_type,
            content,
            image_url,
            personality_id,
            config_id
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
    """

    connection = get_mysql_connection()

    try:
        with connection.cursor() as cursor:
            cursor.execute(
                sql,
                (
                    conversation_id,
                    user_id,
                    role,
                    message_type,
                    content,
                    image_url,
                    personality_id,
                    config_id,
                ),
            )
            message_id = cursor.lastrowid

        connection.commit()
        return int(message_id)
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
```

这里必须理解四件事：

1. SQL 中使用 `%s` 占位符，真实值通过第二个参数传入。
2. 不要使用字符串拼接生成 SQL，否则容易产生 SQL 注入。
3. 成功后调用 `commit()`，数据才会真正保存。
4. 失败后调用 `rollback()`，撤销当前事务里的修改。

PyMySQL 官方 CRUD 示例：<https://pymysql.readthedocs.io/en/latest/user/examples.html>

## 8. 查询一个会话的消息

```python
from typing import Any

from app.integrations.database.mysql import get_mysql_connection


def list_ai_messages(
    user_id: int,
    conversation_id: str,
    limit: int = 50,
) -> list[dict[str, Any]]:
    sql = """
        SELECT
            id,
            conversation_id,
            user_id,
            role,
            message_type,
            content,
            image_url,
            personality_id,
            config_id,
            send_time
        FROM ai_message
        WHERE user_id = %s
          AND conversation_id = %s
        ORDER BY send_time ASC, id ASC
        LIMIT %s
    """

    connection = get_mysql_connection()

    try:
        with connection.cursor() as cursor:
            cursor.execute(sql, (user_id, conversation_id, limit))
            return list(cursor.fetchall())
    finally:
        connection.close()
```

查询不会修改数据，所以不需要 `commit()`。

特别注意：查询条件必须同时包含 `user_id` 和 `conversation_id`。不能只相信前端传来的会话 ID，否则用户可能读取到不属于自己的聊天记录。

## 9. 保存用户问题和 AI 回答

当前调用过程可以逐步演进为：

```python
def query(content: str, config: AgentConfigRequest) -> str:
    # 1. 保存用户消息
    # 2. 调用模型
    # 3. 保存 assistant 消息
    # 4. 返回回答
    ...
```

不要把这四步一次性全塞进 API 路由。路由只负责接收请求，消息保存和模型调用放在 service 中。

如果模型调用失败，第一版可以保留用户消息，并把错误返回给前端；不要保存一条假的 assistant 回答。

## 10. 模型配置表怎么处理

`ai_model_config` 可以保存：

- `provider_name`
- `model_name`
- `base_url`
- `model_temperature`
- `enable_think`

不要把用户的明文 `api_key` 保存进普通数据库字段。当前基础版可以这样处理：

- `provider_name="default"`：后端从 `.env` 读取项目默认模型和密钥。
- 用户自定义模型：前端请求携带密钥，后端只在本次请求或当前进程内存中使用。
- 以后确实需要长期保存密钥时，再增加专门的加密存储方案。

## 11. 常见错误

### `ModuleNotFoundError: No module named 'pymysql'`

依赖没有安装到当前项目环境：

```powershell
uv add pymysql
```

### `Access denied for user`

用户名或密码错误，也可能是该用户没有访问 `zzz-im-server` 的权限。先用同一组账号在 Navicat 中测试。

### `Can't connect to MySQL server`

检查 MySQL 服务是否启动、端口是否为 `3306`，以及 `.env` 中的地址是否正确。

### `Unknown database 'zzz-im-server'`

数据库尚未创建或名称写错。带有短横线的数据库名在 SQL 中需要使用反引号：

```sql
CREATE DATABASE `zzz-im-server`
    CHARACTER SET utf8mb4
    COLLATE utf8mb4_0900_ai_ci;
```

### 插入成功但 Navicat 看不到数据

通常是忘记调用：

```python
connection.commit()
```

## 12. 学完后的验收清单

- [ ] `uv run python -c "import pymysql"` 不报错。
- [ ] `config.database` 能读取 `.env`，但程序不会打印密码。
- [ ] `SELECT 1` 返回 `{'result': 1}`。
- [ ] 可以向 `ai_message` 插入一条 `user` 消息。
- [ ] 可以按 `user_id + conversation_id` 查询消息。
- [ ] SQL 参数全部使用占位符，没有拼接用户输入。
- [ ] 写操作包含 `commit / rollback / close`。
- [ ] `.env` 没有提交到 Git。

完成这些内容，就已经掌握当前 `imAgent` 第一版所需的 Python MySQL 基础。等真实并发量增加后，再学习连接池、异步驱动和 SQLAlchemy。
