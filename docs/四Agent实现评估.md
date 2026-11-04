# 四 Agent 实现对照评估

> 评估对象：`E:\AgentProject\imAgent` 当前工作区代码。
> 对照文档：[需求分析](需求分析.md)、[架构设计](架构设计.md)、[设计文档](设计文档.md)。
> 范围：只看对话、提取、记忆、随机事件四个 Agent 及其共用机制；多模态按“后续扩展”处理，不纳入完成度结论。
> 验证方式：通读代码 + 运行测试。默认环境下 `71` 项测试通过，`27` 项需要真实 MySQL 的集成测试跳过（设置 `IMAGENT_TEST_MYSQL=1` 可跑）。

## 一、总体结论

**四个 Agent 的骨架已经成立，主链路能跑通，职责边界与三份文档基本一致。** 这不是“写了一半的 demo”：幂等、版本 CAS、租约、观察隔离、遗忘后不可恢复这些容易只写在文档里的约束，代码里都有对应实现和测试。以它作为学习基础版本是合适的。

离“初步完善”还差的地方集中在**持续性与闭环**，而不是缺模块：

| Agent | 完成度 | 一句话判断 |
| --- | --- | --- |
| 对话 Agent | ★★★★☆ | 五节点流程、沉默/拒绝/中断/重评都有；缺“延迟回复”的后续调度，且工具只走一轮 |
| 提取 Agent | ★★★☆☆ | 混合召回、版本核对、记忆强化可用；缺检索请求/结果的版本契约与查询改写 |
| 记忆 Agent | ★★★★☆ | 观察→记忆→来源→修正→淡忘闭环完整；缺“用户画像”聚合层与反思的独立流程 |
| 随机事件 Agent | ★★★☆☆ | 三阶段、无新事、生活中断、约定与结束都通了；缺“推进已有安排”分支与多步事件 |
| 共用机制 | ★★★★☆ | 角色评价与统一提交、运行协调、模型槽位、观察隔离做得扎实；`state_change` 来源经历在对话链路缺一环 |

如果只挑三件最值得先补的事：**事件 Agent 的“推进已有安排 / 多步推进”**、**对话 Agent 的“delay 后续处理”**、**评价提交补上 `source_experience_id`**。这三件补完，文档里“持续性”的主干就齐了。

## 二、四 Agent 与代码位置对照

| Agent | 文档职责 | 主要代码 | 当前状态 |
| --- | --- | --- | --- |
| 对话 | 决定回复/沉默/拒绝/分享，组织表达 | `app/agent/person/coordinator.py`、`person/dialogue/agent.py`、`person/dialogue/prompts.py` | 五节点图 + 工具决策 + 分条表达 + 中断重评 |
| 提取 | 按话题、状态、知情范围找记忆 | `person/retrieval/agent.py`、`retrieval/tools.py`、`retrieval/prompts.py`、`memory/algorithms/recall.py` | 向量 + 关键词混合召回，版本核对，唤起强化 |
| 记忆 | 整理实际获知，维护长期记忆与反思 | `person/memory/agent.py`、`memory/prompts.py`、`memory/reflection.py`、`memory_repository.py`、`maintenance_repository.py` | 观察消费、去重、修正、成长、淡忘/遗忘、生日 |
| 随机事件 | 按窗口推进生活，允许无新事 | `agent/event/graph.py`、`event/nodes.py`、`event/prompts.py`、`services/life_service.py`、`life_repository.py` | 事情产生→经历→影响，可 continue，可中断对话 |
| 共用 | 角色评价、统一提交、运行协调 | `person/appraisal.py`、`state_commit.py`、`run_service.py`、`ai_message_service.py`、`core/perception.py` | 版本 CAS、幂等键、租约、感知分级 |

## 三、逐 Agent 评估

### 3.1 对话 Agent

**已经做到的**

1. **流程与文档 3.3 对齐**：`prepare → decide →（retrieval）→ appraisal → dialogue`，条件边只在“需要旧记忆或有电脑工具”时才绕道检索（`coordinator.py`）。
2. **先判断再说话**：`decide_node` 只产出 `RetrievalDecision`，不写聊天正文；`appraisal` 产出`感受 + decision + tendency`，`dialogue` 才组织语言。这对应需求 2.4“先判断感受、意愿和表达倾向，再形成自然语言”。
3. **沉默/延后/离开不是故障**：`dialogue_node` 在 `decision != reply` 时只 `settle_turn`，任务记为 `succeeded`，SSE 正常 `done`（`ai_message_service.py`）。
4. **等待短句**：检索 1.5 秒未返回且评价前已获批 `waiting_text` 时，先入库再发送，key 为 `waiting:<task_id>`；检索本身 20 秒超时后退化为无记忆继续（`retrieval_node`）。对应文档 3.4。
5. **中断与迟到结果**：每 0.3 秒 `run_valid` + 本机取消信号；`append_expression` 校验租约和 `expected_version`；生活事件决定离开时会取消 `dialogue` 任务（`life_repository.commit_life_event`）。
6. **表达期间状态变化会重评**：`dialogue_node` 发现 `state_version` 变了就返回 `reappraise`，最多两次，超出则 `RUN_INTERRUPTED`。
7. **分条消息**：按空行切分、最多 4 条、逐条 `append_expression` + `emit`，先入库再发送，不会出现“说过但没记录”。

**缺口**

| 缺口 | 影响 | 位置 |
| --- | --- | --- |
| `decision="delay"` 没有后续处理 | 文档 3.3 要求“保存选择与后续处理”，现在 delay 与 silence 等价，人物不会“过一会儿再回” | `dialogue_node` / `settle_turn` |
| 工具只走一轮 | 一次交流里无法“先列目录再读文件”；检索结果也不会回到模型再决定是否继续查 | `retrieval_node` |
| 工具调用是显式编排而非 LangChain Agent 循环 | 模型创建统一走 `llm/factory.create_chat_model`，工具由 `retrieval_node` 按决策执行；这是有意的选择（便于校验），但与“LangChain 负责工具调用”的表述需要对齐 | `dialogue/agent.py` |
| 检索结果未带 `revision` | 评价拿到 id/content，但没有版本引用；文档 3.4 的 `RetrievalResult` 要求 memoryId + revision | `retrieval/agent.py` |

### 3.2 提取 Agent

**已经做到的**

- 同时走 MySQL 全量候选（`active/faded`、未过期）与 Chroma 语义检索，`revision` 不一致的向量命中会被丢弃（`retrieval/agent.py`）。
- 打分是“相关性 0.7 + 重要性 0.15 + 记忆温度 0.15”，温度用半衰期公式，重要性越高、被唤起越多，淡忘越慢（`recall.py`）。
- 召回后 `reinforce` 写回 `last_accessed_at` 并 `recalls+1`，对应需求“被提醒/回想后记忆增强”。
- 返回 `epistemicType` 与 `confidence`，让评价区分“亲历/听说/推测”。
- 工具绑定 `user/world/actor`，查询本身不能改变持有人（`tools.py`）。

**缺口**

1. **没有 `RetrievalRequest/Result` 契约**：文档 3.4 要求 `requestId、baseVersion、currentObservationIds`，迟到结果要核对版本；现在只有 20 秒超时，结果本身没有版本号。
2. **没有查询改写/多次查找**：文档允许首版从“过滤、检索、筛选”做起，所以不算违约，但复杂问题（“上次那个和猫有关的事”）召回率会受限。
3. **全量加载打分**：`list_memories(limit=1000)` 后在 Python 里 O(n) 排序；记忆上千条后需要改成 SQL 预过滤 + ANN。
4. **“没找到”和“技术失败”只部分区分**：有 `found`、`mode`、`retrieval_timeout`，但工具层没有把“空结果”与“向量库异常”明确分开。

### 3.3 记忆 Agent

**已经做到的（这是四个 Agent 里闭环最完整的一条）**

1. **只整理实际观察**：`pending_observations` 取该人物未消费的 `observation`，不是原始聊天、不是世界真相。
2. **来源强制**：`MemoryBatch` 每条记忆的 `source_ids` 必须是本批观察的子集，否则拒绝；`memory_source` 同时写“记忆与观察”的关联，数据库层还有复合外键保证“记忆持有人 = 观察者”。
3. **去重与修正**：用 `fingerprint` + `contentKey` 判重；`supersedes_id` 把旧记忆置为 `superseded` 并保留历史；新来源会补挂到已有记忆上。
4. **消费标记**：整理过的观察写 `memory_consumed` 任务，即使这批没形成记忆也不会反复整理；并发整理时有“已消费则返回”的保护。
5. **成长保守**：`growth` 至少引用 3 个不同观察，且 7 天内只允许一次（`reflection.py`），避免一句话重写人格。
6. **淡忘与遗忘**：`maintain_world` 按热度把记忆置 `faded`；只有“重要性 < 0.15 且热度接近 0”才 `forgotten` 并清空正文。
7. **遗忘不可回捞**：`snapshot`、`begin_turn` 的历史裁剪、`pending_observations` 都排除与 `forgotten` 记忆关联的观察/消息。这是文档反复强调、也最容易写漏的一条，代码里确实做了。

**缺口**

| 缺口 | 说明 | 位置 |
| --- | --- | --- |
| `subject_actor_ids_json` 写成了自己 | 文档说这是“涉及的其他人”，现在固定 `[actor_id]`，按人检索会失真 | `memory_repository.save_batch` |
| 没有独立反思流程 | 需求 2.6 的“反思、成长”目前主要靠 `MemoryBatch.growth`；“发现自己误解/失约”没有专门 prompt 或触发 | `memory/` |
| 没有“用户理解”聚合层 | 每次都是原始记忆条目参与召回，没有“用户偏好摘要/画像”这种高层认知 | `memory/` |
| 整理任务重复投递 | 一条回复会入队 `message:<id>`、`turn:<task>`，同一批观察只有第一个任务有效，其余空转 | `append_expression` / `settle_turn` |

### 3.4 随机事件 Agent

**已经做到的**

1. **三阶段与文档 4.2 一致**：`generate`（事情产生）→ `experience`（事件经历）→ `impact`（最终影响）；`continue` 直接结束，不写任何经过（`event/graph.py`）。
2. **无新事是合法结果**：没有合理外因时返回 `continue`，不强制每个窗口造剧情。
3. **伙伴独立发言**：`experience` 逐位参与者生成对话，每人只拿自己的资料、自己的观察和现场已说的话；不在参与者里的角色不会“知道”事件（`event/nodes.py`）。
4. **影响与表达分开**：`impact` 产出 `Appraisal`、主动消息、朋友圈、受众、评论与图片提示，统一由 `commit_life_event` 落库；主 AI 未获知的事件不产生心理影响。
5. **生活可以打断对话**：`appraisal.decision == "leave"` 或手机不可用/睡着时，取消 `dialogue` 任务、结束通话、让本机取消信号生效（`life_repository`）。
6. **调度与停机**：`schedule_lives` 按用户 `lifeWindowMinutes` 入队；`event_end` 用 `run_after` 按真实时长结束；`resume_worlds` 把计划整体平移停机空档，不补算空档内的故事。
7. **约定与共同活动**：`activity_repository.schedule` 只在 `mutually_agreed` 时建 `shared_activity`，到期由 `appointment` 任务处理；用户不在线或拒绝时记为 `missed`，不假装一起做过。

**缺口**

| 缺口 | 影响 | 位置 |
| --- | --- | --- |
| `generate` 只有 `continue` / `event` 两种输出 | 文档 4.1 的“推进已有安排”（看完一章书、约定时间到）没有对应分支；只能靠结束任务或约定任务，不能由事件 Agent 主动推进 | `event/nodes.py` |
| 事件只有“开始/结束”两步 | `next_step_at` 写了但除恢复平移外没有调度器消费；多步事件（中间推进）未实现 | `life_repository` / `task_repository` |
| 版本冲突时整轮重跑 | `commit_life_event` 抛 `STATE_CHANGED` 后 worker 重试整轮，会重新 generate/experience，剧情可能变；幂等键保证不重复写，但语义上是“重掷” | `background_service` |
| 伙伴关系只有 label | `assessment_json` 固定 `'{}'`，伙伴之间没有信任/亲近的增量维护 | `life_repository` |
| `routine_json` 被整体覆盖 | 事件提交时写成 `{"sleeping": ...}`，会丢掉精力等字段 | `life_repository` |

### 3.5 共用机制

- **角色评价**（`appraisal.py`）：一份提示词覆盖对话、事件、约定、互动四种刺激，明确“已生效的影响不重复叠加”“goal_changes 只接真实进展”“工具文本不具备系统权限”。
- **统一提交**（`state_commit.commit_appraisal`）：锁顺序、租约、幂等键、CAS、`state_change` 流水、关系增量 clamp（±0.15 / ±0.1）、目标去重、约定排期。这块是全项目最值得学的部分。
- **运行协调**（`run_service.py` + `message_repository.run_valid`）：本机取消只是优化，最终防线是数据库租约；SSE 每 0.3 秒校验一次。
- **感知**（`core/perception.py` + `begin_turn`）：`read / notification / unnoticed` 三级；未读时历史清空、不写 AI 观察、只写“有通知”的经历；睡眠唤醒用消息 ID 做稳定随机。
- **模型槽位**（`model_config_service.resolve_model`）：ENV 专用槽位优先；用户只能覆盖 `dialogue`；`memory/retrieval/event` 未单配时回落到无用户的服务端默认。
- **隔离**：查询强制 `world_id + owner_actor_id`；25 表用复合外键把“跨世界写入”挡在数据库层；`observation` 是人物唯一的知情面。

**共用机制里的一个明确缺口**：对话链路的 `commit_appraisal` 没有传 `experience_id`，所以 `state_change.source_experience_id` 为 `NULL`。文档要求“变化必有来源”，目前只有事件、互动、约定三条链路满足。修法很简单：`appraisal_node` 把用户消息的 `source_experience_id` 传进去。

## 四、与需求 7.2 九个验收场景的对照

| 场景 | 现状 | 说明 |
| --- | --- | --- |
| 1 生活影响交流 | ✅ 基本满足 | 事件→评价→情绪/关系→下次对话承接，有集成测试 |
| 2 多模态信息进入核心机制 | ⏸ 扩展项 | 按你的要求不评估 |
| 3 隐瞒与真实状态分开 | ✅ | `message`/`experience` 记录“说过”，`routine_json` 保持真实；`intentional_lie` 只影响表达 |
| 4 安排改变有后果 | ⚠️ 部分 | 约定、共同活动、`missed` 有；目标进度与聊天邀请的联动较弱 |
| 5 跨渠道与时间连续 | ✅ 文字/朋友圈/事件/通话 | 多模态效果不在本次范围 |
| 6 平淡生活与活动连续 | ✅ | `continue` 是合法结果，有测试 |
| 7 回忆途中被叫走 | ✅ | 等待短句 + 生活中断 + 迟到结果拒绝，`test_person_mysql` 有专项 |
| 8 手机与现场影响回应 | ✅ 基本 | 未察觉/通知/已读分级；未读正文不进入评价 |
| 9 双端接续 | ✅ 服务端统一 | 客户端实现不在本仓库范围 |

## 五、建议的补齐顺序

**P0（主干闭环，建议先做）**

1. 事件 `generate` 增加 `action="advance"`：允许“继续当前活动 / 推进已有安排 / 新事件”三选一，对齐文档 4.1。
2. 让 `next_step_at` 真正被调度：给 `life_event` 加一个扫描任务，支持事件的中间推进，而不是只有开始与结束。
3. `delay` 的后续处理：`settle_turn` 时若 `decision == "delay"`，入队一个带 `run_after` 的 `pending_reply`，到点重新评价。
4. 对话评价提交补 `source_experience_id`。

**P1（质量与一致性）**

5. 检索工具返回 `memoryId + revision`（以及可选 `requestId`），迟到结果可核对。
6. 一次交流支持多轮工具决策（retrieval 后回到 decide，设上限）。
7. 记忆整理任务合并为单一去重键，减少空转。
8. `subject_actor_ids_json` 改为“涉及的其他人”；伙伴关系维护最小 `trust/closeness`。
9. 事件提交冲突时只重做 `impact`，不重掷整个剧情。

**P2（规模与表达）**

10. 记忆召回改成 SQL 预过滤 + ANN，避免全量加载。
11. 增加“用户理解摘要”层，把多条记忆压成画像，再参与召回。
12. 反思独立成流程（误解、失约、道歉、改正）。
13. 多模态按需求文档继续补齐（不在本次结论内）。

## 六、如果以这个项目为基础学习，建议的阅读顺序

1. **数据模型**：设计文档第二节 25 张表 + 附录 SQL。先接受“experience / observation / memory 三层收窄”这个前提。
2. **一次对话**：`api/ai_message.py` → `ai_message_service.prepare_reply / stream_reply` → `message_repository.begin_turn` → `coordinator.py` 五个节点。
3. **状态写入**：`person/appraisal.py` → `state_commit.commit_appraisal` → `state_change`。重点看幂等键和 `before_version/after_version`。
4. **记忆**：`memory/agent.py` → `memory_repository.save_batch` → `retrieval/agent.py` → `vector_store/memory_store.py`。
5. **生活**：`task_repository.schedule_lives` → `background_service.execute` → `life_service.advance_life` → `event/*` → `life_repository.commit_life_event`。
6. **验证**：先读 `tests/test_person_mysql.py::test_four_agents_execute_together...`，它把四个 Agent 串成了一条可执行的说明。

> 本文是代码对照评估，不是设计文档的一部分；结论以当前工作区代码为准，随代码演进需要更新。
