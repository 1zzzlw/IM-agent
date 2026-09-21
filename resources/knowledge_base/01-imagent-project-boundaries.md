---
knowledge_id: imagent-project-boundary
title: imAgent 项目边界
tags: [imAgent, architecture, electron, java, python]
---

# imAgent 项目边界

## 系统定位

imAgent 是现有 IM 微服务体系中的 Python AI 服务。它负责 AI 会话、模型调用、Agent 编排、知识检索和工具请求，不替代 Java IM 服务已有的账号、好友、群组、普通消息和 Netty 通信能力。

## 三端职责

### Electron 客户端

- 展示 AI 工作台、会话历史、工具状态和流式内容。
- 保存用户工作区在本机的绝对根路径。
- 在用户电脑上执行受控的文件读取、写入、编辑和删除。
- 将文件工具执行结果回传给 imAgent。

### imAgent 服务

- 接收经过网关转发的 AI 请求。
- 管理 AI 会话、消息和模型配置。
- 调用聊天模型和 Embedding 模型。
- 决定是否调用文件工具，并等待 Electron 返回结果。
- 管理系统知识库和按用户、工作区隔离的项目代码索引。

### Java 微服务

- 负责认证、网关、普通 IM 业务和稳定的基础设施能力。
- 网关校验身份并将可信用户信息传给 imAgent。
- 不应和 imAgent 同时写同一份 AI 业务数据。

## 远程工作区原则

imAgent 部署在服务器后，不能直接读取用户电脑上的绝对路径。服务器只保存工作区标识、相对路径、索引内容和工具调用状态。真实绝对路径只保存在 Electron 本地。

如果用户要求分析文件，应先使用项目索引定位候选文件，再通过 `read` 工具获取客户端磁盘上的最新内容。如果用户要求修改文件，应由 imAgent 生成受控工具请求，由 Electron 完成实际写入。

## 数据隔离

工作区和索引至少使用 `user_id + workspace_id` 隔离。`workspace_name` 只用于展示，不能单独作为全局唯一标识。检索时必须同时带上当前用户和当前工作区过滤条件。
