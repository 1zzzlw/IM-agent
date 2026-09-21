---
knowledge_id: code-retrieval-context
title: 代码检索与上下文构建
tags: [rag, retrieval, chroma, embedding, context]
---

# 代码检索与上下文构建

## 检索的职责

项目代码索引用于从大量文件中定位与问题相关的候选片段。它不能证明片段仍是最新版本，也不能替代真实文件读取。

系统知识库与项目代码索引必须分开：系统知识跨项目复用，项目索引按用户和工作区隔离，二者的更新频率和访问边界不同。

## 索引数据

每个项目代码分块至少应保存：

```text
chunk_id
user_id
workspace_id
relative_path
language
content_hash
chunk_index
start_line
end_line
content
embedding
```

`chunk_id` 应稳定且可重复计算，可以由工作区、相对路径、内容哈希和分块序号共同生成。相同文件重新索引时使用 upsert，文件删除或内容变化时移除旧分块。

## 检索流程

```text
用户问题
→ 生成查询向量
→ 使用 user_id 和 workspace_id 过滤
→ 获取 Top K 候选片段
→ 合并重复或相邻片段
→ 将路径、行号和内容提供给模型
```

检索结果应保留来源路径和行号。不要只把纯文本交给模型，否则模型无法指出依据，也难以继续调用文件工具。

## 上下文控制

优先保留直接相关、来源不同且信息互补的片段。不要因为 Top K 设置很大就把大量重复代码放进提示词。若检索结果不足，应使用路径搜索、关键词搜索或目录列表补充，而不是假设不存在相关实现。

## Embedding 一致性

同一 Collection 的文档向量和查询向量必须来自兼容的 Embedding 模型。Embedding 的提供商、模型名称、服务地址或向量维度发生变化时，应视为索引兼容性变化，创建新 Collection 或重建旧索引。

聊天模型可以按用户配置切换，项目索引的 Embedding 模型应保持服务级稳定。
