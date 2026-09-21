---
knowledge_id: stack-troubleshooting
title: 技术栈排查指南
tags: [python, fastapi, vue, electron, java, troubleshooting]
---

# 技术栈排查指南

## FastAPI 与 Python

请求问题按以下顺序排查：路由是否注册、请求模型字段及别名是否匹配、Service 是否执行、异常是否被转换、响应是否符合契约。应用资源应在 lifespan 中初始化和释放，阻塞式 MySQL、Redis 或本地数据库操作不应直接长期占用事件循环。

出现异步错误时，要区分普通函数、协程和异步迭代器。协程需要 `await`，异步流需要 `async for`。不要把字典直接传给只接受字符串或消息列表的 ChatModel。

## MySQL

单条 SQL 可以通过公共 CRUD 方法借用连接。多条 SQL 必须一起成功或失败时，应取得同一个连接，在同一个事务内执行并统一 commit 或 rollback。连接池环境中的 `connection.close()` 通常表示归还连接。

## Redis

创建 ConnectionPool 和 Redis 客户端不等于已经成功连接，主动调用 `ping()` 才能验证。关闭时释放客户端和连接池，并将对象引用清空。日志应输出原始异常堆栈，不能只抛出没有原因的通用错误。

## Vue 与 TypeScript

界面问题先区分状态、组件和样式：Pinia 保存跨组件状态，组件内部 `ref` 保存局部交互状态，API 模块负责通信。重复会话项应由类型化数组循环渲染。弹层要检查定位参照、层级、点击外部关闭、键盘操作和窗口边界。

TypeScript 报错时先确认属性类型是否符合 Vue DOM 类型，例如动态 style 可使用 `CSSProperties` 明确标注。不要用 `any` 隐藏可以准确表达的数据结构。

## Electron

渲染进程不应直接获得不受限制的 Node 文件系统能力。文件选择和真实读写放在主进程，通过 preload 暴露受控 API。主进程必须重新校验工作区路径，不能只相信渲染进程传入的数据。

## Java 微服务与网关

跨服务问题依次检查：客户端请求路径、网关路由、服务发现名称、Nacos 实例状态、请求头透传和下游接口。HTTP 返回 200 只能证明请求被接口接受，不能证明 SSE 后续执行一定成功。

## SSE

SSE 是服务端到客户端的单向事件流。前端回传工具结果应通过独立 HTTP 请求。事件应包含稳定的事件类型、会话标识、消息标识和必要载荷，前端根据事件类型分发给不同 handler。
