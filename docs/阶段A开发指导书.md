# imAgent 阶段 A 开发指导书

> 更新日期：2026-10-21。适用于当前 Python imAgent 与 Electron 客户端。
>
> 本文是实际开工入口：先完成第 00、01 章，再逐章推进。[初版开发方案与技术架构.md](初版开发方案与技术架构.md) 保留为架构参考，[总目标.md](总目标.md) 保留为完整产品方向。
>
> 本次只编写指导书。下文代码块是建议新增或修改的示例，不表示已写入业务代码。示例验证与实际项目联调应分别记录。

## 先回答：阶段 A 要不要向量检索，代码存在哪里

**阶段 A 先不加向量检索。采用“本地文件系统 + 路径查找 + 关键字搜索 + 按需读取 + 本轮 Agent 上下文”。**

代码原件一直保存在用户选择的项目目录。向量库是辅助定位内容的索引；没有向量库，Electron 仍然可以通过文件 API 搜索和读取代码。

例如用户选择一个有 1,000 个文件的项目：

1. 客户端登记本地根目录，后端取得相对文件列表。
2. 用户问“用户名校验在哪里”，模型先看目录或搜索 `username`、`validate` 等关键字。
3. Electron 搜索当前磁盘文件，返回少量文件名、行号和命中内容。
4. 模型选出一两个文件继续读取，源码片段通过后端进入本次模型上下文。
5. 模型依据当前源码修改文件，并由客户端执行测试。

这条链路不需要先上传全部源码，也不需要先给全部文件计算向量。按需读取的片段仍然会经过后端传给模型，不能把它描述成“源码完全不出本机”。

### 不同数据的保存位置

| 数据 | 阶段 A 保存在哪里 | 保存多久与用途 |
|---|---|---|
| 项目源码原件 | 用户电脑原有工作区 | 长期保留，是修改依据 |
| 工作区绝对路径、当前用户与目录的绑定 | Electron 主进程内存，并持久化到客户端已有 SQLite | 重启后恢复绑定，再检查目录是否仍有效 |
| 工作区 ID、所属用户、展示名称 | 后端 MySQL 元数据 | 校验归属，名称不作为唯一键 |
| 相对文件列表 | 后端内存，客户端重新扫描可恢复 | 帮助列目录，不保存每个文件的正文 |
| 本轮读回的源码与工具结果 | 后端当前 Agent 的消息状态 | 有预算地使用；运行结束后不作为全文代码库另行保存 |
| 用户消息、最终助手回复、必要操作摘要 | 现有 MySQL | 提供聊天历史；用户粘贴或助手输出的代码可能随消息保存 |
| Run 状态、开始结束时间、错误摘要 | 后端 MySQL 最小运行表 | 取消、查询、重连与审计 |
| 修改前快照、修改后哈希、工具执行记录 | 客户端应用私有目录及现有 SQLite | 用于撤销、去重和核查执行状态 |
| 全项目向量 | 阶段 A 不生成 | 阶段 B 再接入 |

你的客户端已有 `src/main/DB/mainDB.js`，SQLite 位于 `app.getPath('userData')/local.db`。工作区绑定与执行记录可复用这个入口；快照文件放在同一应用数据目录下的专用子目录，避免存进待修改项目。`userData` 是 Electron 提供的应用数据位置。[Electron app 文档](https://www.electronjs.org/docs/latest/api/app)

前端 Pinia 保存展示状态；后端 MySQL 保存业务状态；本地文件系统保存真实源码。不要让其中一份代替另外两份。

### 先完成这一件小事

**第一个开发任务：让 Agent 能调用 `search_workspace_code`，找到示例项目中的 `valid_username` 函数。**

第一轮实现继续沿用当前 `workspaceName` 通信，不同时迁移所有接口。第 03 章再统一工作区 ID、运行记录和结果结构。身份隔离与稳定标识仍是阶段 A 交付要求，局部验证成功不能直接作为公网版本发布。

## 00. 准备环境与固定演示项目

### 00.1 公开初始化

以下变量在你执行本书命令的 PowerShell 会话中显式初始化；不要依赖其他文档里曾经设置过的变量：

```powershell
$serverRoot = 'E:\AgentProject\imAgent'
$desktopRoot = 'E:\ElectronProject\WeChat-Electron-vue-vite'
$demoRoot = Join-Path $serverRoot 'resources\examples\stage-a-demo'
```

先确认 `git status --short`，保留当前未提交改动。演示使用单独目录，不让尚未验收的 Agent 直接修改 imAgent 自己。

### 00.2 建立只有两个文件的小项目

在 `$demoRoot` 下创建以下文件。目录若已存在，先检查内容，避免覆盖已有实验。

`app.py`：

```python
def valid_username(name: str) -> bool:
    return len(name) >= 3
```

`test_app.py`：

```python
import unittest

from app import valid_username


class UsernameTests(unittest.TestCase):
    def test_normal(self):
        self.assertTrue(valid_username("alice"))

    def test_too_short(self):
        self.assertFalse(valid_username("ab"))

    def test_too_long(self):
        self.assertFalse(valid_username("a" * 21))


if __name__ == "__main__":
    unittest.main()
```

在演示目录运行：

```powershell
Set-Location -LiteralPath $demoRoot
python -m unittest -v
```

这里故意留了“未限制长度上限”的缺陷，预期三个测试中一个失败。以后让 Agent 修复它并增加长度恰好为 3、20 的边界测试，保留原有断言。这是可重复验收的任务，不需要额外安装 FastAPI 或 pytest。

### 00.3 确认已有工具链

后端开发入口来自当前 `main.py`：

```powershell
Set-Location -LiteralPath $serverRoot
uv run python main.py
```

客户端在另一个终端启动：

```powershell
Set-Location -LiteralPath $desktopRoot
npm run dev
```

沿用现有登录与网关入口，把演示目录添加到工作区，发送：“列出当前工作区文件，然后读取 app.py。”

记录实际是否出现：`list_workspace_files`、`tool.request`、本地 read、`/workspace/tool/result`、模型回答。当前已有源码入口不等于这次机器上的配置已经能跑通。

**通过标准**：能看到真实 `app.py` 内容；未通过时先定位下面哪个环节没有发生，不同时改模型、前端和数据库。

| 断在哪一步 | 先检查 |
|---|---|
| 工作区注册失败 | `handleAddWorkspace`、本地目录扫描、`/workspace/register` |
| 模型不调工具 | 当前模型是否支持工具调用、`build_workspace_tools` 是否传入 |
| 有 `tool.request` 但没读文件 | SSE 分发、`handleWorkspaceToolRequest`、preload / IPC |
| 文件读到了但模型一直等待 | `/tool/result` 是否提交、用户和工作区名称是否匹配、Future 是否被唤醒 |
| 回答结束但界面还在运行 | `message.completed`、`done` 与前端终态处理 |

### 00.4 先处理启动前置依赖

当前 `lifespan.py` 会初始化 MySQL、Redis、Chroma、Embedding 并注册 Nacos。阶段 A 不检索向量，不代表当前程序已经去掉了这些启动依赖。

实施时建议：保留 `app/embedding/`、`app/project_index/`、`app/integrations/vector_store/` 与原有数据；将未使用的 Chroma/Embedding 初始化和对应关闭移出阶段 A 的基础对话启动路径，阶段 B 真正接入索引时再统一启停。其余现有业务依赖按实际部署继续使用。

静态阅读还发现 `lifespan.py` 调用了 `embedding_service.close()`，但当前 `EmbeddingService` 没有该方法；调整生命周期时应同时对齐初始化和清理，不能只改启动而遗留错误关闭路径。

**通过标准**：启动、一次已有对话、正常关闭都记录真实结果；没有使用索引时，不应因为缺少 Embedding 配置而阻塞基础聊天。

## 01. 第一个功能：本地代码搜索

本章先做客户端，再接工具。每完成一段都可独立验证。

### 01.1 新增独立搜索函数

建议新增客户端 `src/main/File/workspaceSearch.mjs`。使用 `.mjs` 是为了让这个只依赖 Node 的模块可以直接运行独立测试，不导入 `fileInfo.js` 中的 Electron `shell`。

下面是小型工作区的起步实现：字面量搜索、UTF-8 文本、有限文件数、有限总读取量、有限结果数。它不负责持久化源码，也不生成索引。

```javascript
import fs from 'node:fs/promises'
import path from 'node:path'

const SKIP_DIRS = new Set(['node_modules', '.git', 'out', 'dist', '.venv', '__pycache__'])
const TEXT_EXTS = new Set(['.py', '.js', '.mjs', '.cjs', '.ts', '.vue', '.java', '.json', '.md', '.txt', '.css', '.html', '.xml', '.toml', '.yaml', '.yml', '.sql'])

export async function searchWorkspaceCode(rootDir, filePaths, query, limit = 20) {
  const needle = typeof query === 'string' ? query.trim() : ''
  if (!needle || needle.length > 200) throw new Error('搜索关键字长度应为 1 到 200')
  const safeLimit = Math.max(1, Math.min(Number.isInteger(limit) ? limit : 20, 50))
  const root = await fs.realpath(rootDir)
  const matches = []
  let searchedFiles = 0
  let totalBytes = 0
  let skippedFiles = 0
  let truncated = filePaths.length > 2000

  for (const candidate of filePaths.slice(0, 2000)) {
    const relativePath = candidate.replaceAll('\\', '/')
    const parts = relativePath.split('/')
    if (path.isAbsolute(relativePath) || /^[A-Za-z]:/.test(relativePath) || parts.includes('..')) {
      throw new Error('搜索候选包含越界路径')
    }
    const name = parts.at(-1)
    if (parts.some((part) => SKIP_DIRS.has(part)) || name.startsWith('.env') || !TEXT_EXTS.has(path.extname(name))) {
      skippedFiles += 1
      continue
    }

    let buffer
    try {
      const target = await fs.realpath(path.resolve(root, relativePath))
      const inside = path.relative(root, target)
      if (inside === '..' || inside.startsWith(`..${path.sep}`) || path.isAbsolute(inside)) {
        throw new Error('解析后的搜索路径超出工作区')
      }
      const handle = await fs.open(target, 'r')
      try {
        const info = await handle.stat()
        if (!info.isFile() || info.size > 1_000_000) {
          skippedFiles += 1
          continue
        }
        if (totalBytes + info.size > 8_000_000) {
          truncated = true
          break
        }
        buffer = Buffer.alloc(Math.min(1_000_001, 8_000_001 - totalBytes))
        let length = 0
        while (length < buffer.length) {
          const read = await handle.read(buffer, length, buffer.length - length, null)
          if (read.bytesRead === 0) break
          length += read.bytesRead
        }
        buffer = buffer.subarray(0, length)
      } finally {
        await handle.close()
      }
    } catch (error) {
      if (['ENOENT', 'EACCES', 'EPERM'].includes(error.code)) {
        skippedFiles += 1
        continue
      }
      throw error
    }

    totalBytes += buffer.length
    if (buffer.length > 1_000_000 || totalBytes > 8_000_000) {
      truncated = true
      break
    }
    let content
    try {
      content = new TextDecoder('utf-8', { fatal: true }).decode(buffer)
      if (content.includes('\u0000')) throw new Error('二进制文件')
    } catch {
      skippedFiles += 1
      continue
    }
    searchedFiles += 1
    const lines = content.split(/\r?\n/)
    for (let index = 0; index < lines.length; index += 1) {
      const column = lines[index].indexOf(needle)
      if (column < 0) continue
      matches.push({
        path: relativePath,
        line: index + 1,
        text: lines[index].slice(Math.max(0, column - 80), column + needle.length + 120)
      })
      if (matches.length >= safeLimit) {
        return { matches, searchedFiles, skippedFiles, truncated: true }
      }
    }
  }
  return { matches, searchedFiles, skippedFiles, truncated }
}
```

`truncated: true` 表示达到扫描、读取或结果上限，可能还有内容未搜索，不能向模型报告“整个项目都没有其他结果”。例子只支持列出的文本后缀；大项目以后增加目录前缀和更快搜索实现，不在第一步追求所有语言与所有编码。

目录枚举继续使用已有 `scanDirectory`，它已经跳过扫描时发现的符号链接；搜索函数另外核对真实路径。统一过滤规则和更复杂的文件系统并发情况，作为接入后的路径回归内容继续验证。异步文件 API 与文件句柄行为可参考 [Node 文件系统文档](https://nodejs.org/api/fs.html)。

### 01.2 给搜索函数一份独立测试

新增客户端 `tests/workspaceSearch.test.mjs`，示例覆盖命中、无命中、忽略、非法路径、空查询与结果上限：

```javascript
import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { searchWorkspaceCode } from '../src/main/File/workspaceSearch.mjs'

test('本地搜索的结果、范围与过滤规则', async () => {
  const parent = await fs.realpath(os.tmpdir())
  const root = await fs.mkdtemp(path.join(parent, 'imagent-guide-'))
  try {
    await fs.writeFile(path.join(root, 'app.py'), 'def valid_username(name):\n    return len(name) >= 3\n')
    await fs.mkdir(path.join(root, 'node_modules'))
    await fs.writeFile(path.join(root, 'node_modules', 'hidden.js'), 'valid_username')
    await fs.writeFile(path.join(root, '.env'), 'valid_username=private')
    const files = ['app.py', 'node_modules/hidden.js', '.env']
    const found = await searchWorkspaceCode(root, files, 'valid_username')
    assert.deepEqual(found.matches.map(({ path, line }) => [path, line]), [['app.py', 1]])
    assert.equal(found.skippedFiles, 2)
    assert.deepEqual((await searchWorkspaceCode(root, files, 'not_present')).matches, [])
    await assert.rejects(searchWorkspaceCode(root, ['../outside.py'], 'name'), /越界/)
    await assert.rejects(searchWorkspaceCode(root, files, ' '), /关键字/)
    await fs.writeFile(path.join(root, 'many.py'), 'hit\nhit\n')
    const limited = await searchWorkspaceCode(root, ['many.py'], 'hit', 1)
    assert.equal(limited.matches.length, 1)
    assert.equal(limited.truncated, true)
  } finally {
    const actual = await fs.realpath(root)
    const relative = path.relative(parent, actual)
    assert.ok(relative.startsWith('imagent-guide-') && !relative.includes(path.sep) && !path.isAbsolute(relative))
    await fs.rm(actual, { recursive: true, force: true })
  }
})
```

在客户端根目录执行：

```powershell
node --test tests/workspaceSearch.test.mjs
```

**通过标准**：独立测试通过。它验证 Node 搜索函数，不代表 Electron IPC 或模型调用已经通过。

### 01.3 接入已有 Electron 执行入口

在 `src/main/File/fileInfo.js` 导入：

```javascript
import { searchWorkspaceCode } from './workspaceSearch.mjs'
```

在 `executeWorkspaceTool(request)` 内、已有 read 分支之前增加：

```javascript
if (operation === 'search') {
  const rootDir = workspaceRoots.get(workspaceName)
  if (!rootDir) throw new Error('本地工作区不存在，请重新选择工作目录')
  const scan = await scanDirectory(rootDir, { maxFiles: 2000 })
  const result = await searchWorkspaceCode(rootDir, scan.files, request.query, request.limit)
  const hits = result.matches.map((item) => `${item.path}:${item.line}: ${item.text}`).join('\n')
  const note = scan.truncated || result.truncated ? '搜索达到上限，结果可能不完整' : '本次候选范围搜索结束'
  return {
    success: true,
    content: `${hits || '没有匹配结果'}\n${note}；已搜索 ${result.searchedFiles} 个文本文件，跳过 ${result.skippedFiles} 个文件`
  }
}
```

已有 `workspace:execute-tool` IPC 与 preload 是通用转发入口，本步骤可以继续复用，不新增一套 IPC 或 HTTP 接口。

在 Electron 界面选中示例工作区后，打开该界面的开发者工具控制台，手动调用：

```javascript
await window.uploadFileApi.executeWorkspaceTool({
  workspaceName: 'stage-a-demo',
  operation: 'search',
  query: 'valid_username',
  limit: 10
})
```

`workspaceName` 使用界面实际登记的名称。预期至少命中 `app.py`，也可能命中测试文件。

**通过标准**：不经过模型也能获得正确结果。失败时只排查客户端搜索、工作区绑定与 IPC，不改模型提示词。

### 01.4 更新前端类型与步骤标题

在 `src/renderer/src/types/agentApi.ts` 增加 `search` 操作。将文件操作请求与搜索请求区分为联合类型：文件操作仍要求 `path`，搜索请求要求 `query`，避免简单把所有字段改成可选。

```typescript
export interface AIWorkspaceSearchToolRequest {
  toolCallId: string
  workspaceName: string
  operation: 'search'
  query: string
  limit?: number
}

// 原有请求接口改名为 AIWorkspaceFileToolRequest，保持原有文件字段。
// 其 operation 改为 Exclude<AIWorkspaceToolOperation, 'search'>，只接受文件操作。
export type AIWorkspaceToolRequest =
  | AIWorkspaceFileToolRequest
  | AIWorkspaceSearchToolRequest
```

同步修改 `Ai-chat.vue` 的工具标签表与标题：search 显示关键字，文件操作显示路径。第一版结果沿用 `content` 回传即可，不要求先完成结构化结果迁移。

### 01.5 增加后端 Tool

在 `app/agent/tools/workspace.py` 的 `build_workspace_tools(workspace)` 内增加：

```python
@tool
async def search_workspace_code(query: str, limit: int = 20) -> str:
    """在用户客户端搜索工作区代码的字面量关键字，返回相对路径、行号和命中内容。"""
    if not query.strip() or len(query) > 200:
        raise ValueError("搜索关键字长度应为 1 到 200")
    result = await tool_request_dispatcher.request(
        user_id=workspace.user_id,
        workspace_name=workspace.name,
        operation="search",
        arguments={"query": query, "limit": max(1, min(limit, 50))},
        stream_writer=get_stream_writer(),
    )
    return _tool_content(result.success, result.content, result.error)
```

把该函数加入末尾返回的工具列表，并在 `_workspace_system_prompt()` 中补充“已知名称或关键字时先搜索命中位置，再读取上下文”。不需要改 `AgentRuntime`，它已经把工具交给 `create_agent()`。

最后发送：“找到用户名校验函数，读取实现并解释问题，暂时不要修改。”

**通过标准**：工具步骤显示搜索与读取；答案依据真实函数，能指出缺少长度上限。做到这里，你已经完成第一条新增功能链路。

## 02. 多轮上下文：读到的代码怎样交给模型

### 02.1 本轮代码暂存在哪个对象里

当前读取工具返回文本，由 Agent 框架把它作为工具结果继续交给模型。概念上，一轮执行包含：

```text
用户消息：找出用户名校验位置
助手工具调用：search_workspace_code("valid_username")
工具结果：app.py 第 1 行……
助手工具调用：read_workspace_file("app.py", 1, 30)
工具结果：当前源码片段……
助手答复：这个函数没有限制长度上限……
```

这些消息在当前运行的 Agent 状态中组织。`ToolMessage` 应与对应模型工具调用配对；客户端 HTTP 使用的 `toolCallId` 是跨端关联标识，不可随意替换模型内部配对 ID。沿用框架封装即可，不自行拼一套不配对的工具消息。

不必把这些片段先写进 MySQL 或 Chroma，模型才“看得见”。模型接收的是此次调用的消息上下文，数据库只是你组织、恢复这些上下文的可选来源。Agent 工具循环的用法见 [LangChain Agent 文档](https://docs.langchain.com/oss/python/langchain/agents)。

### 02.2 当前需要补的跨轮历史

`ai_message_service.py` 当前主要传本轮 `body.content`。实施顺序：

1. 在准备阶段读取当前用户、当前会话的必要历史，继续使用 `select_ai_messages()` 的正序结果。
2. 先读取历史再保存当前用户消息；或者保存后查询，但按插入 ID 排除重复的本轮消息。
3. 把历史的 user/assistant 角色转为消息列表，末尾只加一次当前用户内容。
4. 普通模型 `astream()` 和 Agent `astream({"messages": ...})` 都使用该列表。
5. 运行结束只保存最终助手答复与必要操作摘要；跨轮需要代码时重新读取，避免把过期源码无限堆进历史。

先验两轮：“当前函数有什么问题？”随后“请继续处理刚才的长度上限问题”。再新建另一个会话，验证不会串入上一会话内容。

历史需要按上下文预算裁剪，工具结果也应限制文件行数与输出长度。初始验证可使用较小的历史窗口，但发布前要有总输入预算；消息条数不能代替 token 或保守文本预算。暂不同时引入第二套 checkpoint 存储。[LangChain 短期记忆文档](https://docs.langchain.com/oss/python/langchain/short-term-memory)

**本章完成标准**：能承接前文、会话隔离、当前消息没有重复加入；模型每次修改前仍读取当前文件。

## 03. 补齐稳定标识、持久化与结果结构

前两章是已有链路上的局部验证。开始受控修改前，把运行和工作区身份补齐，避免每增加一种工具都临时拼字段。

### 03.1 最小数据模型

本章列的是逻辑字段，实际类型与外键要对照当前数据库后写入迁移脚本，不直接猜测现有用户 ID 的数据库类型。

| 位置 | 建议记录 | 关键字段与约束 |
|---|---|---|
| 后端 MySQL | `ai_workspace` | 工作区 ID、用户 ID、展示名称；名称允许重复，没有源码正文或本地绝对路径 |
| 后端 MySQL | `ai_run` | Run ID、用户、会话、工作区、客户端消息 ID、状态、开始结束时间、错误摘要；用户与客户端消息 ID 组合唯一 |
| 后端已有会话 | 绑定工作区 | 编码会话绑定一个工作区；切换项目时进入对应会话 |
| 客户端已有 SQLite | 工作区绑定 | 用户、工作区 ID、本地真实根目录、展示名称；工作区 ID 与用户组合唯一 |
| 客户端已有 SQLite | 工具执行记录 | 用户、工作区、Run、工具 ID、参数摘要、执行状态与结果摘要；同工具 ID 不重复执行副作用 |
| 客户端私有目录 | 改动快照 | 运行与文件对应的原始字节、修改后哈希、文件原先是否存在；SQLite 仅记录快照索引与状态 |

后端运行中的工具 Future 继续留在内存。相对文件列表可以重新扫描恢复，阶段 A 不必给每一行源码建立数据库记录。

### 03.2 修改工作区注册流程

```text
Electron 选择目录并扫描
    → 后端登记工作区归属，返回 workspaceId
    → 主进程将此前用户选择的目录绑定到该 ID，并持久化
    → 后续消息和工具都携带 workspaceId
```

客户端重启后，按当前用户加载绑定并校验真实目录，再向后端刷新清单。不要根据同名目录自动推断为同一个工作区，也不要接受 SSE 请求里临时指定的任意本地绝对路径。

过渡期可以保留 `workspaceName` 展示，但查找、权限和工具回传都逐步改用 ID。`workspace_service.py` 的内存键、`build_workspace_tools()` 的上下文、前端选中工作区类型需要一起迁移。

### 03.3 为源码读取增加版本信息

当前 read 返回带行号的字符串。建议改成原始选中内容加结构化元数据，示例：

```json
{
  "runId": "run-xxx",
  "workspaceId": "workspace-xxx",
  "toolCallId": "tool-xxx",
  "success": true,
  "content": "def valid_username(name: str) -> bool:\n    return len(name) >= 3",
  "data": {
    "path": "app.py",
    "contentHash": "实际文件字节的 SHA-256",
    "startLine": 1,
    "endLine": 2,
    "totalLines": 2,
    "truncated": false
  }
}
```

哈希使用本次读取的整文件原始字节计算，展示行号另行添加。不要对格式化后的 `1: xxx` 或只读到的片段计算“整文件版本”。

**增加一个 `data` 字段，需要检查整条链路**：

```text
fileInfo.js 的读取结果
    → agentApi.ts 的执行结果类型
    → Ai-chat.vue 提交 tool/result 时带上 data
    → app/schemas/tool.py 声明 data
    → app/api/workspace.py 转交 data
    → ToolCallResult / PendingToolCall 处理数据与归属
    → read 工具记录版本并把必要内容交给模型
```

当前前端只提交 `success/content`，后端也只接收这些文本字段；不能只在 Electron 返回哈希，就认为后端已经拿到了它。

可以在每次 `build_workspace_tools()` 创建的本轮工具闭包中记录 `relative_path → 最后一次读取哈希`。修改工具从这里取得预期版本，而不是相信模型自己编出的哈希。

### 03.4 身份和并发边界

- 对外部署前从可信登录态取得当前用户，不让请求体的 `userId` 决定权限。与网关约定验证令牌或可信身份注入，并阻止伪造内部身份头。
- Run 创建时检查会话、工作区归属；工具回传再核对用户、工作区、Run 与工具 ID。
- 同一个工作区先只允许一个活跃编码 Run；服务总并发也有上限，满时明确提示忙碌。
- 重复客户端消息返回已有 Run；用户主动重新执行应产生新的客户端消息 ID，不盲目重放上一次操作。

**本章完成标准**：同名目录可区分；换用户不会复用别人目录；重启能恢复工作区；一次读取的哈希确实抵达后端；重复提交不会重复建运行。

## 04. 文件修改：先有可核查的 diff，再落盘

### 04.1 先沿用精确文本替换

第一版不实现通用补丁引擎。继续使用已有 `edit_workspace_file(path, old_text, new_text)`，默认要求旧文本只出现一次。

修改目标为：

```python
def valid_username(name: str) -> bool:
    return 3 <= len(name) <= 20
```

修改工具还需要取得第 03 章记录的读取版本。对未读过的已有文件拒绝直接覆盖；新建文件则使用“原本不存在”的明确条件。

### 04.2 客户端拆成准备与应用两个步骤

建议在 `fileInfo.js` 附近增加准备、应用和撤销函数，工具仍然是一条 edit 请求，内部按步骤处理：

1. **准备**：校验预期哈希，按精确替换计算候选内容，生成 diff，但不写文件。
2. **展示与授权**：界面展示真实差异；本轮已授权范围内可以应用，否则等待确认。用户拒绝则向后端返回明确拒绝结果。
3. **再次检查**：应用前重新读取当前文件并核对哈希，避免用户在看 diff 时修改了文件。
4. **保存与应用**：先保存快照，再写候选内容；成功后记录新哈希与状态。
5. **回传**：返回实际修改文件、前后哈希、改动摘要，不仅返回“操作成功”。

新增 IPC 可表达为 `workspace:prepare-change`、`workspace:apply-change`、`workspace:revert-change`。这是建议名称；关键在于 prepare 不落盘，apply 使用已准备的提案并重新校验，不允许界面换一份未经检查的内容直接写入。

diff 的算法使用成熟文本 diff 实现，不自己写复杂比较算法。先做普通增删行展示，语法高亮和双栏编辑器不作为前置条件。

### 04.3 撤销只恢复本轮受控改动

快照目录可安排为：

```text
Electron userData/
  local.db
  agent-changes/
    用户标识/
      工作区标识/
        运行标识/
          快照文件与索引
```

实际快照文件名由程序生成，原始相对路径记在索引中，不将不受控的路径直接拼成快照写入地址。

同一文件一轮修改多次，保留本轮最初字节与最后一次写入哈希。撤销时：

- 当前哈希等于本轮最后写入版本：恢复快照；新文件可以移除。
- 当前哈希已变化：报告冲突，保留用户后来修改的内容。
- 多文件部分失败：列出已经修改的文件，按文件提供恢复，不假装整个批次具有数据库事务。

已有删除能力仍使用回收站；受控文本文件的恢复还应依赖快照。阶段 A 不增加批量目录删除和二进制修改，也不通过整仓库 reset 撤销用户改动。

**本章完成标准**：先在演示项目修改一行，检查 diff 与原文件一致；再验证拒绝不写、哈希变化不覆盖、正常撤销可恢复、人工再次修改后撤销会报告冲突。

## 05. 命令执行：让 Agent 知道改得对不对

### 05.1 先只运行演示项目测试

第一条命令固定为客户端的 `python -m unittest -v`，工作目录是演示工作区。先在本地终端确认 Python 能被找到，再接入 Electron；服务器 Python 路径不能代替用户电脑的解释器路径。

建议新增客户端 `src/main/File/workspaceCommand.js`，主进程通过异步子进程运行，基础启动方式为：

```javascript
import { spawn } from 'node:child_process'

const child = spawn(executable, args, {
  cwd: resolvedWorkspaceDirectory,
  windowsHide: true,
  shell: false,
  stdio: ['ignore', 'pipe', 'pipe']
})
```

这是启动片段，**还不是完整命令工具**。完整实现还需要：

| 环节 | 必须完成 |
|---|---|
| 输入 | 工作区 ID、程序、参数数组、相对工作目录、超时；客户端解析目录并检查授权 |
| 启动 | 登记 Run、工具 ID 与进程标识；处理程序不存在的 `error` |
| 日志 | stdout/stderr 有界保存，并经本地 IPC 展示；多字节文本按流正确解码 |
| 结束 | 等待进程与输出关闭，返回真实退出码、耗时、是否截断 |
| 超时与取消 | 处理对应进程树并确认退出，不能只结束等待 Promise |
| 回传 | 输出摘要与结构化退出状态共同传给模型 |

普通可执行程序使用参数数组，避免拼 Shell 文本。Windows 的 `.cmd/.bat` 需要显式平台处理，接入 npm 时再覆盖该分支，不能假设所有程序都能按相同方式启动。`cwd` 也不是安全沙箱。[Node 子进程文档](https://nodejs.org/api/child_process.html)

### 05.2 增加后端命令工具

在 `build_workspace_tools()` 中新增 `run_workspace_command`，仍经同一个 dispatcher 下发 `operation: command`，客户端执行后通过同一个 `/tool/result` 回传。

命令结果示例：

```json
{
  "success": false,
  "content": "test_too_long 失败：期望 False，实际 True",
  "data": {
    "exitCode": 1,
    "timedOut": false,
    "cancelled": false,
    "truncated": false
  }
}
```

`success` 表示验证是否成功，非零退出必须保留具体输出。模型不能把“进程创建成功”或输出里偶然出现 success 当成测试通过。

### 05.3 修正循环要有上限

将失败测试结果回给同一个 Agent，使它重新读取、修改、再验证。模型调用次数、工具次数和执行总时长应由代码限制，不能只在提示词里要求节制；用户确认等待与真正执行的超时分别处理。

上限触发时结束运行并展示已发生的改动与未解决问题，不能加一轮无界重试或修改断言制造通过。

**本章完成标准**：先故意运行原始演示项目看到失败；修复后看到原有测试通过，并增加 3、20 的边界测试；再用不存在程序、非零退出和超时场景验证错误回传。

## 06. 运行状态、停止与重连

### 06.1 只引入一份最小 Run

Run 表先使用 `running / completed / failed / cancelled / interrupted`。等待工具或等待确认显示为步骤，不急着扩展大量状态。

成功保留 `message.completed` 后发 `done`；失败发 `run.failed`；取消增加 `run.cancelled`。三种运行终态互斥，前端不能继续要求失败或取消也必须收到成功的 `done`。

运行正常结束只说明流程结束；最终界面还应展示测试是否通过、实际验证范围和未解决问题，不把模型答完当作需求已验收。

### 06.2 停止按钮同时通知两端

```text
用户点击停止
    → 客户端拒绝尚未开始的本轮工具，撤销待确认提案
    → HTTP 请求后端取消 runId
    → 主进程终止本轮可中止的命令并核查状态
    → 后端取消模型与等待任务，在 finally 清理
    → 更新 Run 终态；必要时再中断 SSE
```

建议新增 `POST /ai-message/run/{runId}/cancel` 与 `GET /ai-message/run/{runId}`。取消请求不依赖 SSE 通道；已经写入的内容保留在改动列表，只有用户撤销才恢复。

当前 `AIMessageStream.js` 会 `await onToolRequest()`。命令和确认接入后，长操作应登记为独立客户端任务，SSE 继续消费；异常要被任务管理逻辑接住并回传，不能变成无人处理的 Promise。

**Electron IPC 只暴露明确能力**，校验调用来源、当前用户与工作区绑定。可以沿用 preload 的有限方法模式，不开放任意命令字符串入口给不受控页面。[Electron IPC 文档](https://www.electronjs.org/docs/latest/tutorial/ipc)

### 06.3 异常后的实际状态

- 服务重启：原运行标记为 `interrupted`，Future 不存在时不要假装继续从原位置执行。
- 客户端断线：查询 Run；核查本地实际文件与进程状态后再重试。
- 重复工具请求：已完成的返回原结果；只知道曾经开始的写入或命令先标记状态不确定，核查副作用，不自动重放。
- 迟到结果：记录实际执行情况，但不能让已经取消的 Agent 继续下一步。
- 无法确认命令是否停止：界面明确显示未知状态，不能声称全部停止。

**本章完成标准**：等模型、等确认、等文件工具、命令运行中都能停止后续执行；断线与重启不会假报成功，也不自动重放可能已有副作用的命令。

## 07. 文件与接口的实施清单

这里的新增文件是建议落点，实际实现保持现有风格，不要求为小功能建立新框架。

| 阶段 | 后端主要入口 | 客户端主要入口 | 应交付的结果 |
|---|---|---|---|
| 00 | `main.py`、`core/lifespan.py` | 原有界面与工作区注册 | 已有读文件链路可重复演示 |
| 01 | `agent/tools/workspace.py`、`_workspace_system_prompt()` | 新 `workspaceSearch.mjs`、`fileInfo.js`、`agentApi.ts`、`Ai-chat.vue` | 搜索与读取真实代码 |
| 02 | `ai_message_service.py`、`message_repository.py` | 已有会话输入和展示 | 多轮承接与隔离 |
| 03 | workspace/tool/message schema、workspace/run 服务与仓储、迁移脚本 | 本地 SQLite 表、绑定 IPC、类型和回传处理 | ID、归属、Run 与读取版本 |
| 04 | 修改工具、结果 schema、dispatcher | prepare/apply/revert、diff 组件、快照索引 | 修改可检查、冲突不覆盖、可撤销 |
| 05 | 命令工具、调用限制 | 新 `workspaceCommand.js`、命令 IPC、日志 | 真实测试闭环 |
| 06 | Run 查询、取消与 finally 清理 | SSE、停止按钮、进程清理和执行记录 | 终态与恢复行为明确 |
| 08 | 现有部署入口与可信身份 | 打包后的实际客户端 | 远程后端与本地工作区共同工作 |

接口只需沿用现有三条并增加运行查询、取消两条：

```text
POST /ai-message/sendMessage
POST /ai-message/workspace/register
POST /ai-message/workspace/tool/result
GET  /ai-message/run/{runId}             新增
POST /ai-message/run/{runId}/cancel      新增
```

增加新的 `operation` 时，逐项检查：后端工具是否注册、请求是否能下发、前端类型与标签是否识别、主进程是否实现、结果是否透传、模型是否收到。不要看到界面出现一条“工具步骤”就认定整个功能已经完成。

## 08. 联调与阶段 A 验收

### 08.1 自动化与人工分别验证

后端若引入 pytest，先添加开发依赖并编写相应测试，再执行：

```powershell
Set-Location -LiteralPath $serverRoot
uv add --dev pytest
uv run pytest tests -q
```

这些是你实施时的命令，当前项目未发现已有 pytest 测试，不能把“目录存在”当作已有回归。后端测试用假模型、假工具或独立数据库，不依赖真实模型调用才可重复验证规则。

| 检查 | 至少覆盖 |
|---|---|
| 搜索 | 命中、无命中、忽略目录、无效编码、边界数量、路径范围 |
| 上下文 | 当前消息不重复、第二轮承接、不同会话隔离、失败消息不冒充完成 |
| 归属 | 其他用户或工作区的 Run、工具结果不能被接收 |
| 修改 | 无匹配、多匹配、哈希冲突、原本不存在的新文件、部分失败 |
| 撤销 | 正常恢复、人工再次修改不被抹掉 |
| 命令 | 成功、非零退出、程序不存在、日志截断、超时与取消 |
| 重连与去重 | 无终态断线、服务重启、重复请求、执行中断后的未知状态 |

纯函数测试、Electron IPC 回归、真实模型联调、远程部署验收分别记录。指导书代码示例通过测试，不等于你的项目已经接入并通过。

### 08.2 完整演示脚本

使用第 00 章的小项目，按顺序演示：

1. 添加目录，展示当前工作区。
2. 提问：“找到用户名校验逻辑并解释当前问题，先不要修改。”
3. 追问：“长度应为 3 到 20，修复并补边界测试，保留现有断言。”
4. 展示搜索、读取、diff 与实际落盘。
5. 展示 `python -m unittest -v` 与真实退出码，失败时观察有限修正。
6. 用独立测试场景展示冲突、撤销与停止；不要把这三种情况混进一次正常演示里掩盖结果。
7. 最终回答列出改动文件、测试结果、未解决问题。

### 08.3 服务器部署检查

- 单 Agent 服务进程起步。网关下发和工具结果回传必须到达同一持有 Future 的进程；暂不开多个 worker 来制造不可恢复的等待状态。
- 后端只使用工作区标识与相对路径，读写与命令实际发生在客户端。用户电脑离线时，本地工具不可执行，界面应明确结束等待。
- 关闭开发 reload，沿用现有网关与 Nacos 接入；实际验证身份、SSE、工具回传和正常关闭。
- 限制活跃 Run、模型/工具次数与输出量。记录空闲、对话和工具执行时的后端延迟、CPU、内存及模型用量，不宣称未经测量的高并发。
- 普通聊天与按需读取仍有模型输入成本。全项目不入库能减少后台同步与索引开销，但不能让 Agent 请求变成零成本。

**阶段 A 完成标准**：完整演示与必要异常场景均通过，并保留实际记录。此前简历采用的是“假设 A 全部完成”的目标表述，实际投递前按这些记录核对。

## 09. 阶段 B 如何加入向量检索

等 A 已经可以日常使用，再接回原先规划：添加目录后自动后台同步允许的源码，按文件分块入库；对话读取尚未入库或已过期文件时提高建索引优先级。

向量库保存的是可再生成的检索副本，文件系统仍是源码原件。后端记录文件哈希与索引版本，修改和删除同步失效；查询命中后必要时仍向客户端读取当前代码。

这样，A 的 `search/read/edit/command` 和 Run 机制都可以保留，B 只增加语义定位与后台索引链路。`project_index/indexer.py`、`retriever.py`、已有 Embedding 和 Chroma 封装到这一阶段再落实。

什么时候值得开始：已有实时搜索对一组真实开发问题表现不够好，需要跨文件语义定位；或者 A 已验收、准备补齐自动索引的完整产品目标。不要为了让模型“记住代码”而误把全量向量入库当作 A 的前置条件。

## 10. 从今天开始按这个顺序做

以下清单表达实际业务接入进度，不因为本文示例验证通过就自动勾选：

- [ ] 创建独立演示目录，确认初始一个测试故意失败。
- [ ] 复现已有“选目录—读 app.py”链路，记下断点或成功记录。
- [ ] 在客户端新增搜索函数，先通过独立测试。
- [ ] 接入 `fileInfo.js`，在 Electron 控制台手动搜索成功。
- [ ] 补前端类型和后端 Tool，让模型完成搜索与读取。
- [ ] 按第 02 章补两轮上下文，然后再进入 ID、Run 与版本结构调整。
- [ ] 按第 04—06 章完成受控修改、真实测试与停止。
- [ ] 按第 08 章完成远程部署与验收，再冻结可展示版本。

**现在先停在第 01 章的完成标准：模型找到并读取真实函数。** 先把这一步做出结果，再进入下一章；无需同时启动向量库开发、完整数据库迁移和所有 UI 改造。
