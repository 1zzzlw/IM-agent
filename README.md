# Lumina-Agent · 微光

面向长期相处的个性化虚拟人物 Agent。对话、提取和记忆共同维护一个人物，生活事件在后台独立推进。

已接入的首版链路：

- 生成完整人物候选，只展示表面介绍；确认后拥有独立背景、往事、伙伴和目标。旧基础角色可完成首次正式创建，保留世界与聊天。
- 对话按需调用记忆工具，先评价感受与回应意愿，再表达；支持沉默、延迟、拒绝、有动机的隐瞒、分条消息和等待短句。
- MySQL 保存权威记忆与观察来源，Chroma 提供可重建语义索引；支持纠正、强化、淡忘与保守的成长整理。未配置 Embedding 时使用关键词召回。
- 持久后台任务推进生活、关系、目标和约定；允许没有新事件，支持手机感知与生活中断，旧任务不得发送迟到回复。
- 朋友圈按显式受众显示，支持用户点赞评论和伙伴评论。多模态使用配置的识图、声音理解、图片生成、语音识别与合成模型。
- 桌面端与 Flutter 端使用同一个人物、聊天、配置和朋友圈；语音通话使用连续音频轮次、插话作废和分句播出确认。

以上是首版实现范围。真实人物体验、多模态供应方适配和长期运行质量需要继续验证，不等同于已经达到真人效果。

## 运行

保留现有 25 张 `imagent_` 表，不需要重建。新数据库只执行一次 [初始化 SQL](docs/sql/imagent_init.sql)，不要删除普通 IM 表。

```bash
uv sync
uv run python main.py
```

MySQL 必须可用。通过现有 IM 网关访问时使用 Nacos 注册；单独启动可设 `NACOS__ENABLED=false`。Redis 默认不作为必需依赖。角色在后台服务运行时生活，关掉客户端不会停止，停止服务后不会补算空档内的事件。

模型主要通过项目根目录 `.env` 配置，模板见 [.env.example](.env.example)。已有 `.env` 时保留原值、补充需要的项；只填写 `MODELS__DIALOGUE__MODEL`、`MODELS__DIALOGUE__BASE_URL`、`MODELS__DIALOGUE__API_KEY`，四个 Agent 就能共用默认模型。可用 `MODELS__MEMORY__MODEL` 等为各 Agent 单独指定模型，多模态按 `vision`、`image`、`asr`、`tts`、`audio_emotion` 分别配置。修改后重启后端。

桌面端在 `WeChat-Electron-vue-vite` 运行 `npm run dev`；手机端在 `im_app` 运行 `flutter run`。配置好 `.env` 后可直接创建人物，不必先在界面保存模型。用户只切换对话 Agent 的语言模型；提取、记忆、事件及图片、语音能力使用固定服务端配置，不随之切换。完整步骤与模型能力约定见 [运行与体验](docs/运行与体验.md)。

## 文档

- [需求分析](docs/需求分析.md)
- [架构设计](docs/架构设计.md)
- [设计文档：表结构、当前接口与完整 DDL](docs/设计文档.md)
- [代码导读：目录地图、调用链与阅读顺序](docs/代码导读.md)
- [运行、模型配置与体验检查](docs/运行与体验.md)
- [资源目录与心理知识说明](resources/README.md)

## 检查

```bash
uv run python -m unittest discover -s tests -p "test_*.py"
```

`IMAGENT_TEST_MYSQL=1` 开启独立测试库验证；测试只创建和删除随机命名的测试库。`IMAGENT_TEST_LIVE_MODEL=1` 额外调用当前默认模型的小样本测试。普通测试、模拟模型的事务验证、真实供应方调用和人工体验分别记录。
