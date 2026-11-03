-- imAgent AI 数据库完整初始化脚本
-- 适用于 MySQL 8.0.16+；请先在数据库客户端选中项目使用的数据库。
-- 共 25 张 imagent_ 表；不依赖旧 ai_conversation / ai_message / ai_model_config。
-- 不包含 DROP、真实用户、模型密钥或模拟人物数据；已初始化的数据库不要重复执行。

SET NAMES utf8mb4 COLLATE utf8mb4_bin;
SET time_zone = '+00:00';

CREATE TABLE imagent_user_space (
  user_id VARCHAR(64) NOT NULL COMMENT '现有用户系统中的用户标识',
  active_world_id CHAR(36) NULL COMMENT '唯一在用世界；初次创建前及管理员允许重建时为空',
  settings_json JSON NOT NULL COMMENT '用户状态可见性和通知设置；不保存人格',
  settings_version BIGINT UNSIGNED NOT NULL DEFAULT 0 COMMENT '设置版本',
  next_event_seq BIGINT UNSIGNED NOT NULL DEFAULT 0 COMMENT '已分配的最后一个用户事件序号',
  event_floor_seq BIGINT UNSIGNED NOT NULL DEFAULT 0 COMMENT '已清理事件的序号水位',
  last_seen_at DATETIME(6) NULL COMMENT '客户端最近心跳；隐藏时不提供给角色',
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '创建时间',
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6) COMMENT '修改时间',
  PRIMARY KEY (user_id),
  UNIQUE KEY uq_space_active (active_world_id),
  CONSTRAINT ck_space_event_floor CHECK (event_floor_seq <= next_event_seq)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin COMMENT='用户空间与唯一在用角色入口';

CREATE TABLE imagent_model_config (
  id CHAR(36) NOT NULL COMMENT '应用生成的 UUID',
  owner_user_id VARCHAR(64) NULL COMMENT '为空表示管理员公共配置',
  name VARCHAR(100) NOT NULL COMMENT '配置名称',
  provider VARCHAR(64) NOT NULL COMMENT '模型供应方',
  model_name VARCHAR(160) NOT NULL COMMENT '模型名称',
  base_url VARCHAR(500) NULL COMMENT '自定义服务地址',
  credential_ref VARCHAR(128) NULL COMMENT '运行时凭据引用，不是 API Key',
  parameters_json JSON NOT NULL COMMENT '温度等模型参数',
  capabilities_json JSON NOT NULL COMMENT '经过配置或探测的能力描述',
  status ENUM('enabled','disabled') NOT NULL DEFAULT 'enabled' COMMENT '配置状态',
  version BIGINT UNSIGNED NOT NULL DEFAULT 0 COMMENT '配置版本',
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '创建时间',
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6) COMMENT '修改时间',
  PRIMARY KEY (id),
  KEY idx_model_owner (owner_user_id, status),
  CONSTRAINT fk_model_owner FOREIGN KEY (owner_user_id) REFERENCES imagent_user_space (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin COMMENT='可复用的公共或用户模型配置';

CREATE TABLE imagent_model_binding (
  scope_key VARCHAR(80) NOT NULL COMMENT 'system 或 user:用户标识；非空以保证公共默认唯一',
  owner_user_id VARCHAR(64) NULL COMMENT '公共默认时为空',
  slot VARCHAR(32) NOT NULL COMMENT 'dialogue/memory/retrieval/event/embedding/vision/image/asr/tts/audio_emotion',
  config_id CHAR(36) NOT NULL COMMENT '引用的模型配置',
  version BIGINT UNSIGNED NOT NULL DEFAULT 0 COMMENT '绑定版本',
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '创建时间',
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6) COMMENT '修改时间',
  PRIMARY KEY (scope_key, slot),
  KEY idx_binding_config (config_id),
  CONSTRAINT fk_binding_owner FOREIGN KEY (owner_user_id) REFERENCES imagent_user_space (user_id),
  CONSTRAINT fk_binding_config FOREIGN KEY (config_id) REFERENCES imagent_model_config (id),
  CONSTRAINT ck_binding_scope CHECK ((owner_user_id IS NULL AND scope_key = 'system') OR (owner_user_id IS NOT NULL AND scope_key = CONCAT('user:', owner_user_id)))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin COMMENT='各 Agent 与多模态能力的显式模型绑定';

CREATE TABLE imagent_world (
  id CHAR(36) NOT NULL COMMENT '应用生成的 UUID',
  user_id VARCHAR(64) NOT NULL COMMENT '世界所属用户',
  primary_actor_id CHAR(36) NULL COMMENT '唯一主 AI 人物',
  user_actor_id CHAR(36) NULL COMMENT '用户在本世界的人物投影',
  active_call_id CHAR(36) NULL COMMENT '当前通话指针',
  status ENUM('initializing','active','archived') NOT NULL DEFAULT 'initializing' COMMENT '世界生命周期',
  state_version BIGINT UNSIGNED NOT NULL DEFAULT 0 COMMENT '世界权威状态提交版本',
  next_message_seq BIGINT UNSIGNED NOT NULL DEFAULT 0 COMMENT '最后分配的世界消息序号',
  initial_snapshot_json JSON NOT NULL COMMENT '创建时的初始设定模板；管理员重置使用',
  last_tick_at DATETIME(6) NULL COMMENT '后台最后实际推进时间',
  archived_at DATETIME(6) NULL COMMENT '归档时间',
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '创建时间',
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6) COMMENT '修改时间',
  PRIMARY KEY (id),
  UNIQUE KEY uq_world_owner (id, user_id),
  KEY idx_world_user (user_id, status),
  CONSTRAINT fk_world_user FOREIGN KEY (user_id) REFERENCES imagent_user_space (user_id),
  CONSTRAINT ck_world_active CHECK (status <> 'active' OR (primary_actor_id IS NOT NULL AND user_actor_id IS NOT NULL AND primary_actor_id <> user_actor_id))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin COMMENT='一次角色生命周期对应的独立世界';

CREATE TABLE imagent_actor (
  id CHAR(36) NOT NULL COMMENT '应用生成的 UUID',
  world_id CHAR(36) NOT NULL COMMENT '所属生活世界',
  actor_type ENUM('user','virtual') NOT NULL COMMENT '用户投影或虚拟人物；主 AI 由世界指针指定',
  display_name VARCHAR(100) NOT NULL COMMENT '显示名',
  public_profile_json JSON NOT NULL COMMENT '可公开的头像和表面介绍',
  private_profile_json JSON NOT NULL COMMENT '人格、兴趣、背景、知识与技能；伙伴仅需简单性格描述',
  voice_profile_json JSON NOT NULL COMMENT '稳定声线身份、供应方映射与参考媒体',
  residence_timezone VARCHAR(64) NULL COMMENT '居住地时区，例如 Asia/Shanghai',
  age_years SMALLINT UNSIGNED NULL COMMENT '已提交的当前年龄',
  birthday_month_day CHAR(5) NULL COMMENT '可选生日 MM-DD',
  status ENUM('present','inactive') NOT NULL DEFAULT 'present' COMMENT '人物在本世界的状态',
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '创建时间',
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6) COMMENT '修改时间',
  PRIMARY KEY (id),
  UNIQUE KEY uq_actor_world (world_id, id),
  CONSTRAINT fk_actor_world FOREIGN KEY (world_id) REFERENCES imagent_world (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin COMMENT='主 AI、虚拟伙伴与用户投影的统一人物身份';

CREATE TABLE imagent_actor_state (
  world_id CHAR(36) NOT NULL COMMENT '所属生活世界',
  actor_id CHAR(36) NOT NULL COMMENT '对应人物',
  emotion_json JSON NOT NULL COMMENT '有版本的情绪状态与原因，不固定情绪维度',
  routine_json JSON NOT NULL COMMENT '真实作息、睡眠与精力状态',
  display_state_json JSON NOT NULL COMMENT '选择公开或伪装的对外状态',
  context_json JSON NOT NULL COMMENT '当前活动、关注点和其他运行上下文',
  version BIGINT UNSIGNED NOT NULL DEFAULT 0 COMMENT '最后更新时的世界状态版本',
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '创建时间',
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6) COMMENT '修改时间',
  PRIMARY KEY (world_id, actor_id),
  CONSTRAINT fk_actor_state_actor FOREIGN KEY (world_id, actor_id) REFERENCES imagent_actor (world_id, id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin COMMENT='人物的当前心理与生活状态快照';

CREATE TABLE imagent_media_asset (
  id CHAR(36) NOT NULL COMMENT '应用生成的 UUID',
  user_id VARCHAR(64) NOT NULL COMMENT '媒体所有者',
  world_id CHAR(36) NULL COMMENT '创建候选阶段可空；确认后绑定世界',
  client_asset_id CHAR(36) NULL COMMENT '用户上传重试标识',
  kind ENUM('image','audio') NOT NULL COMMENT '媒体种类',
  mime_type VARCHAR(100) NOT NULL COMMENT '实际媒体格式',
  storage_key VARCHAR(500) NOT NULL COMMENT '服务端生成的存储键，不接受任意客户端磁盘路径',
  size_bytes BIGINT UNSIGNED NOT NULL COMMENT '大小',
  sha256 CHAR(64) NOT NULL COMMENT '内容摘要',
  duration_ms BIGINT UNSIGNED NULL COMMENT '音频时长',
  metadata_json JSON NOT NULL COMMENT '图像尺寸、音频格式、声线引用及播出对齐信息',
  analysis_json JSON NOT NULL COMMENT '图片理解、转写和声音情绪线索；内部使用',
  status ENUM('processing','ready','failed') NOT NULL DEFAULT 'processing' COMMENT '媒体处理状态',
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '创建时间',
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6) COMMENT '修改时间',
  PRIMARY KEY (id),
  UNIQUE KEY uq_media_upload (user_id, client_asset_id),
  KEY idx_media_world (world_id),
  CONSTRAINT fk_media_user FOREIGN KEY (user_id) REFERENCES imagent_user_space (user_id),
  CONSTRAINT fk_media_world FOREIGN KEY (world_id, user_id) REFERENCES imagent_world (id, user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin COMMENT='图片、短语音、声线参考与生成媒体';

CREATE TABLE imagent_relationship (
  id CHAR(36) NOT NULL COMMENT '应用生成的 UUID',
  world_id CHAR(36) NOT NULL COMMENT '所属生活世界',
  from_actor_id CHAR(36) NOT NULL COMMENT '持有此关系判断的人物',
  to_actor_id CHAR(36) NOT NULL COMMENT '关系对象',
  relation_label VARCHAR(64) NOT NULL COMMENT '陌生、朋友等语义描述；不等同于数值等级',
  assessment_json JSON NOT NULL COMMENT '信任、亲近、反感等有依据的判断，具体维度待算法设计',
  contact_policy_json JSON NOT NULL COMMENT 'AI 对该对象的关心、免打扰等设置',
  last_interaction_at DATETIME(6) NULL COMMENT '最近实际互动时间',
  version BIGINT UNSIGNED NOT NULL DEFAULT 0 COMMENT '最后变更的世界版本',
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '创建时间',
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6) COMMENT '修改时间',
  PRIMARY KEY (id),
  UNIQUE KEY uq_relation_pair (world_id, from_actor_id, to_actor_id),
  KEY idx_relation_target (world_id, to_actor_id),
  CONSTRAINT fk_relation_from FOREIGN KEY (world_id, from_actor_id) REFERENCES imagent_actor (world_id, id),
  CONSTRAINT fk_relation_to FOREIGN KEY (world_id, to_actor_id) REFERENCES imagent_actor (world_id, id),
  CONSTRAINT ck_relationship_distinct CHECK (from_actor_id <> to_actor_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin COMMENT='人物间有方向的关系及联系偏好';

CREATE TABLE imagent_goal (
  id CHAR(36) NOT NULL COMMENT '应用生成的 UUID',
  world_id CHAR(36) NOT NULL COMMENT '所属生活世界',
  actor_id CHAR(36) NOT NULL COMMENT '目标所属人物',
  title VARCHAR(200) NOT NULL COMMENT '目标名称',
  description TEXT NOT NULL COMMENT '目标内容',
  status ENUM('active','paused','succeeded','failed','abandoned') NOT NULL DEFAULT 'active' COMMENT '目标状态',
  progress_json JSON NOT NULL COMMENT '阶段、进度与结果依据',
  target_at DATETIME(6) NULL COMMENT '可选目标期限',
  version BIGINT UNSIGNED NOT NULL DEFAULT 0 COMMENT '最后变更的世界版本',
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '创建时间',
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6) COMMENT '修改时间',
  PRIMARY KEY (id),
  UNIQUE KEY uq_goal_world (world_id, id),
  KEY idx_goal_actor (world_id, actor_id, status),
  CONSTRAINT fk_goal_actor FOREIGN KEY (world_id, actor_id) REFERENCES imagent_actor (world_id, id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin COMMENT='长期目标及其可持续变化的进展';

CREATE TABLE imagent_life_event (
  id CHAR(36) NOT NULL COMMENT '应用生成的 UUID',
  world_id CHAR(36) NOT NULL COMMENT '所属生活世界',
  kind ENUM('random','routine','appointment','shared_activity','learning') NOT NULL COMMENT '事件类型',
  title VARCHAR(200) NOT NULL COMMENT '事件或活动名称',
  initiator_actor_id CHAR(36) NOT NULL COMMENT '发起人物',
  participant_ids_json JSON NOT NULL COMMENT '本世界参与者列表',
  goal_id CHAR(36) NULL COMMENT '受影响的目标',
  phase ENUM('generated','experiencing','impact_applied') NOT NULL DEFAULT 'generated' COMMENT '事情产生、事件经历、最终影响',
  status ENUM('planned','running','suspended','completed','cancelled','missed') NOT NULL DEFAULT 'planned' COMMENT '生命周期',
  scheduled_start_at DATETIME(6) NULL COMMENT '计划开始',
  scheduled_end_at DATETIME(6) NULL COMMENT '计划结束',
  started_at DATETIME(6) NULL COMMENT '实际开始',
  ended_at DATETIME(6) NULL COMMENT '实际结束',
  next_step_at DATETIME(6) NULL COMMENT '下次实际推进条件对应的时间',
  progress_json JSON NOT NULL COMMENT '过程、已推进时长、选择及后续条件',
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '创建时间',
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6) COMMENT '修改时间',
  PRIMARY KEY (id),
  UNIQUE KEY uq_event_world (world_id, id),
  KEY idx_event_due (status, next_step_at),
  KEY idx_event_world (world_id, status, scheduled_start_at),
  CONSTRAINT fk_event_world FOREIGN KEY (world_id) REFERENCES imagent_world (id),
  CONSTRAINT fk_event_initiator FOREIGN KEY (world_id, initiator_actor_id) REFERENCES imagent_actor (world_id, id),
  CONSTRAINT fk_event_goal FOREIGN KEY (world_id, goal_id) REFERENCES imagent_goal (world_id, id),
  CONSTRAINT ck_event_schedule CHECK (scheduled_end_at IS NULL OR scheduled_start_at IS NULL OR scheduled_end_at >= scheduled_start_at),
  CONSTRAINT ck_event_actual_time CHECK (ended_at IS NULL OR started_at IS NULL OR ended_at >= started_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin COMMENT='随机事件、作息安排、约定与共同活动的生命周期';

CREATE TABLE imagent_experience (
  id CHAR(36) NOT NULL COMMENT '应用生成的 UUID',
  world_id CHAR(36) NOT NULL COMMENT '所属生活世界',
  event_id CHAR(36) NULL COMMENT '关联的生活事件',
  kind VARCHAR(40) NOT NULL COMMENT '初始往事、用户陈述、发言、动作、互动等',
  realm ENUM('user_report','virtual_world','shared_virtual') NOT NULL COMMENT '用户自述、模拟世界或共同虚拟经历',
  participant_ids_json JSON NOT NULL COMMENT '此条经过的参与者',
  actual_json JSON NOT NULL COMMENT '此时实际发生的经过，发言表示说过而非内容属实',
  occurred_at DATETIME(6) NULL COMMENT '发生时间；初始往事无明确日期时可空',
  recorded_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '实际记录时间',
  dedupe_key VARCHAR(160) NOT NULL COMMENT '同一条经过的稳定去重标识',
  payload_hash CHAR(64) NOT NULL COMMENT '去重时核对内容',
  PRIMARY KEY (id),
  UNIQUE KEY uq_exp_world (world_id, id),
  UNIQUE KEY uq_exp_dedupe (world_id, dedupe_key),
  KEY idx_exp_time (world_id, recorded_at, id),
  CONSTRAINT fk_exp_world FOREIGN KEY (world_id) REFERENCES imagent_world (id),
  CONSTRAINT fk_exp_event FOREIGN KEY (world_id, event_id) REFERENCES imagent_life_event (world_id, id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin COMMENT='不可改写的已发生经过，按动作或阶段追加';

CREATE TABLE imagent_observation (
  id CHAR(36) NOT NULL COMMENT '应用生成的 UUID',
  world_id CHAR(36) NOT NULL COMMENT '所属生活世界',
  experience_id CHAR(36) NOT NULL COMMENT '对应某条不可变经过',
  observer_actor_id CHAR(36) NOT NULL COMMENT '获知人物',
  source_actor_id CHAR(36) NULL COMMENT '直接来源人物',
  source_observation_id CHAR(36) NULL COMMENT '转述时的来源观察，仅供内部追溯',
  method ENUM('direct','retold','visible_post','initial_seed','notification') NOT NULL COMMENT '获知方式',
  perceived_json JSON NOT NULL COMMENT '当时实际看到或听到的内容快照',
  obtained_at DATETIME(6) NOT NULL COMMENT '获知时间',
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '写入时间',
  PRIMARY KEY (id),
  UNIQUE KEY uq_obs_world (world_id, id),
  UNIQUE KEY uq_obs_owner (world_id, observer_actor_id, id),
  UNIQUE KEY uq_obs_once (world_id, experience_id, observer_actor_id),
  KEY idx_obs_reader (world_id, observer_actor_id, obtained_at),
  CONSTRAINT fk_obs_exp FOREIGN KEY (world_id, experience_id) REFERENCES imagent_experience (world_id, id),
  CONSTRAINT fk_obs_reader FOREIGN KEY (world_id, observer_actor_id) REFERENCES imagent_actor (world_id, id),
  CONSTRAINT fk_obs_source_actor FOREIGN KEY (world_id, source_actor_id) REFERENCES imagent_actor (world_id, id),
  CONSTRAINT fk_obs_source FOREIGN KEY (world_id, source_observation_id) REFERENCES imagent_observation (world_id, id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin COMMENT='某个人物实际获知的经过与局部内容';

CREATE TABLE imagent_memory (
  id CHAR(36) NOT NULL COMMENT '应用生成的 UUID',
  world_id CHAR(36) NOT NULL COMMENT '所属生活世界',
  owner_actor_id CHAR(36) NOT NULL COMMENT '记忆持有人',
  kind VARCHAR(40) NOT NULL COMMENT '事实、经历、反思、知识等语义类别',
  epistemic_type ENUM('observed','reported','inferred') NOT NULL COMMENT '亲自获知、转述或推测',
  content TEXT NULL COMMENT '记忆正文；遗忘后可按保留策略清理',
  subject_actor_ids_json JSON NOT NULL COMMENT '相关人物',
  cognition_json JSON NOT NULL COMMENT '记忆程度、重要性、可信程度及算法版本等',
  status ENUM('active','faded','forgotten','superseded') NOT NULL DEFAULT 'active' COMMENT '记忆生命周期',
  valid_from DATETIME(6) NULL COMMENT '信息成立起点',
  valid_until DATETIME(6) NULL COMMENT '信息失效时间',
  supersedes_id CHAR(36) NULL COMMENT '被此记忆修正的旧记忆',
  revision BIGINT UNSIGNED NOT NULL DEFAULT 1 COMMENT '记忆内容版本',
  last_accessed_at DATETIME(6) NULL COMMENT '最近唤起时间',
  embedding_space VARCHAR(160) NULL COMMENT '向量模型与维度的索引命名空间',
  indexed_revision BIGINT UNSIGNED NOT NULL DEFAULT 0 COMMENT '已同步到向量库的版本',
  index_status ENUM('pending','ready','failed') NOT NULL DEFAULT 'pending' COMMENT '索引同步状态',
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '创建时间',
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6) COMMENT '修改时间',
  PRIMARY KEY (id),
  UNIQUE KEY uq_memory_owner (world_id, owner_actor_id, id),
  KEY idx_memory_recall (world_id, owner_actor_id, status, last_accessed_at),
  KEY idx_memory_index (index_status, updated_at),
  CONSTRAINT fk_memory_owner FOREIGN KEY (world_id, owner_actor_id) REFERENCES imagent_actor (world_id, id),
  CONSTRAINT fk_memory_old FOREIGN KEY (world_id, owner_actor_id, supersedes_id) REFERENCES imagent_memory (world_id, owner_actor_id, id),
  CONSTRAINT ck_memory_index_revision CHECK (indexed_revision <= revision),
  CONSTRAINT ck_memory_forgotten CHECK (status <> 'forgotten' OR content IS NULL)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin COMMENT='角色长期记忆正文、认知与检索索引状态';

CREATE TABLE imagent_memory_source (
  world_id CHAR(36) NOT NULL COMMENT '所属生活世界',
  owner_actor_id CHAR(36) NOT NULL COMMENT '记忆持有人，也是观察者',
  memory_id CHAR(36) NOT NULL COMMENT '记忆',
  observation_id CHAR(36) NOT NULL COMMENT '支持或修正记忆的观察',
  source_role ENUM('supporting','contradicting') NOT NULL DEFAULT 'supporting' COMMENT '依据作用',
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '关联时间',
  PRIMARY KEY (memory_id, observation_id),
  KEY idx_mem_source_obs (world_id, owner_actor_id, observation_id),
  CONSTRAINT fk_mem_source_memory FOREIGN KEY (world_id, owner_actor_id, memory_id) REFERENCES imagent_memory (world_id, owner_actor_id, id),
  CONSTRAINT fk_mem_source_obs FOREIGN KEY (world_id, owner_actor_id, observation_id) REFERENCES imagent_observation (world_id, observer_actor_id, id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin COMMENT='记忆与观察之间的多对多依据';

CREATE TABLE imagent_background_task (
  id CHAR(36) NOT NULL COMMENT '应用生成的 UUID',
  user_id VARCHAR(64) NOT NULL COMMENT '任务所属用户',
  world_id CHAR(36) NULL COMMENT '创建候选时可为空',
  kind VARCHAR(40) NOT NULL COMMENT 'dialogue/memory/retrieval/event/media/candidate 等',
  visibility ENUM('internal','user') NOT NULL DEFAULT 'internal' COMMENT '是否允许用户查看任务进度',
  dedupe_key VARCHAR(160) NOT NULL COMMENT '同种任务的稳定幂等标识',
  request_hash CHAR(64) NOT NULL COMMENT '输入摘要',
  payload_json JSON NOT NULL COMMENT '输入引用与阶段上下文；不含密钥',
  result_json JSON NOT NULL COMMENT '结果引用；不直接作为用户响应',
  status ENUM('queued','running','succeeded','failed','cancelled') NOT NULL DEFAULT 'queued' COMMENT '执行状态',
  run_after DATETIME(6) NULL COMMENT '最早调度时间',
  lease_owner VARCHAR(100) NULL COMMENT '领取任务的进程',
  lease_until DATETIME(6) NULL COMMENT '执行租约截止',
  attempts INT UNSIGNED NOT NULL DEFAULT 0 COMMENT '已执行次数',
  error_code VARCHAR(64) NULL COMMENT '规范化错误码',
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '创建时间',
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6) COMMENT '修改时间',
  PRIMARY KEY (id),
  UNIQUE KEY uq_task_owner (id, user_id),
  UNIQUE KEY uq_task_request (user_id, kind, dedupe_key),
  KEY idx_task_queue (status, run_after, lease_until),
  KEY idx_task_lease (status, lease_until),
  KEY idx_task_world (world_id, kind, status),
  CONSTRAINT fk_task_user FOREIGN KEY (user_id) REFERENCES imagent_user_space (user_id),
  CONSTRAINT fk_task_world FOREIGN KEY (world_id, user_id) REFERENCES imagent_world (id, user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin COMMENT='四类 Agent 及媒体、候选生成任务的执行进度';

CREATE TABLE imagent_character_candidate (
  id CHAR(36) NOT NULL COMMENT '应用生成的 UUID',
  user_id VARCHAR(64) NOT NULL COMMENT '创建者',
  batch_id CHAR(36) NOT NULL COMMENT '一次完整重新生成的批次',
  generation_task_id CHAR(36) NOT NULL COMMENT '生成任务',
  wish_text TEXT NOT NULL COMMENT '用户的简单期望描述',
  meeting_mode VARCHAR(64) NOT NULL COMMENT '选择的相识方式',
  public_preview_json JSON NOT NULL COMMENT '仅表面介绍和可试听声线候选',
  generated_bundle_json JSON NOT NULL COMMENT '初始人物、背景、伙伴和往事等完整内部设定',
  status ENUM('ready','selected','discarded','expired') NOT NULL DEFAULT 'ready' COMMENT '候选状态',
  selected_world_id CHAR(36) NULL COMMENT '选中后创建的世界',
  selection_key VARCHAR(160) NULL COMMENT '确认创建的幂等键',
  selection_hash CHAR(64) NULL COMMENT '确认请求摘要',
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '创建时间',
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6) COMMENT '修改时间',
  PRIMARY KEY (id),
  KEY idx_candidate_batch (user_id, batch_id, status),
  UNIQUE KEY uq_candidate_world (selected_world_id),
  CONSTRAINT fk_candidate_user FOREIGN KEY (user_id) REFERENCES imagent_user_space (user_id),
  CONSTRAINT fk_candidate_task FOREIGN KEY (generation_task_id, user_id) REFERENCES imagent_background_task (id, user_id),
  CONSTRAINT fk_candidate_world FOREIGN KEY (selected_world_id, user_id) REFERENCES imagent_world (id, user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin COMMENT='未正式创建的人物候选，不作为运行中的角色';

CREATE TABLE imagent_state_change (
  id CHAR(36) NOT NULL COMMENT '应用生成的 UUID',
  world_id CHAR(36) NOT NULL COMMENT '所属生活世界',
  source_experience_id CHAR(36) NULL COMMENT '触发此变化的已发生经过',
  origin_type ENUM('agent','admin','system') NOT NULL COMMENT '变化发起类型',
  origin_id CHAR(36) NULL COMMENT '内部任务或管理员操作标识',
  change_key VARCHAR(160) NOT NULL COMMENT '独立影响标识；重试时保持不变',
  request_hash CHAR(64) NOT NULL COMMENT '变化内容摘要',
  before_version BIGINT UNSIGNED NOT NULL COMMENT '依据的世界状态版本',
  after_version BIGINT UNSIGNED NOT NULL COMMENT '成功提交后的版本',
  changes_json JSON NOT NULL COMMENT '获准修改的字段、前后值与对象引用',
  appraisal_json JSON NOT NULL COMMENT '感受、理由与依据引用；仅内部可见',
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '提交时间',
  PRIMARY KEY (id),
  UNIQUE KEY uq_change_world (world_id, id),
  UNIQUE KEY uq_change_key (world_id, change_key),
  UNIQUE KEY uq_change_version (world_id, after_version),
  CONSTRAINT fk_change_world FOREIGN KEY (world_id) REFERENCES imagent_world (id),
  CONSTRAINT fk_change_source FOREIGN KEY (world_id, source_experience_id) REFERENCES imagent_experience (world_id, id),
  CONSTRAINT ck_change_version CHECK (after_version = before_version + 1)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin COMMENT='一次统一状态提交的前后版本与依据';

CREATE TABLE imagent_call_session (
  id CHAR(36) NOT NULL COMMENT '应用生成的 UUID',
  world_id CHAR(36) NOT NULL COMMENT '所属生活世界',
  caller_actor_id CHAR(36) NOT NULL COMMENT '首版仅用户投影',
  callee_actor_id CHAR(36) NOT NULL COMMENT '首版仅主 AI',
  client_call_id CHAR(36) NOT NULL COMMENT '客户端呼叫幂等标识',
  status ENUM('ringing','connected','rejected','missed','ended') NOT NULL DEFAULT 'ringing' COMMENT '通话状态',
  transport_json JSON NOT NULL COMMENT '协商后的媒体格式与连接参数，不含持久化访问令牌',
  generation_epoch BIGINT UNSIGNED NOT NULL DEFAULT 0 COMMENT '插话或结束时递增以作废旧语音输出',
  connected_at DATETIME(6) NULL COMMENT '实际接通',
  ended_at DATETIME(6) NULL COMMENT '实际结束',
  end_reason VARCHAR(64) NULL COMMENT '公开的结束原因，不保存内部心理解释',
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '创建时间',
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6) COMMENT '修改时间',
  PRIMARY KEY (id),
  UNIQUE KEY uq_call_world (world_id, id),
  UNIQUE KEY uq_call_request (world_id, client_call_id),
  CONSTRAINT fk_call_world FOREIGN KEY (world_id) REFERENCES imagent_world (id),
  CONSTRAINT fk_call_from FOREIGN KEY (world_id, caller_actor_id) REFERENCES imagent_actor (world_id, id),
  CONSTRAINT fk_call_to FOREIGN KEY (world_id, callee_actor_id) REFERENCES imagent_actor (world_id, id),
  CONSTRAINT ck_call_distinct CHECK (caller_actor_id <> callee_actor_id),
  CONSTRAINT ck_call_connected CHECK (status <> 'connected' OR connected_at IS NOT NULL),
  CONSTRAINT ck_call_terminal CHECK (status NOT IN ('rejected','missed','ended') OR ended_at IS NOT NULL)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin COMMENT='用户呼入 AI 的实时语音会话';

CREATE TABLE imagent_message (
  id CHAR(36) NOT NULL COMMENT '应用生成的 UUID',
  world_id CHAR(36) NOT NULL COMMENT '所属生活世界',
  seq BIGINT UNSIGNED NOT NULL COMMENT '本世界消息顺序',
  sender_actor_id CHAR(36) NULL COMMENT '发送人物；系统通话记录可空',
  origin ENUM('user','agent','system') NOT NULL COMMENT '来源身份，由服务端确定',
  channel ENUM('chat','call') NOT NULL DEFAULT 'chat' COMMENT '普通聊天或通话发言',
  call_id CHAR(36) NULL COMMENT '通话发言所属会话',
  client_message_id CHAR(36) NULL COMMENT '用户消息的重试标识',
  dedupe_key VARCHAR(160) NOT NULL COMMENT '每一条表达的稳定去重标识',
  payload_hash CHAR(64) NOT NULL COMMENT '请求或表达内容摘要',
  parts_json JSON NOT NULL COMMENT '文字、图片或音频等内容块',
  reply_to_id CHAR(36) NULL COMMENT '本世界被引用的消息',
  source_experience_id CHAR(36) NULL COMMENT '实际发送此表达的经过',
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '创建时间',
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6) COMMENT '修改时间',
  PRIMARY KEY (id),
  UNIQUE KEY uq_message_world (world_id, id),
  UNIQUE KEY uq_message_seq (world_id, seq),
  UNIQUE KEY uq_message_dedupe (world_id, dedupe_key),
  KEY idx_message_call (world_id, call_id, seq),
  CONSTRAINT fk_message_world FOREIGN KEY (world_id) REFERENCES imagent_world (id),
  CONSTRAINT fk_message_sender FOREIGN KEY (world_id, sender_actor_id) REFERENCES imagent_actor (world_id, id),
  CONSTRAINT fk_message_call FOREIGN KEY (world_id, call_id) REFERENCES imagent_call_session (world_id, id),
  CONSTRAINT fk_message_reply FOREIGN KEY (world_id, reply_to_id) REFERENCES imagent_message (world_id, id),
  CONSTRAINT fk_message_exp FOREIGN KEY (world_id, source_experience_id) REFERENCES imagent_experience (world_id, id),
  CONSTRAINT ck_message_channel CHECK ((channel = 'chat' AND call_id IS NULL) OR (channel = 'call' AND call_id IS NOT NULL)),
  CONSTRAINT ck_message_sender CHECK (origin = 'system' OR sender_actor_id IS NOT NULL),
  CONSTRAINT ck_message_client_id CHECK (origin <> 'user' OR channel <> 'chat' OR client_message_id IS NOT NULL)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin COMMENT='长期聊天与通话发言的对外表达记录';

CREATE TABLE imagent_message_delivery (
  world_id CHAR(36) NOT NULL COMMENT '所属生活世界',
  message_id CHAR(36) NOT NULL COMMENT '消息',
  viewer_actor_id CHAR(36) NOT NULL COMMENT '仅消息参与方的视图人物',
  delivered_at DATETIME(6) NULL COMMENT '实际送达',
  read_at DATETIME(6) NULL COMMENT '实际阅读',
  displayed_read_at DATETIME(6) NULL COMMENT '允许对另一方展示的已读时间',
  played_ms BIGINT UNSIGNED NOT NULL DEFAULT 0 COMMENT '单调递增的已播时长',
  heard_content TEXT NULL COMMENT '由播出对齐数据计算的已听内容',
  hidden_at DATETIME(6) NULL COMMENT '管理员清理此视图历史的时间',
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '创建时间',
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6) COMMENT '修改时间',
  PRIMARY KEY (world_id, message_id, viewer_actor_id),
  KEY idx_delivery_view (world_id, viewer_actor_id, hidden_at, message_id),
  CONSTRAINT fk_delivery_msg FOREIGN KEY (world_id, message_id) REFERENCES imagent_message (world_id, id),
  CONSTRAINT fk_delivery_viewer FOREIGN KEY (world_id, viewer_actor_id) REFERENCES imagent_actor (world_id, id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin COMMENT='消息的各方视图、真实回执与历史隐藏';

CREATE TABLE imagent_moment (
  id CHAR(36) NOT NULL COMMENT '应用生成的 UUID',
  world_id CHAR(36) NOT NULL COMMENT '所属生活世界',
  author_actor_id CHAR(36) NOT NULL COMMENT '由服务端校验为本世界主 AI',
  parts_json JSON NOT NULL COMMENT '文字和图片内容块',
  source_experience_id CHAR(36) NULL COMMENT '触发分享的经历',
  expression_json JSON NOT NULL COMMENT '表达选择及事实关联；内部使用',
  dedupe_key VARCHAR(160) NOT NULL COMMENT '发布去重标识',
  status ENUM('published','withdrawn') NOT NULL DEFAULT 'published' COMMENT '发布状态',
  version BIGINT UNSIGNED NOT NULL DEFAULT 1 COMMENT '内容及互动的可见版本',
  published_at DATETIME(6) NOT NULL COMMENT '发布时间',
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '创建时间',
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6) COMMENT '修改时间',
  PRIMARY KEY (id),
  UNIQUE KEY uq_moment_world (world_id, id),
  UNIQUE KEY uq_moment_dedupe (world_id, dedupe_key),
  KEY idx_moment_feed (world_id, published_at, id),
  CONSTRAINT fk_moment_author FOREIGN KEY (world_id, author_actor_id) REFERENCES imagent_actor (world_id, id),
  CONSTRAINT fk_moment_source FOREIGN KEY (world_id, source_experience_id) REFERENCES imagent_experience (world_id, id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin COMMENT='只有主 AI 可以发布的朋友圈动态';

CREATE TABLE imagent_moment_audience (
  world_id CHAR(36) NOT NULL COMMENT '所属生活世界',
  moment_id CHAR(36) NOT NULL COMMENT '动态',
  viewer_actor_id CHAR(36) NOT NULL COMMENT '被允许查看的人物',
  granted_at DATETIME(6) NOT NULL COMMENT '授权时间',
  revoked_at DATETIME(6) NULL COMMENT '撤回可见授权',
  seen_version BIGINT UNSIGNED NOT NULL DEFAULT 0 COMMENT '实际看到的最后一个动态版本',
  last_viewed_at DATETIME(6) NULL COMMENT '实际浏览时间',
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '创建时间',
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6) COMMENT '修改时间',
  PRIMARY KEY (world_id, moment_id, viewer_actor_id),
  KEY idx_audience_reader (world_id, viewer_actor_id, revoked_at, moment_id),
  CONSTRAINT fk_audience_moment FOREIGN KEY (world_id, moment_id) REFERENCES imagent_moment (world_id, id),
  CONSTRAINT fk_audience_viewer FOREIGN KEY (world_id, viewer_actor_id) REFERENCES imagent_actor (world_id, id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin COMMENT='动态对各人物的显式可见授权与实际浏览';

CREATE TABLE imagent_moment_interaction (
  id CHAR(36) NOT NULL COMMENT '应用生成的 UUID',
  world_id CHAR(36) NOT NULL COMMENT '所属生活世界',
  moment_id CHAR(36) NOT NULL COMMENT '所属动态',
  actor_id CHAR(36) NOT NULL COMMENT '互动人物',
  kind ENUM('like','comment') NOT NULL COMMENT '互动类型',
  content TEXT NULL COMMENT '评论正文；点赞为空',
  reply_to_id CHAR(36) NULL COMMENT '回复的同动态评论',
  active BOOLEAN NOT NULL DEFAULT TRUE COMMENT '点赞是否有效或评论是否展示',
  dedupe_key VARCHAR(160) NOT NULL COMMENT '点赞为固定键，评论使用请求键',
  payload_hash CHAR(64) NOT NULL COMMENT '重试核对输入',
  revision BIGINT UNSIGNED NOT NULL DEFAULT 1 COMMENT '互动变更版本',
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '创建时间',
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6) COMMENT '修改时间',
  PRIMARY KEY (id),
  UNIQUE KEY uq_interaction_post (world_id, moment_id, id),
  UNIQUE KEY uq_interaction_request (world_id, moment_id, actor_id, dedupe_key),
  KEY idx_interaction_moment (world_id, moment_id, created_at, id),
  CONSTRAINT fk_interaction_moment FOREIGN KEY (world_id, moment_id) REFERENCES imagent_moment (world_id, id),
  CONSTRAINT fk_interaction_actor FOREIGN KEY (world_id, actor_id) REFERENCES imagent_actor (world_id, id),
  CONSTRAINT fk_interaction_reply FOREIGN KEY (world_id, moment_id, reply_to_id) REFERENCES imagent_moment_interaction (world_id, moment_id, id),
  CONSTRAINT ck_interaction_payload CHECK ((kind = 'like' AND content IS NULL AND reply_to_id IS NULL AND dedupe_key = 'like') OR (kind = 'comment' AND content IS NOT NULL AND dedupe_key <> 'like'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin COMMENT='用户与虚拟伙伴的点赞及评论';

CREATE TABLE imagent_client_event (
  user_id VARCHAR(64) NOT NULL COMMENT '事件所属用户',
  event_seq BIGINT UNSIGNED NOT NULL COMMENT '在用户空间锁内分配的连续序号',
  world_id CHAR(36) NULL COMMENT '候选进度等创建前事件可空',
  event_type VARCHAR(64) NOT NULL COMMENT '消息、通话、动态或公开任务状态',
  resource_type VARCHAR(40) NOT NULL COMMENT '关联公开资源类型',
  resource_id VARCHAR(64) NOT NULL COMMENT '关联资源标识',
  payload_json JSON NOT NULL COMMENT '经过公开 DTO 投影的内容；发送时再次校验可见范围',
  dedupe_key VARCHAR(160) NOT NULL COMMENT '发布幂等标识',
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '入流时间',
  PRIMARY KEY (user_id, event_seq),
  UNIQUE KEY uq_client_event (user_id, dedupe_key),
  KEY idx_client_world (world_id, event_seq),
  CONSTRAINT fk_client_user FOREIGN KEY (user_id) REFERENCES imagent_user_space (user_id),
  CONSTRAINT fk_client_world FOREIGN KEY (world_id, user_id) REFERENCES imagent_world (id, user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin COMMENT='客户端可见变更的持久事件流';

CREATE TABLE imagent_admin_operation (
  id CHAR(36) NOT NULL COMMENT '应用生成的 UUID',
  operator_user_id VARCHAR(64) NOT NULL COMMENT '可信管理员身份',
  target_user_id VARCHAR(64) NULL COMMENT '被处理用户',
  world_id CHAR(36) NULL COMMENT '被处理世界',
  kind ENUM('intervention','history_clear','reset','recreate','model_default') NOT NULL COMMENT '操作类型',
  reason TEXT NOT NULL COMMENT '人工处理原因',
  request_key VARCHAR(160) NOT NULL COMMENT '操作幂等标识',
  request_hash CHAR(64) NOT NULL COMMENT '操作参数摘要',
  parameters_json JSON NOT NULL COMMENT '操作范围、来源经历和参数',
  state_change_id CHAR(36) NULL COMMENT '关联的状态变化',
  result_world_id CHAR(36) NULL COMMENT '重置后新世界',
  result_json JSON NOT NULL COMMENT '执行结果引用',
  status ENUM('pending','completed','failed') NOT NULL DEFAULT 'pending' COMMENT '处理状态',
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) COMMENT '创建时间',
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6) COMMENT '修改时间',
  PRIMARY KEY (id),
  UNIQUE KEY uq_admin_request (operator_user_id, request_key),
  KEY idx_admin_target (target_user_id, created_at),
  CONSTRAINT fk_admin_target FOREIGN KEY (target_user_id) REFERENCES imagent_user_space (user_id),
  CONSTRAINT fk_admin_world FOREIGN KEY (world_id, target_user_id) REFERENCES imagent_world (id, user_id),
  CONSTRAINT fk_admin_change FOREIGN KEY (world_id, state_change_id) REFERENCES imagent_state_change (world_id, id),
  CONSTRAINT fk_admin_result FOREIGN KEY (result_world_id, target_user_id) REFERENCES imagent_world (id, user_id),
  CONSTRAINT ck_admin_scope CHECK ((world_id IS NULL OR target_user_id IS NOT NULL) AND (state_change_id IS NULL OR world_id IS NOT NULL) AND (result_world_id IS NULL OR target_user_id IS NOT NULL))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin COMMENT='管理员干预、历史清理与重置的审计记录';

ALTER TABLE imagent_user_space ADD CONSTRAINT fk_space_world FOREIGN KEY (active_world_id, user_id) REFERENCES imagent_world (id, user_id);
ALTER TABLE imagent_world ADD CONSTRAINT fk_world_primary FOREIGN KEY (id, primary_actor_id) REFERENCES imagent_actor (world_id, id);
ALTER TABLE imagent_world ADD CONSTRAINT fk_world_user_actor FOREIGN KEY (id, user_actor_id) REFERENCES imagent_actor (world_id, id);
ALTER TABLE imagent_world ADD CONSTRAINT fk_world_call FOREIGN KEY (id, active_call_id) REFERENCES imagent_call_session (world_id, id);
