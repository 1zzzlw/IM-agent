# FastAPI SSE 流式对话实战

这篇文档按照当前 `imAgent` 后端和 Electron 前端的真实结构编写。目标不仅是让流式输出跑起来，还要保证每段代码放在职责正确的位置，避免以后加入工具调用、心跳和取消功能时，把所有逻辑堆进同一个文件。

完成后的调用链：

```text
Ai-chat.vue
    ↓ 发起请求、更新界面
AIMessage.js
    ↓ 鉴权、请求、分发 AI 事件
utils/sse.js
    ↓ 把响应字节流解析成 SSE 事件
Spring Cloud Gateway
    ↓ 转发，不缓存响应
api/ai_message.py
    ↓ HTTP 请求转为业务调用，业务事件编码为 SSE
services/ai_message_service.py
    ↓ 保存消息、调用模型、产出结构化事件
Agent / ChatModel + Repository
```

> 本文指导你自己完成代码。建议逐章实现，每完成一层就按对应命令验证，不要一次改完后再统一排错。

## 00. 完成标准和文件归属

最终需要达到：

1. 前端仍通过 `POST /ai-message/sendMessage` 发送原有 JSON；
2. 后端返回 `text/event-stream`；
3. 模型生成一块，前端就显示一块；
4. 用户消息在模型调用前落库；
5. 助手消息在正常完成后只落库一次；
6. 中途失败能显示错误，用户可以主动停止生成；
7. 默认模型、自定义模型、历史消息接口不受影响。

需要新增或修改的文件：

```text
后端 E:\AgentProject\imAgent
├─ app/
│  ├─ api/
│  │  ├─ ai_message.py                 # 修改：路由、断连检测、StreamingResponse
│  │  └─ sse.py                        # 新增：只负责 SSE 文本编码
│  ├─ domain/entities/
│  │  └─ ai_stream.py                  # 新增：流式业务事件结构
│  └─ services/
│     └─ ai_message_service.py         # 修改：消息业务编排，产出业务事件
└─ tests/
   ├─ api/test_sse.py                  # 新增：SSE 编码测试
   └─ services/test_ai_message_service.py  # 新增：流式业务测试

前端 E:\ElectronProject\WeChat-Electron-vue-vite
└─ src/renderer/src/
   ├─ utils/sse.js                     # 新增：通用 SSE 拆包解析器
   ├─ api/AIMessage.js                 # 修改：请求、鉴权、AI 事件分发
   ├─ types/agentApi.ts                # 修改：流式事件类型
   ├─ types/index.ts                   # 修改：导出新类型
   └─ views/chat/Ai-chat.vue           # 修改：增量展示和停止请求
```

### 为什么这样放

| 层 | 应该知道什么 | 不应该知道什么 |
|---|---|---|
| `api` | FastAPI、Request、SSE、响应头 | SQL、模型供应商构造细节 |
| `services` | 一次发送消息的业务顺序 | `StreamingResponse`、SSE 文本格式 |
| `domain/entities` | 业务输入、输出和事件结构 | HTTP、数据库连接 |
| `agent/LLM` | 创建模型、适配模型 | SSE、页面状态、消息 SQL |
| `integrations/database` | SQL 和数据库连接 | 页面状态、SSE |

不要创建 `SSEService` 类，也不要把 SSE 放进 `commons`。当前只需要一个很小的编码文件；真正的业务编排仍然保留在现有 `ai_message_service.py`。

## 01. 当前代码为什么不是流式

当前 `app/services/ai_message_service.py` 使用：

```python
result = agent.invoke(body.content)
response_content = result.content
return response_content
```

`invoke()` 会等待模型完整生成。后端路由随后返回普通字符串，前端 `AIMessage.js` 又调用：

```javascript
const payload = await response.json()
```

因此模型、HTTP 响应和页面三层都在等待完整结果。三层必须一起接通：

```text
model.astream()
    ↓
StreamingResponse
    ↓
response.body.getReader()
```

只修改其中一层，不会形成真正的端到端流式体验。

## 02. 为什么使用 POST + fetch SSE

SSE 事件由普通文本行组成，每个事件以空行结束：

```text
event: message.delta
data: {"delta":"你"}

event: message.delta
data: {"delta":"好"}

```

最后的空行是协议的一部分，也就是 Python 字符串中的 `\n\n`。

浏览器原生 `EventSource` 更适合 GET 订阅。当前请求还需要携带消息正文、模型配置和 `Authorization` 请求头，因此继续使用 `fetch()` 发 POST，再读取 `response.body`。

MDN 对 [`ReadableStream.getReader()`](https://developer.mozilla.org/en-US/docs/Web/API/ReadableStream/getReader) 和 [`TextDecoder.decode(..., { stream: true })`](https://developer.mozilla.org/en-US/docs/Web/API/TextDecoder/decode) 有对应说明。

SSE 和 WebSocket 不冲突：

- 当前“一次请求、持续返回模型结果”使用 SSE；
- 以后语音通话、多人协作等长期双向通信再使用 WebSocket；
- 不要为了流式文字回复先引入两套通信协议。

## 03. 第一版事件契约

第一版实现五种事件：

| 事件 | 含义 | 数据字段 |
|---|---|---|
| `run.started` | 服务端开始处理 | `conversationId`、`clientMessageId` |
| `message.delta` | 新增的一小块文本 | `delta` |
| `message.completed` | 完整回答已生成并落库 | `messageId`、`content` |
| `run.failed` | 流开始后的业务错误 | `message` |
| `done` | 本次流正常结束 | `conversationId` |

完整示例：

```text
event: run.started
data: {"conversationId":"conversation-001","clientMessageId":"message-001"}

event: message.delta
data: {"delta":"你好"}

event: message.completed
data: {"messageId":"9527","content":"你好"}

event: done
data: {"conversationId":"conversation-001"}

```

约定：

- `data:` 后面统一是 JSON；
- `message.delta` 只放新增内容，不放累计全文；
- `message.completed.content` 放最终全文，用来校准前端结果；
- 不向前端发送 API Key、请求头或异常堆栈；
- 流开始后 HTTP 状态通常已经是 200，后续错误通过 `run.failed` 表达。

## 04. 后端第一步：定义业务事件

新建 `app/domain/entities/ai_stream.py`：

```python
from dataclasses import dataclass
from typing import Any, Literal


AIStreamEventName = Literal[
    "run.started",
    "message.delta",
    "message.completed",
    "run.failed",
    "done",
]


@dataclass(frozen=True, slots=True)
class AIStreamEvent:
    event: AIStreamEventName
    data: dict[str, Any]
```

这里使用 dataclass，而不是 Pydantic：

- 它是后端内部在 service 和 api 之间传递的事件；
- 不直接接收前端输入；
- 不需要重复执行复杂校验。

以后加入工具事件时，只需要扩展 `AIStreamEventName`，例如 `tool.started` 和 `tool.completed`。

## 05. 后端第二步：SSE 编码只放在 API 层

新建 `app/api/sse.py`：

```python
import json

from app.domain.entities.ai_stream import AIStreamEvent


def encode_sse(event: AIStreamEvent) -> str:
    payload = json.dumps(event.data, ensure_ascii=False)
    return f"event: {event.event}\ndata: {payload}\n\n"
```

这个文件只做一件事：把结构化业务事件转换成 SSE 文本。

不要把它放到 `ai_message_service.py`，因为 service 不应该知道 `event:`、`data:` 或 `\n\n` 这些 HTTP 传输细节。也不要直接拼模型文本，JSON 编码可以正确处理换行、引号和中文。

先验证编码器：

```powershell
cd E:\AgentProject\imAgent
uv run python -c "from app.api.sse import encode_sse; from app.domain.entities.ai_stream import AIStreamEvent; print(repr(encode_sse(AIStreamEvent('message.delta', {'delta': '你好'}))))"
```

预期：

```text
'event: message.delta\ndata: {"delta": "你好"}\n\n'
```

## 06. 后端第三步：业务服务只产出结构化事件

当前数据库使用同步 PyMySQL，而模型流使用异步调用。业务服务需要：

1. 在线程池中保存用户消息；
2. 调用 `model.astream()`；
3. 每获得一块文本就产出一个 `AIStreamEvent`；
4. 正常完成后在线程池中保存完整助手消息；
5. 不负责 SSE 编码，也不接收 FastAPI `Request`。

LangChain 的聊天模型支持 `astream()`，模型流会产出 `AIMessageChunk`；当前版本可以通过 `chunk.text` 获取文本。参考 [LangChain Models - Streaming](https://docs.langchain.com/oss/python/langchain/models#stream) 和 [LangChain Streaming](https://docs.langchain.com/oss/python/langchain/streaming)。

用下面内容替换 `app/services/ai_message_service.py`。不要保留原来的同步 `query()`，否则以后会出现两套发送逻辑：

```python
import asyncio
import logging
from collections.abc import AsyncIterator

from app.agent.agent import agent_running
from app.domain.entities.ai_stream import AIStreamEvent
from app.domain.entities.ai_message import AIMessageRequest
from app.integrations.database.ai_conversation_repository import (
    insert_ai_conversation,
    select_ai_conversation,
    touch_ai_conversation,
)
from app.integrations.database.ai_message_repository import insert_ai_message

logger = logging.getLogger(__name__)


def _create_conversation_title(content: str) -> str:
    return " ".join(content.split())[:24] or "新的对话"


def _prepare_user_message(body: AIMessageRequest) -> None:
    conversation = select_ai_conversation(
        conversation_id=body.conversation_id,
        user_id=body.user_id,
    )
    if conversation is None:
        insert_ai_conversation(
            conversation_id=body.conversation_id,
            user_id=body.user_id,
            title=_create_conversation_title(body.content),
        )

    insert_ai_message(
        conversation_id=body.conversation_id,
        user_id=body.user_id,
        role="user",
        message_type=body.message_type,
        content=body.content,
        image_url=body.image_url,
        personality_id=body.personality_id,
        config_id=body.config.config_id,
    )
    touch_ai_conversation(
        conversation_id=body.conversation_id,
        user_id=body.user_id,
    )


def _save_assistant_message(body: AIMessageRequest, content: str) -> int:
    message_id = insert_ai_message(
        conversation_id=body.conversation_id,
        user_id=body.user_id,
        role="assistant",
        message_type=1,
        content=content,
        personality_id=body.personality_id,
        config_id=body.config.config_id,
    )
    touch_ai_conversation(
        conversation_id=body.conversation_id,
        user_id=body.user_id,
    )
    return message_id


async def stream_reply(body: AIMessageRequest) -> AsyncIterator[AIStreamEvent]:
    full_content_parts: list[str] = []

    try:
        await asyncio.to_thread(_prepare_user_message, body)

        model = agent_running.submit_agent_task(body.config)

        yield AIStreamEvent(
            event="run.started",
            data={
                "conversationId": body.conversation_id,
                "clientMessageId": body.id,
            },
        )

        async for chunk in model.astream(body.content):
            delta = chunk.text
            if not delta:
                continue

            full_content_parts.append(delta)
            yield AIStreamEvent(
                event="message.delta",
                data={"delta": delta},
            )

        full_content = "".join(full_content_parts)
        message_id = await asyncio.to_thread(
            _save_assistant_message,
            body,
            full_content,
        )

        yield AIStreamEvent(
            event="message.completed",
            data={
                "messageId": str(message_id),
                "content": full_content,
            },
        )
        yield AIStreamEvent(
            event="done",
            data={"conversationId": body.conversation_id},
        )
    except asyncio.CancelledError:
        logger.info(
            "AI stream cancelled: conversation_id=%s user_id=%s",
            body.conversation_id,
            body.user_id,
        )
        raise
    except Exception:
        logger.exception(
            "AI stream failed: conversation_id=%s user_id=%s",
            body.conversation_id,
            body.user_id,
        )
        yield AIStreamEvent(
            event="run.failed",
            data={"message": "模型调用失败，请稍后重试"},
        )
```

### 为什么数据库辅助函数仍留在 service

`_prepare_user_message()` 和 `_save_assistant_message()` 表达的是“发送一次消息时按什么顺序调用仓储”，属于当前业务用例。它们没有 SQL，暂时不需要再拆一个文件。

只有将来多个业务用例都重复使用同一段会话规则时，才考虑提取新的会话服务。不要把每个十几行函数都拆成独立模块。

### 为什么使用 asyncio.to_thread

当前 PyMySQL 是同步驱动。直接在异步生成器里执行它会阻塞事件循环。`asyncio.to_thread()` 会让同步数据库操作在线程池中执行，同时避免 service 依赖 FastAPI 或 Starlette。

当前 `insert_ai_message()` 已经返回 `MysqlService.insert()` 的 `lastrowid`，因此可以直接作为助手消息 ID。

### 第一版落库规则

```text
模型调用前：保存用户消息
模型生成中：只在内存累计，不反复 UPDATE MySQL
模型完成后：保存一条完整助手消息
失败或取消：不保存残缺助手消息
```

以后确实需要恢复“生成中”消息时，再给消息表增加 `status`；第一版不需要提前扩大表结构。

## 07. 后端第四步：API 层转换为 StreamingResponse

FastAPI 的 `StreamingResponse` 可以消费异步生成器并逐块输出，参考 [FastAPI StreamingResponse](https://fastapi.tiangolo.com/advanced/custom-response/#streamingresponse)。

在 `app/api/ai_message.py` 顶部增加：

```python
from collections.abc import AsyncIterator
from contextlib import aclosing

from fastapi.responses import StreamingResponse

from app.api.sse import encode_sse
from app.services.ai_message_service import stream_reply
```

删除原来的：

```python
from app.services.ai_message_service import query
```

在路由文件中增加一个私有传输函数：

```python
async def _stream_message(
    body: AIMessageRequest,
    request: Request,
) -> AsyncIterator[str]:
    async with aclosing(stream_reply(body)) as events:
        async for event in events:
            if await request.is_disconnected():
                return

            yield encode_sse(event)
```

它只负责两件事：

- 检查 HTTP 客户端是否断开；
- 把 service 产出的 `AIStreamEvent` 编码为 SSE 字符串。

`aclosing()` 能在浏览器中断连接时关闭业务异步生成器，避免继续无意义地消费模型流。

把原来的 `send_message()` 替换为：

```python
@router.post("/sendMessage", response_class=StreamingResponse)
async def send_message(body: AIMessageRequest, request: Request):
    return StreamingResponse(
        _stream_message(body, request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
```

同时删除原来的 `print(user_info)`。不要随意打印完整请求头，也不要手动设置 `Content-Length`。

此时依赖方向是：

```text
api/ai_message.py
    ├─ imports api/sse.py
    └─ imports services/ai_message_service.py

services/ai_message_service.py
    ├─ imports domain/entities
    ├─ imports agent
    └─ imports integrations/database

services 不反向 import api
```

## 08. 先用 curl 验证后端

启动 Python 服务后先绕开网关和前端，直接请求 9090 端口：

```powershell
$requestBody = @'
{
  "id": "message-test-001",
  "conversationId": "conversation-test-001",
  "userId": "1780672870072950",
  "role": "user",
  "messageType": 1,
  "content": "请用三句话介绍 SSE",
  "imageUrl": null,
  "personalityId": null,
  "config": {
    "configId": null,
    "providerName": "default",
    "modelName": null,
    "apiKey": null,
    "baseUrl": null,
    "modelTemperature": 1.0,
    "enableThink": false
  }
}
'@

curl.exe -N -i `
  -X POST "http://127.0.0.1:9090/ai-message/sendMessage" `
  -H "Content-Type: application/json" `
  --data-raw $requestBody
```

`-N` 会关闭 curl 自己的输出缓冲。正确现象：

1. 响应头包含 `content-type: text/event-stream`；
2. 先出现 `run.started`；
3. 多次出现 `message.delta`；
4. 最后出现 `message.completed` 和 `done`；
5. 终端不是等完整答案生成后一次性显示。

如果直连 Python 是流式，经过网关后不是流式，说明业务代码已经正确，应排查第 14 章的代理配置。

## 09. 前端为什么必须使用半包缓冲

浏览器一次 `reader.read()` 得到的是网络字节块，不是 SSE 事件：

- 一个 SSE 事件可能被拆成多个网络块；
- 多个 SSE 事件可能合并在一个网络块中；
- 一个中文字符的 UTF-8 字节也可能被拆开。

所以下面的写法不可靠：

```javascript
const text = decoder.decode(value)
const events = text.split('\n\n')
```

正确实现必须保留：

```text
TextDecoder 的跨块 UTF-8 解码状态
未形成完整 SSE 事件的字符串 buffer
```

## 10. 前端第一步：建立通用 SSE 解析器

新建 `src/renderer/src/utils/sse.js`。这个文件只负责把 `Response` 转换成 `{ event, data }`，不知道任何 AI 业务事件：

```javascript
function parseSSEBlock(rawEvent) {
  let eventName = 'message'
  const dataLines = []

  for (const line of rawEvent.split('\n')) {
    if (!line || line.startsWith(':')) {
      continue
    }

    const separatorIndex = line.indexOf(':')
    const field = separatorIndex === -1 ? line : line.slice(0, separatorIndex)
    let value = separatorIndex === -1 ? '' : line.slice(separatorIndex + 1)

    if (value.startsWith(' ')) {
      value = value.slice(1)
    }

    if (field === 'event') {
      eventName = value
    } else if (field === 'data') {
      dataLines.push(value)
    }
  }

  if (dataLines.length === 0) {
    return null
  }

  return {
    event: eventName,
    data: dataLines.join('\n')
  }
}


function extractSSEEvents(buffer, flush = false) {
  let remaining = buffer.replace(/\r\n/g, '\n')
  const events = []

  let boundaryIndex = remaining.indexOf('\n\n')
  while (boundaryIndex !== -1) {
    const rawEvent = remaining.slice(0, boundaryIndex)
    remaining = remaining.slice(boundaryIndex + 2)

    if (rawEvent.trim()) {
      const event = parseSSEBlock(rawEvent)
      if (event) events.push(event)
    }

    boundaryIndex = remaining.indexOf('\n\n')
  }

  if (flush && remaining.trim()) {
    const event = parseSSEBlock(remaining)
    if (event) events.push(event)
    remaining = ''
  }

  return { events, remaining }
}


export async function* readSSE(response) {
  if (!response.body) {
    throw new Error('当前环境不支持流式响应')
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder('utf-8')
  let buffer = ''
  let streamEnded = false

  try {
    while (true) {
      const { value, done } = await reader.read()
      if (done) {
        streamEnded = true
        break
      }

      buffer += decoder.decode(value, { stream: true })
      const result = extractSSEEvents(buffer)
      buffer = result.remaining

      for (const event of result.events) {
        yield event
      }
    }

    buffer += decoder.decode()
    const result = extractSSEEvents(buffer, true)

    for (const event of result.events) {
      yield event
    }
  } finally {
    if (!streamEnded) {
      await reader.cancel().catch(() => {})
    }
    reader.releaseLock()
  }
}
```

这里刻意没有 `JSON.parse()`，因为通用 SSE 层只负责协议拆包。`data` 是不是 JSON，应由具体业务 API 决定。

这段代码处理了：

- UTF-8 中文拆包；
- SSE 事件拆包和粘包；
- `\r\n` 换行；
- 多行 `data:`；
- 以 `:` 开头的注释或心跳；
- 调用方提前退出时取消 reader。

## 11. 前端第二步：增加事件类型

在 `src/renderer/src/types/agentApi.ts` 末尾增加：

```typescript
export interface AIStreamStartedData {
  conversationId: string
  clientMessageId: string | null
}

export interface AIStreamDeltaData {
  delta: string
}

export interface AIStreamCompletedData {
  messageId: string
  content: string
}

export interface AIStreamDoneData {
  conversationId: string
}

export interface AIStreamHandlers {
  onStarted?: (data: AIStreamStartedData) => void
  onDelta?: (delta: string, data: AIStreamDeltaData) => void
  onCompleted?: (data: AIStreamCompletedData) => void
  onDone?: (data: AIStreamDoneData) => void
}
```

然后在 `src/renderer/src/types/index.ts` 原有的 `agentApi` 导出列表中加入：

```typescript
export type {
  AgentModelRequestConfig,
  AIStreamCompletedData,
  AIStreamDeltaData,
  AIStreamDoneData,
  AIStreamHandlers,
  AIStreamStartedData,
  SavedAIConversation,
  SavedAIMessage,
  SendAIMessageRequest
} from '@/types/agentApi'
```

这是类型契约，不包含解析和 UI 逻辑。

## 12. 前端第三步：AIMessage.js 负责请求和事件分发

在 `src/renderer/src/api/AIMessage.js` 顶部增加：

```javascript
import { readSSE } from '@/utils/sse'
```

保留文件中的模型配置、会话和个性化 API，只替换 `sendAIMessageApi()`：

```javascript
/**
 * 发送 AI 消息并消费 SSE 流。
 * @param {import('@/types').SendAIMessageRequest} data
 * @param {import('@/types').AIStreamHandlers} [handlers]
 * @param {AbortSignal} [signal]
 * @returns {Promise<string>}
 */
export async function sendAIMessageApi(data, handlers = {}, signal) {
  const baseURL = request.defaults.baseURL || window.location.origin

  const sendRequest = (accessToken) => {
    return fetch(`${baseURL}/ai-message/sendMessage`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `${accessToken}`
      },
      body: JSON.stringify(data),
      signal
    })
  }

  let response = await sendRequest(getAccessToken())

  if (response.status === 401) {
    if (response.body) {
      await response.body.cancel().catch(() => {})
    }

    try {
      console.info('AI请求token过期，刷新token')
      const userId = await window.userInfoApi.storeGetUserInfo('userId')
      const isSuccess = await refreshToken(userId)

      if (!isSuccess) {
        console.info('刷新token失败，返回登录界面')
        window.api.resizeWindow('login')
        window.location.href = '#/login'
        if (window.WSManager) {
          window.WSManager.disconnect()
        }
        throw new Error('登录已过期，请重新登录')
      }

      response = await sendRequest(getAccessToken())
    } catch (error) {
      console.error('Token刷新失败:', error)
      throw error
    }
  }

  if (!response.ok) {
    const errorText = await response.text()
    throw new Error(`请求失败: ${response.status} - ${errorText}`)
  }

  const contentType = response.headers.get('content-type') || ''
  if (!contentType.includes('text/event-stream')) {
    throw new Error(`服务端没有返回 SSE，实际类型：${contentType || '未知'}`)
  }

  let fullContent = ''
  let doneReceived = false

  for await (const event of readSSE(response)) {
    let eventData
    try {
      eventData = JSON.parse(event.data)
    } catch {
      throw new Error(`无法解析 SSE 事件数据：${event.event}`)
    }

    if (event.event === 'run.started') {
      handlers.onStarted?.(eventData)
      continue
    }

    if (event.event === 'message.delta') {
      const delta = eventData.delta ?? ''
      fullContent += delta
      handlers.onDelta?.(delta, eventData)
      continue
    }

    if (event.event === 'message.completed') {
      fullContent = eventData.content ?? fullContent
      handlers.onCompleted?.(eventData)
      continue
    }

    if (event.event === 'run.failed') {
      throw new Error(eventData.message || '模型调用失败')
    }

    if (event.event === 'done') {
      doneReceived = true
      handlers.onDone?.(eventData)
    }
  }

  if (!doneReceived) {
    throw new Error('SSE 连接提前结束，未收到 done 事件')
  }

  return fullContent
}
```

职责边界现在很清楚：

```text
utils/sse.js
    字节 → 通用 SSE 事件

api/AIMessage.js
    通用 SSE 事件 → AI 业务回调

Ai-chat.vue
    AI 业务回调 → 页面状态
```

`Authorization` 继续沿用项目现在的格式，不要自行增加或删除 `Bearer`。401 必须在开始消费 SSE 正文前处理。

## 13. 前端第四步：Ai-chat.vue 增量展示和取消

当前 Pinia 已经提供：

```text
appendAssistantContent()
setAssistantContent()
completeAssistantMessage()
failAssistantMessage()
stopGenerating()
```

不需要再创建新的消息状态容器。

### 13.1 清理模拟计时器

在响应式变量区域增加：

```typescript
const activeRequestController = ref<AbortController | null>(null)
```

真实流接通后删除：

```typescript
const responseTimer = ref<number | null>(null)
```

同时删除整个 `clearResponseTimer()` 函数，把 `stopActiveSimulation()` 改为：

```typescript
const stopActiveSimulation = () => {
  activeRequestController.value?.abort()
  activeRequestController.value = null

  if (activeSimulation.value) {
    agentWorkspaceStore.stopGenerating(
      activeSimulation.value.conversationId,
      activeSimulation.value.messageId
    )
  }
  activeSimulation.value = null
}
```

将取消集中在这里后，新建任务、删除当前会话、清空会话和组件卸载都会使用同一个停止逻辑。

### 13.2 替换 handleSubmit

用下面完整版本替换当前 `handleSubmit()`：

```typescript
const handleSubmit = async () => {
  if (!canSubmit.value || isGenerating.value) return

  const attachmentNames = attachments.value.map((attachment) => attachment.name).join('、')
  const content = [draft.value.trim(), attachmentNames ? `附件：${attachmentNames}` : '']
    .filter(Boolean)
    .join('\n')
  const conversationId = agentWorkspaceStore.ensureConversation(content)
  agentWorkspaceStore.addUserMessage(conversationId, content)
  const messageId = agentWorkspaceStore.addAssistantPlaceholder(
    conversationId,
    mockSettings.showToolSteps
  )

  if (!messageId) return

  draft.value = ''
  agentWorkspaceStore.clearAttachments()
  activeSimulation.value = { conversationId, messageId }
  void scrollToBottom()

  const controller = new AbortController()
  activeRequestController.value = controller

  try {
    const userId = await getCurrentUserId()
    const requestData: SendAIMessageRequest = {
      id: messageId,
      conversationId,
      userId,
      role: 'user',
      messageType: 1,
      content,
      config: {
        providerName: selectedModelOption.value.providerName,
        modelName: selectedModelOption.value.modelName,
        apiKey: selectedModelOption.value.apiKey,
        baseUrl: selectedModelOption.value.baseUrl,
        modelTemperature: selectedModelOption.value.modelTemperature ?? 1,
        enableThink: enableThink.value,
        configId: selectedModelOption.value.isDefault
          ? undefined
          : Number(selectedModelOption.value.id)
      }
    }

    const fullContent = await sendAIMessageApi(
      requestData,
      {
        onDelta: (delta) => {
          if (
            activeSimulation.value?.conversationId !== conversationId ||
            activeSimulation.value?.messageId !== messageId
          ) {
            return
          }

          agentWorkspaceStore.appendAssistantContent(conversationId, messageId, delta)
          void scrollToBottom()
        },
        onCompleted: ({ content }) => {
          if (
            activeSimulation.value?.conversationId !== conversationId ||
            activeSimulation.value?.messageId !== messageId
          ) {
            return
          }

          agentWorkspaceStore.setAssistantContent(conversationId, messageId, content)
        }
      },
      controller.signal
    )

    if (
      activeSimulation.value?.conversationId !== conversationId ||
      activeSimulation.value?.messageId !== messageId
    ) {
      return
    }

    agentWorkspaceStore.setAssistantContent(conversationId, messageId, fullContent)
    agentWorkspaceStore.completeAssistantMessage(conversationId, messageId)
    activeSimulation.value = null
  } catch (error) {
    if (error instanceof Error && error.name === 'AbortError') {
      return
    }

    if (
      activeSimulation.value?.conversationId !== conversationId ||
      activeSimulation.value?.messageId !== messageId
    ) {
      return
    }

    const errorMessage = error instanceof Error ? error.message : 'AI 服务请求失败'
    agentWorkspaceStore.failAssistantMessage(
      conversationId,
      messageId,
      `请求失败：${errorMessage}`
    )
    ElMessage.error(errorMessage)
    activeSimulation.value = null
  } finally {
    if (activeRequestController.value === controller) {
      activeRequestController.value = null
    }
  }
}
```

`appendAssistantContent()` 用于增量追加；`setAssistantContent()` 用于用服务端最终全文进行校准，两者作用不同。

### 13.3 停止按钮和组件卸载

停止按钮：

```typescript
const handleStopGenerating = () => {
  stopActiveSimulation()
  ElMessage.info('已停止生成')
}
```

当前 `onBeforeUnmount()` 已调用 `stopActiveSimulation()`，保持原样即可：

```typescript
onBeforeUnmount(() => {
  window.removeEventListener('keydown', handleGlobalShortcut)
  stopActiveSimulation()
})
```

`AbortController.abort()` 会让 fetch 以 `AbortError` 结束，参考 [MDN AbortSignal](https://developer.mozilla.org/en-US/docs/Web/API/AbortSignal)。

第一版停止表示断开当前 HTTP 流。模型厂商是否立即停止计费还取决于 SDK 的取消传播能力；以后出现后台长期任务时，再增加 `runId`、任务表和独立取消接口。

## 14. 网关和代理配置

验证顺序：

```text
直连 Python
    ↓
Spring Cloud Gateway
    ↓
Vite 开发代理
    ↓
Electron 页面
```

哪一层开始变成一次性返回，就检查哪一层。

### 14.1 Vite

当前 `electron.vite.config.mjs` 已把 `/api` 代理到网关。Vite 开发代理通常可以直接转发响应流，第一步不需要添加自定义插件。

### 14.2 Spring Cloud Gateway

现有网关已经把 `/ai-message/**` 路由到 `lb://im-agent`，不需要创建重复路由。如果长回答在固定时间被切断，再向当前 `spring.cloud.gateway` 配置添加：

```yaml
spring:
  cloud:
    gateway:
      httpclient:
        connect-timeout: 5000
        response-timeout: 10m
```

也可以只给现有 `im-agent` 路由增加 metadata：

```yaml
- id: im-agent
  uri: lb://im-agent
  predicates:
    - Path=/ai-message/**
  metadata:
    connect-timeout: 5000
    response-timeout: 600000
```

参考 [Spring Cloud Gateway HTTP 超时配置](https://docs.spring.io/spring-cloud-gateway/reference/spring-cloud-gateway-server-webflux/http-timeouts-configuration.html)。

### 14.3 线上 Nginx

只有线上实际使用 Nginx 时才增加：

```nginx
location /ai-message/ {
    proxy_pass http://gateway;
    proxy_http_version 1.1;
    proxy_buffering off;
    proxy_cache off;
    gzip off;
    proxy_read_timeout 600s;
}
```

后端的 `X-Accel-Buffering: no` 也会提示 Nginx 不缓冲。参考 [Nginx proxy_buffering](https://nginx.org/en/docs/http/ngx_http_proxy_module.html#proxy_buffering)。

## 15. 自动化测试：不调用真实模型

安装测试依赖：

```powershell
cd E:\AgentProject\imAgent
uv add --dev pytest
```

### 15.1 测试 SSE 编码层

新建 `tests/api/test_sse.py`：

```python
from app.api.sse import encode_sse
from app.domain.entities.ai_stream import AIStreamEvent


def test_encode_sse():
    result = encode_sse(
        AIStreamEvent(
            event="message.delta",
            data={"delta": "你好"},
        )
    )

    assert result.startswith("event: message.delta\n")
    assert 'data: {"delta": "你好"}\n\n' in result
```

这个测试只验证传输格式，不调用模型和数据库。

### 15.2 测试业务服务层

新建 `tests/services/test_ai_message_service.py`：

```python
from types import SimpleNamespace

import pytest

from app.domain.entities.ai_message import AIMessageRequest
from app.services import ai_message_service


@pytest.fixture
def anyio_backend():
    return "asyncio"


class FakeStreamingModel:
    async def astream(self, content: str):
        assert content == "你好"
        for text in ["你", "好", "！"]:
            yield SimpleNamespace(text=text)


@pytest.mark.anyio
async def test_stream_reply_outputs_events_and_saves_complete_message(monkeypatch):
    saved_messages = []
    generated_ids = iter([101, 202])

    monkeypatch.setattr(
        ai_message_service,
        "select_ai_conversation",
        lambda **kwargs: {"id": kwargs["conversation_id"]},
    )
    monkeypatch.setattr(
        ai_message_service,
        "insert_ai_message",
        lambda **kwargs: saved_messages.append(kwargs) or next(generated_ids),
    )
    monkeypatch.setattr(
        ai_message_service,
        "touch_ai_conversation",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        ai_message_service.agent_running,
        "submit_agent_task",
        lambda config: FakeStreamingModel(),
    )

    body = AIMessageRequest.model_validate(
        {
            "id": "message-test-001",
            "conversationId": "conversation-test-001",
            "userId": "user-test-001",
            "role": "user",
            "messageType": 1,
            "content": "你好",
            "imageUrl": None,
            "personalityId": None,
            "config": {
                "configId": None,
                "providerName": "default",
                "modelName": None,
                "apiKey": None,
                "baseUrl": None,
                "modelTemperature": 1.0,
                "enableThink": False,
            },
        }
    )

    events = [event async for event in ai_message_service.stream_reply(body)]
    event_names = [event.event for event in events]
    deltas = [
        event.data["delta"]
        for event in events
        if event.event == "message.delta"
    ]

    assert event_names == [
        "run.started",
        "message.delta",
        "message.delta",
        "message.delta",
        "message.completed",
        "done",
    ]
    assert "".join(deltas) == "你好！"
    assert saved_messages[0]["role"] == "user"
    assert saved_messages[1]["role"] == "assistant"
    assert saved_messages[1]["content"] == "你好！"
```

运行：

```powershell
uv run pytest tests/api/test_sse.py tests/services/test_ai_message_service.py -q
```

预期：

```text
2 passed
```

如果实体字段后来发生变化，以当前 `AIMessageRequest` 的真实必填字段调整测试输入，不要为了测试放松业务校验。

## 16. 数据一致性和失败语义

正常流程：

```text
创建会话（不存在时）
  ↓
保存用户消息
  ↓
run.started
  ↓
多个 message.delta
  ↓
保存完整助手消息
  ↓
message.completed
  ↓
done
```

失败或取消：

```text
用户消息已保存
  ↓
模型失败或客户端取消
  ↓
不保存残缺助手消息
  ↓
失败发送 run.failed；断连直接结束
```

因此用户刷新页面后可能只看到用户消息，看不到刚才未完成的助手占位消息。这是基础版的明确选择。

不要每收到一个 `message.delta` 就更新 MySQL。先在内存累计，完成后只插入一次，否则会造成大量 UPDATE 和锁竞争。

## 17. 常见问题

### 模型仍然一次性返回

按顺序检查：

1. service 是否调用 `model.astream()`；
2. 当前模型服务是否真正支持流式；
3. `curl.exe -N` 直连 Python 是否逐块出现；
4. 网关或 Nginx 是否缓冲；
5. 前端是否还在调用 `response.json()`。

### 中文乱码或 JSON.parse 偶发失败

确认通用解析器使用：

```javascript
decoder.decode(value, { stream: true })
```

并且保留未完成的 `buffer`。不能假设一次 `reader.read()` 就是一个完整事件。

### 前端收到 HTTP 200，但显示调用失败

流开始后出现业务错误时，状态码通常已经是 200。检查响应正文中是否收到：

```text
event: run.failed
```

### 后端逐块输出，前端几秒后一起出现

按 Python、Gateway、Vite、线上 Nginx 的顺序逐层测试，找到首次不流式的那一层。

### 点击停止后数据库没有助手消息

这是第一版的预期行为：保留用户消息，不保存残缺助手消息。

### 数据库连接耗尽

检查仓储函数是否在 `finally` 中关闭 cursor 和 connection。`asyncio.to_thread()` 只解决事件循环阻塞，不代替连接管理。

### 开发环境正常，打包后不正常

检查生产 API 地址和 CSP `connect-src`，再确认生产代理没有缓冲响应。

## 18. 将来接入真正 Agent 时如何扩展

当前 `agent_creator.create_agent()` 实际返回 `BaseChatModel`，所以 service 使用：

```python
async for chunk in model.astream(body.content):
```

将来接入真正的 LangGraph Agent 后，只修改 service 中“Agent 输出如何映射为 `AIStreamEvent`”的部分：

```text
模型 token         → message.delta
工具开始           → tool.started
工具完成           → tool.completed
等待批准           → tool.approval_required
最终回答           → message.completed
图执行失败         → run.failed
```

以下部分不需要推倒重写：

- `api/sse.py` 仍然只编码事件；
- `api/ai_message.py` 仍然只输出 StreamingResponse；
- `utils/sse.js` 仍然只解析 SSE；
- `Ai-chat.vue` 只增加新事件对应的 UI 行为。

这就是本次分层的主要价值。

## 19. 最终验收清单

### 文件职责

- [ ] `ai_message_service.py` 没有导入 FastAPI `Request`；
- [ ] `ai_message_service.py` 没有拼接 `event:` 或 `data:`；
- [ ] `api/sse.py` 只负责事件编码；
- [ ] `utils/sse.js` 不包含 `message.delta` 等 AI 业务判断；
- [ ] `AIMessage.js` 不手写字节拆包；
- [ ] 项目中不再保留同步 `query()` 发送分支。

### 后端

- [ ] `sendMessage` 返回 `StreamingResponse`；
- [ ] 响应类型是 `text/event-stream`；
- [ ] 模型使用 `astream()`；
- [ ] 每个事件以 `\n\n` 结束；
- [ ] 同步数据库操作通过 `asyncio.to_thread()` 执行；
- [ ] 用户消息先落库；
- [ ] 助手消息完成后只落库一次；
- [ ] 错误不会把 API Key 或堆栈发给前端。

### 前端

- [ ] 发送接口不再调用 `response.json()`；
- [ ] `utils/sse.js` 正确处理拆包、粘包和中文；
- [ ] `message.delta` 调用 `appendAssistantContent()`；
- [ ] `message.completed` 校准最终全文；
- [ ] `run.failed` 显示失败；
- [ ] 未收到 `done` 时不会把残缺响应标记为完成；
- [ ] 停止按钮调用 `AbortController.abort()`；
- [ ] 组件卸载会中止未完成请求。

### 端到端

- [ ] `curl.exe -N` 直连 Python 能逐块显示；
- [ ] 经过网关仍能逐块显示；
- [ ] Electron 页面能边生成边显示；
- [ ] 刷新页面能加载完整历史消息；
- [ ] 取消生成不会插入残缺助手消息；
- [ ] 默认模型和自定义模型走同一个流式接口。

全部通过后，基础 SSE 闭环才算完成。之后再逐项增加工具事件、心跳、任务表、断线恢复和服务端主动取消，不要重新把这些职责塞回同一个文件。
