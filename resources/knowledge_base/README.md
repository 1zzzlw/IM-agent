# imAgent 系统知识库

本目录保存 imAgent 编程 Agent 可检索的工程知识。内容用于帮助 Agent 理解项目、定位问题、规划修改和选择工具，不保存用户工作区的项目代码。

## 使用边界

- 本知识库属于系统知识，所有用户和工作区可以复用。
- 用户项目代码应进入独立的项目代码索引，不能写入本目录。
- 检索结果只能作为上下文，不能替代对最新文件的 `read`。
- 路径安全、密钥保护、写入审批等强制规则必须同时写入系统提示和程序校验，不能只依赖 RAG。
- 文档描述的是通用工程判断。具体修改前，仍要以当前仓库代码、配置和测试结果为准。

## 文档清单

| 文档 | 主要用途 |
|---|---|
| `01-imagent-project-boundaries.md` | 识别 imAgent、Java 微服务和 Electron 的职责 |
| `02-coding-task-workflow.md` | 从理解需求到验证结果的标准流程 |
| `03-workspace-file-tool-rules.md` | 选择 list/read/write/edit/delete 工具 |
| `04-code-retrieval-and-context.md` | 使用项目索引定位文件并构建上下文 |
| `05-technology-stack-troubleshooting.md` | Python、FastAPI、Vue、Electron、Java 常见排查入口 |
| `06-security-and-change-boundaries.md` | 文件、命令、密钥和用户隔离规则 |
| `07-verification-and-reporting.md` | 选择验证方式并如实报告结果 |

## 推荐元数据

入库时至少为每个分块附加以下元数据：

```text
source: 相对于 knowledge_base 的文档路径
knowledge_type: system
title: 文档标题
section: 当前标题路径
content_hash: 文档内容哈希
```

不要把 API Key、访问令牌、用户代码或用户绝对路径写入元数据。
