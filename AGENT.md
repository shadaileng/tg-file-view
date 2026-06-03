# AGENT.md — tg_file_viewer 项目开发指南

> AI Agent 快速上手文档：架构、规则、开发计划、操作指令。

---

## Current Phase: feat/thumbnail-management-fix — 缩略图管理 4 大问题修复 🔧

**分支**: `feat/thumbnail-management-fix`

### 问题清单
1. **缩略图管理无自动更新** — 前端只加载一次，不轮询
2. **处理中无阶段/进度显示** — `ThumbJob` 缺少 `phase` / `progress` 字段
3. **视频封面极慢** — 全量下载视频 + ffmpeg（可耗时数分钟）
4. **取消盲区** — Worker 处理中不检查取消，最终覆盖 `completed`

### 变更范围矩阵

| 变更点 | 影响模块 | 破坏性变更 |
|--------|---------|:---:|
| `ThumbJob` 新增 `phase` + `progress` 字段 | `models.py` | 否（SQLite MO 兼容） |
| `ThumbJobOut` 新增 `phase` + `progress` | `api/thumbnails.py` | 否 |
| `_process_job` 拆分为 3 路径：视频TG缩略图 / 图片Pillow / 视频ffmpeg回退 | `services/task_queue.py` | 否 |
| 新增 `_download_telegram_thumb()` (下载 TG 预生成缩略图，几KB) | `services/task_queue.py` | 否 |
| 3 个取消检查点 (checkpoint 0/1/2/3) 防止 completed 覆盖 cancelled | `services/task_queue.py` | 否 |
| 前端轮询 + 阶段进度展示 | `ThumbnailsView.vue` | 否 |

### 场景设计

#### S1 — Happy Path: 视频封面用 TG 缩略图
```
GIVEN 视频文件，TG 缩略图可用
WHEN  worker 处理该 job
THEN  下载 TG 缩略图（~10KB，<1s）
      thumb_type = "telegram"
      job.phase: processing → downloading → completed
```

#### S2 — Edge: TG 缩略图不可用，回退 ffmpeg
```
GIVEN 视频文件，TG 缩略图为空或下载失败
WHEN  worker 处理该 job
THEN  回退到完整下载 + ffmpeg 提取封面
      thumb_type = "auto"
```

#### S3 — Happy Path: 前端自动轮询
```
GIVEN 有 pending/processing job
WHEN  用户停留在缩略图管理页面
THEN  每 2s 自动刷新 jobs + stats，显示阶段标签和进度条
```

#### S4 — Edge: 下载中取消（修复 bug）
```
GIVEN job 正在 _ensure_cached 下载文件
WHEN  用户点击取消 → DB status = "cancelled"
THEN  下载完成后 session.refresh(job)，检测到 cancelled → return（不写 completed）
```

#### S5 — Edge: 无活跃任务时停止轮询
```
GIVEN 所有 job 都已完成/失败/取消
WHEN  前端检测到 hasActive = false
THEN  停止轮询
```
THEN  创建 3 个 ThumbJob (status=pending)，入队到 ThumbnailWorkerPool
      Worker 下载缓存 → Pillow 生成缩略图 → 更新 file.thumb_path
```

#### S2 — Happy Path: 同步后自动生成视频封面
```
GIVEN 频道同步完成，有 2 个新 video 文件（thumb_path=NULL）
WHEN  _bg_sync 进入 post-sync 阶段
THEN  创建 2 个 ThumbJob，入队
      Worker 下载缓存 → ffmpeg 提取首帧(1s / 10%) → JPEG 保存到 thumbnails/
```

#### S3 — Happy Path: 混合文件类型同时处理
```
GIVEN 同步完成，有 photo×3、video×2、document×1（均 thumb_path=NULL）
WHEN  post-sync 触发
THEN  只创建 5 个 job（跳过 document），photo 用 Pillow，video 用 ffmpeg
```

#### S4 — Edge: 文件已全部有缩略图
```
GIVEN 频道所有文件的 thumb_path 不为 NULL
WHEN  post-sync 触发
THEN  跳过，返回 0
```

#### S5 — Edge: 部分文件已有 pending/processing job
```
GIVEN 同步完成，5 个文件缺缩略图，其中 2 个已有 pending ThumbJob
WHEN  post-sync 触发
THEN  只为剩余 3 个文件创建 ThumbJob（防重入）
```

#### S6 — Edge: Worker pool 未启动
```
GIVEN _bg_sync 调用时 thumb worker pool 为 None
WHEN  post-sync 尝试创建 job
THEN  返回 0，记录 warning
```

#### S7 — Edge: 视频封面生成失败（ffmpeg 不可用或视频损坏）
```
GIVEN Worker 拿到 video 类型 job，ffmpeg 命令失败
WHEN  generate_video_cover() 返回 False
THEN  job→failed (重试 3 次后)
```

#### S8 — Happy Path: 视频封面取 1s 处帧，失败回退到 10%
```
GIVEN 视频时长 30 秒
WHEN  生成封面
THEN  先尝试 ffmpeg -ss 1 取第 1 秒帧
      若失败，回退到 ffprobe 探测时长 × 10% 处帧
```

### 场景→测试映射

| 场景 ID | 场景描述 | 对应测试函数 | 类型 |
|---------|---------|-------------|------|
| S1 | 同步后自动生成图片缩略图 | `test_post_sync_photo_thumb_trigger` | 集成 |
| S2 | 同步后自动生成视频封面 | `test_post_sync_video_cover_trigger` | 集成 |
| S3 | 混合文件类型 | `test_post_sync_mixed_types` | 集成 |
| S4 | 全部已有缩略图 | `test_post_sync_all_have_thumbs` | 集成 |
| S5 | 部分已有 pending job | `test_post_sync_skips_existing_jobs` | 集成 |
| S6 | Worker pool 不可用 | `test_post_sync_no_pool` | 单元 |
| S7 | 视频封面生成失败 | `test_process_job_video_no_ffmpeg` | 单元 |
| S8 | 视频封面 1s→10% 回退 | `test_generate_video_cover_fallback_position` | 单元 |
| — | `generate_video_cover` 成功 | `test_generate_video_cover_success` | 单元 |
| — | `generate_video_cover` 无 ffmpeg | `test_generate_video_cover_no_ffmpeg` | 单元 |
| — | 视频 job 处理成功 | `test_process_job_video_success` | 单元 |
| — | 同步 API 触发 post-sync | `test_sync_triggers_post_sync_thumb` | 集成 |

### 关键设计决策
- **Post-sync 非致命**：缩略图触发失败不阻止同步完成，仅记录 warning
- **防重入**：查询已有 pending/processing ThumbJob 的 file_id，跳过重复创建
- **仅支持类型**：photo、sticker、video — 其他文件类型不创建 ThumbJob
- **视频封面策略**：先 1s 主策略 → 失败回退 10%（使用 ffprobe 探测时长）

---


## Previous Phase: feat/sync-phase-progress — 同步进度分阶段可视化 & 详细信息展示 ✅

**分支**: `feat/sync-phase-progress`

### 需求背景
用户反馈：同步过程中进度不更新（`0 / ?`），synced=0 skipped=14 时误以为"没同步上"，全过程缺乏分阶段感知。

### 变更范围矩阵

| 变更点 | 影响模块 | 破坏性变更 |
|--------|---------|:---:|
| SyncTask 新增 `phase`(str) + `progress`(int) 字段 | `models.py` | 是（需迁移） |
| sync_channel 5 阶段进度上报 (connecting→scanning→inserting→finalizing→completed) | `services/sync_engine.py` | 否 |
| `_sync_task_to_dict` 暴露 phase/progress | `api/sync.py` | 否 |
| 前端进度面板重设计：阶段指示器 + 详细统计 | `frontend/src/views/SyncView.vue` | 否 |
| schema 迁移：ALTER TABLE sync_tasks 添加新列 | `database.py` | 否 |

### 分阶段设计

| Phase Key | 中文 | 百分比区间 | 进度触发条件 |
|-----------|------|:---:|------------|
| `pending` | 等待 | 0% | 任务创建后 |
| `connecting` | 连接频道 | 0%→5% | `get_entity()` 完成后 |
| `scanning` | 扫描消息 | 5%→55% | 每 10 条消息 commit 一次 |
| `inserting` | 入库处理 | 55%→90% | 每次 _batch_insert_files 后 |
| `finalizing` | 更新统计 | 90%→100% | channel 统计更新 |
| `completed` | 完成 | 100% | 任务结束 |

### 场景设计

#### S1 — Happy Path: 分阶段进度交互
```
GIVEN 频道未同步，点击"开始同步"
WHEN  同步进行
THEN  前端展示 5 阶段指示器：连接→扫描→入库→统计→完成
      进度条从 5%→55%→90%→100% 逐步推进
      统计面板显示：已扫描 N、新增 +M、跳过 K
      同步完成后任务状态变 completed
```

#### S2 — Edge: synced=0 skipped=N 全量跳过
```
GIVEN 频道已完全同步
WHEN  用户再次触发同步
THEN  阶段指示器正常流转（scanning→inserting→completed）
      新增显示 +0，跳过显示 K（非零），用户明确知道已全量匹配
```

#### S3 — Edge: 小频道（消息数 < batch_size）
```
GIVEN 频道仅 16 条消息，sync_batch_size=500
WHEN  同步进行
THEN  每 10 条消息更新一次 total_files 和进度（不再 stuck at 0）
      阶段指示器正常推进，3 秒内完成也不会有"空白期"
```

#### S4 — Edge: 取消同步
```
GIVEN 同步正在 scanning 阶段
WHEN  用户点击"取消同步"
THEN  任务 phase→cancelled，进度条冻结在当前值变灰
      轮询停止，历史列表更新
```

#### S5 — Edge: 同步异常
```
GIVEN Telegram 连接异常
WHEN  sync_channel 抛异常
THEN  任务 phase→failed，进度条变红
      错误详情记录在 errors 字段
```

### 场景→测试映射

| 场景 ID | 场景描述 | 对应测试函数 | 类型 |
|---------|---------|-------------|------|
| S1 | 分阶段进度正常流转 | `test_sync_full` | 集成 |
| S2 | synced=0 全量跳过 | `test_sync_incremental` | 集成 |
| S3 | 小频道 total_files 实时更新 | `test_total_files_updates_during_sync` | 集成 |
| S4 | 取消同步 | 手动验证 (浏览器) | 手动 |
| S5 | 同步异常 | `test_sync_unauthorized` | 集成 |
| — | phase/progress 字段持久化 | `test_sync_with_existing_task_id` | 集成 |

### 数据库迁移

```sql
ALTER TABLE sync_tasks ADD COLUMN phase VARCHAR(20) NOT NULL DEFAULT 'pending';
ALTER TABLE sync_tasks ADD COLUMN progress INTEGER NOT NULL DEFAULT 0;
```

迁移于 `database.py::_migrate_schema()` 自动化执行。

---

## 完成记录: fix/sync-progress-realtime — 修复同步进度实时更新 & 信息不完整 ✅

**分支**: `fix/sync-progress-realtime` (已合并)

### 变更范围矩阵

| 变更点 | 影响模块 | 破坏性变更 |
|--------|---------|:---:|
| `sync_channel` 增加 `task_id` 参数，复用 API 创建的任务 | `services/sync_engine.py` | 否 |
| `_bg_sync` 传递 `task_id` 给 `sync_channel` | `api/sync.py` | 否 |
| 批量插入时实时更新 `total_files` | `services/sync_engine.py` | 否 |
| 同步完成后更新 channel 统计 (file_count, total_size) | `services/sync_engine.py` | 否 |
| 页面加载时自动检测运行中任务并恢复轮询 | `frontend/src/views/SyncView.vue` | 否 |

### 场景设计

#### S1 — Happy Path: 同步进度实时更新
```
GIVEN 频道未同步
WHEN  用户触发同步
THEN  前端每 2 秒轮询可见 synced_files/total_files 实时百分比进度
      同步完成后任务状态为 completed，channel.file_count 和 total_size 已更新
```

#### S2 — Edge: 页面刷新恢复运行中任务
```
GIVEN 同步正在运行 (status=running)
WHEN  用户刷新页面 → 选择该频道
THEN  watch 自动检测到 running 任务 → 恢复 activeSync 并启动轮询
```

#### S3 — Edge: 取消同步
```
GIVEN 同步正在运行
WHEN  用户点击取消
THEN  任务状态变为 cancelled，channel.last_sync 不更新
      轮询停止，历史列表更新
```

#### S4 — Edge: 重复触发
```
GIVEN 同步已在运行
WHEN  再次点击"开始同步"
THEN  返回 409 Conflict（已有逻辑，不受影响）
```

### 场景→测试映射

| 场景 ID | 场景描述 | 对应测试函数 | 类型 |
|---------|---------|-------------|------|
| S1 | API 创建的 task_id 被 sync_channel 复用 | `test_sync_with_existing_task_id` | 集成 |
| S2 | 不存在的 task_id 抛异常 | `test_sync_task_id_not_found` | 集成 |
| — | total_files 在同步过程中更新 | `test_total_files_updates_during_sync` | 集成 |
| — | channel 统计在同步完成后更新 | `test_sync_updates_channel_stats` | 集成 |
| — | 前端 watch 检测 running 任务 | 手动验证 (Vue, 无自动化) | 手动 |

### Bug 修复记录 (2026-06-02)

#### Bug #1：触发同步后轮询立即停止（字段名不匹配）
- **现象**: 触发同步后，进度标题显示"同步进行中"，但进度永不更新——需要刷新页面才能看到任务完成
- **根因**: `trigger_sync` 返回 `{"task_id": ..., ...}`，前端 `pollActiveSync()` 检查 `activeSync.value?.id` 为 `undefined`，立即 `stopPolling()`
- **修复**: `trigger_sync` 改用 `_sync_task_to_dict(task)` 统一序列化，字段名 `id` 与前端一致
- **文件**: `api/sync.py` line 127-130

#### Bug #2：同步异常时任务永远 stuck 在 pending
- **现象**: 后台 `_bg_sync` 抛异常后，任务状态永远保持 `pending`，前端持续轮询无法结束
- **根因**: `_bg_sync` 的 `except` 块只记日志，未更新任务状态
- **修复**: 在 `except` 块中增加兜底逻辑——若任务仍为 `pending`，标记为 `failed`
- **文件**: `api/sync.py` line 69-81

#### S2 — Happy Path: 发现频道按钮正常加载
```
GIVEN Telegram 已授权，用户有已订阅频道
WHEN  点击"发现频道"按钮
THEN  面板展开，调用 GET /api/channels/discover
      列表展示发现的频道（已添加的标记"已添加"）
```

#### S3 — Edge: 发现频道无结果
```
GIVEN Telegram 已授权，用户无已订阅频道 或 授权过期
WHEN  点击"发现频道"按钮
THEN  面板展开，显示"未发现频道，请确认 Telegram 已授权"
```

#### S4 — Edge: 认证状态变更后前端自动刷新
```
GIVEN 认证页面完成登录/登出
WHEN  dispatchEvent('app-auth-changed')
THEN  App.vue 监听到事件 → 调用 checkAuth() → Header 授权图标实时更新
```

---

## 1. 项目概览

**tg_file_viewer** 是一个单体 FastAPI 服务，替代原有的三服务架构（tg-bot-server / tg_channel_sync / tg_sync_manager），整合 Telegram 频道文件查看、同步、预览、缩略图生成和缓存管理。

- **技术栈**: FastAPI + Telethon + SQLAlchemy/aiosqlite + Vue 3 + Tailwind
- **包管理**: uv (后端) / pnpm (前端)
- **测试**: pytest + pytest-asyncio, TDD 模式
- **部署**: 本地 / Docker / HF Space

---

## 2. 目录结构

```
tg_file_viewer/
├── logging_config.py  # ✅ loguru 日志初始化: 控制台 + 文件轮转 + 错误分离
├── main.py              # FastAPI app, lifespan, CORS, 静态文件挂载, 路由注册, 日志配置
├── config.py            # Settings类 + DB动态配置(get/set/get_settings/ensure_initialized)
├── database.py          # 异步SQLite引擎 + AsyncSessionLocal + Base + get_session/get_db
├── models.py            # 5张ORM表: Channel, File, SyncTask, ThumbJob, AppConfig
├── api/                 # API路由层 (按功能拆分)
│   ├── auth.py          # ✅ 认证: send-code, verify-code, verify-2fa, status, logout
│   ├── channels.py      # ✅ 频道CRUD: create(list), read(get/list), delete, discover
│   ├── files.py         # ✅ 文件列表/下载/缓存: list, detail, download, cache
│   ├── sync.py          # ✅ 同步触发/管理: trigger(202), tasks, cancel
│   ├── thumbnails.py    # ✅ 缩略图任务管理: trigger, batch, list, stats, cancel
│   ├── config.py         # ✅ 配置管理API: list/get/update (admin-auth, 类型校验, 只读保护)
│   └── cache.py          # ✅ 缓存统计 + 手动淘汰 API
├── middleware/           # 中间件层
│   ├── logging.py       # ✅ 请求日志: method + path + status + 耗时ms
├── services/            # 业务逻辑层
│   ├── telegram_client.py  # ✅ TelegramService: Telethon封装, AuthState状态机, 全局单例
│   ├── sync_engine.py      # ✅ 同步引擎 (iter_messages + 去重 + 批量INSERT)
│   ├── task_queue.py       # ✅ 生产者-消费者 PriorityQueue 缩略图任务池
│   └── cache_manager.py    # ✅ LRU淘汰, 动态上限, 手动触发
├── tests/               # 测试文件 (与源文件一一对应)
│   ├── conftest.py         # fixtures: session-scoped建库, function-scoped reset tables
│   ├── test_database.py    # ✅ 8 tests
│   ├── test_config.py      # ✅ 10 tests
│   ├── test_models.py      # ✅ 12 tests
│   ├── test_telegram_client.py # ✅ 15 tests
│   ├── test_auth_api.py    # ✅ 9 tests
│   ├── test_logging.py     # ✅ 13 tests
│   ├── test_channels_api.py   # ✅ 19 tests
│   ├── test_files_api.py      # ✅ 14 tests
│   ├── test_sync_engine.py    # ✅ 12 tests
│   ├── test_sync_api.py       # ✅ 12 tests
│   ├── test_task_queue.py     # ✅ 11 tests
│   ├── test_thumbnails_api.py # ✅ 13 tests
│   └── test_data/          # 测试数据
│   ├── test_config_api.py    # ✅ 12 tests
├── frontend/            # ⏳ Vue 3 + Vite + Tailwind (pnpm)
├── data/                # 运行时数据 (db.sqlite, thumbnails/, cache/)
├── CHANGELOG.md         # 开发日志 (每步更新的详细记录)
└── README.md            # 项目文档
```

---

## 3. 核心架构模式

### 3.1 配置优先级

```
DB app_config 表 > 环境变量 (TG_ 前缀) > Settings 默认值
```

- `config.py::Settings` — pydantic-settings，`model_config = {"env_prefix": "TG_"}`
- `config.py::get_settings(db_session)` — 从 DB 合并覆盖
- `config.py::ensure_initialized(db_session)` — 首次启动 seed DB
- 运行时热更新：修改 `app_config` 表 → 调用 `get_settings()` 生效

### 3.2 认证状态机

```
TelegramService.auth_state: Enum
  DISCONNECTED → CONNECTING → CODE_SENT → AUTHORIZED
                                        → 2FA_REQUIRED → AUTHORIZED
  AUTHORIZED → LOGGED_OUT (logout)
```

- 全局单例：`get/set/reset_telegram_service()` 在 `services/telegram_client.py`
- 支持 phone + bot_token 两种认证
- session 持久化到文件

### 3.3 数据库

- SQLite (aiosqlite) + SQLAlchemy 2.0 async
- `check_same_thread=False`
- `expire_on_commit=False`
- 5 张表：
  - `channels` — tg_id unique, username, title, file_count, last_sync
  - `files` — channel_id FK, message_id, file_type, thumb_path/cache_path, tg_ref
  - `sync_tasks` — UUID, status, counts, errors JSON
  - `thumb_jobs` — UUID, status, priority(1-10), attempt, max_retries(3)
  - `app_config` — key PK, value, updated_at

### 3.4 API 设计约定

- 所有 API 返回 JSON，pydantic 模型校验
- 路由在 `api/` 下按功能拆分
- 在 `main.py` 中用 `app.include_router()` 注册
- 静态文件通过 `app.mount()` 挂载 `/thumbnails` 和 `/cache`
- CORS 允许所有来源

---

## 4. 开发计划 (10 步 TDD)

| Step | 内容 | 测试数 | 状态 |
|------|------|--------|------|
| 1 | 项目骨架 + DB + 配置 + 模型 | 30 | ✅ |
| 2 | Telegram 客户端 + 认证 API | 23 | ✅ |
| 3 | 频道管理 API (CRUD) | 19 | ✅ |
| 4 | 文件列表 / 下载 / 缓存 API | 14 | ✅ |
| 5 | 同步引擎 (Telethon iter → DB) | 24 | ✅ |
| 6 | 缩略图任务队列 (PriorityQueue) | 24 | ✅ |
| 7 | 缓存管理器 (LRU, 动态上限) | 17 | ✅ |
| 8 | 配置管理 API (热更新 DB config) | 12 | ✅ |
| 9 | Vue 3 + Tailwind 前端 | ~15 | ✅ |
| 10 | Docker + HF Space 部署 | ~5 | ⏳ |

---

## 5. ⚠️ 开发规则 (必须遵守)

### 5.0 开发全生命周期

任何功能开发/修改必须走完整闭环，3 条规则覆盖 6 个阶段：

| Phase | 规则 | 关键动作 |
|:---:|------|------|
| 1-3 | 需求分析与闭环开发 | 需求矩阵 → GIVEN/WHEN/THEN 场景 → 先写文档再写代码 |
| — | Git分支管理 | 从 main 切出 `feat/fix/refactor` 分支，禁止在 main 直接开发 |
| 4-6 | 开发过程记录 | TDD 实现 → 全量测试 → 文档同步 → 合并确认 → 提交 |

### 5.1 需求分析与场景模拟 (Phase 1-3)

**开发前必须先做**，严禁直接写代码。

#### 5.1.1 需求分析矩阵
- 谁提出的需求？解决什么问题？
- 变更范围矩阵：列出每个变更点、影响模块、是否破坏性变更
- 边界条件：至少列出 3 种异常场景及预期行为

#### 5.1.2 GIVEN/WHEN/THEN 场景
```
GIVEN [前置条件]
WHEN  [触发动作]
THEN [预期结果]
```
- 至少 1 条正常流程 + 2 条异常流程
- 记录在 AGENT.md 当前 Phase 下

#### 5.1.3 文档先行
分析完成后，**先更新文档，再写代码**：
- `AGENT.md`：记录场景表格
- `CHANGELOG.md`：预留变更条目
- `.env.example`：如需新配置项，先加占位

### 5.2 Git 分支管理

**禁止直接在 main 分支开发。**

#### 5.2.1 分支命名
```
feat/<desc>     # 新功能  → feat/channel-sync
fix/<desc>      # 缺陷修复 → fix/db-init-missing-models
refactor/<desc> # 重构   → refactor/split-api-routes
docs/<desc>     # 文档   → docs/api-reference
test/<desc>     # 测试补充 → test/integration-coverage
```

#### 5.2.2 合并前检查清单
- [ ] `pytest` 全量测试通过
- [ ] diff 确认无意外改动
- [ ] 文档已同步更新
- [ ] 无 merge conflict
- [ ] 开发日志已记录

#### 5.2.3 合并确认
1. 确认检查清单全部 ✔
2. 一句话说明变更内容
3. **等待用户确认**后执行合并
4. 使用 `git merge --no-ff` 保留分支历史

**禁止**：`--no-edit` 自动合并、未确认 push main、force push main

### 5.3 TDD 开发流程 (Phase 4)

```
🔴 写测试 → 🟢 最小实现 → 🔧 重构 → 📝 更新CHANGELOG.md → ✅ git commit
```

### 5.4 测试要求

- 每步完成后运行 **全量测试**，必须 **100% 通过**
- 测试命令：`uv run pytest tests/ -v`
- 新增测试文件放在 `tests/`，命名 `test_<module>.py`
- 测试数据隔离在 `tests/test_data/`
- 同时覆盖单元测试、集成测试、回归测试

#### 5.4.1 场景全覆盖（强制性）

**Phase 2 中定义的每条 GIVEN/WHEN/THEN 场景，必须有对应的自动化测试。** 建立场景→测试映射：

| 场景 ID | 场景描述 | 对应测试函数 | 类型 |
|---------|---------|-------------|------|
| S1 | Happy Path: xxx | `test_xxx_normal` | 集成 |
| S2 | Edge: 空值输入 | `test_xxx_empty_input` | 单元 |
| S3 | Edge: 并发冲突 | `test_xxx_concurrent` | 单元 |

测试函数命名建议：`test_{模块}_{场景简称}`，如 `test_channel_create_duplicate`。

### 5.5 Git 提交规范

```
feat(step-N): 简短描述 ✅ N/N PASS

详细变更列表

Tests: N/N PASS
```

示例：
```
feat(step-3): channel management API ✅ 65/65 PASS

New: api/channels.py, tests/test_channels_api.py
Modified: main.py (register channels router)

Tests: 65/65 PASS
```

### 5.6 CHANGELOG 格式

每步完成后在 `CHANGELOG.md` 中追加：
- 步骤标题 + 状态
- 新增/修改文件表格
- 关键设计决策
- 测试统计

### 5.7 代码风格

- **异步优先**：所有 I/O 操作使用 async/await
- **类型标注**：所有函数参数和返回值有类型标注
- **文档字符串**：每个公开函数/类有 docstring
- **异常处理**：API 层捕获并转为 HTTPException
- **无 emoji**：代码中不使用 emoji（测试/文档除外）
- **配置统一来源**：`main.py` 中所有配置读取**必须**通过 `settings` 对象，禁止使用 `os.environ.get()`。`database.py` 中的模块级 `os.environ.get()` 是合理的（导入时执行），但需要确保 `config.py`（含 `load_dotenv()`）在其之前导入。
- **UTC 时间戳规范**（三原则）：
  1. **存储层**：所有 `DateTime` 列必须 `DateTime(timezone=True)`，`default` 用 `lambda: datetime.now(timezone.utc)`，禁止 `datetime.utcnow()`
  2. **序列化层**：API 输出的 datetime 字符串**一律经过 `api/utils.py::utc_iso()`**，禁止裸调 `.isoformat()`
  3. **写入层**：所有手动赋值 datetime 的地方用 `datetime.now(timezone.utc)`，禁止 `datetime.utcnow()`

  为什么：
  - SQLite + SQLAlchemy 即使设置 `timezone=True`，读回时仍可能丢失 tzinfo → 裸 `.isoformat()` 产生无时区字符串（如 `"2026-06-03T06:10:20"`） → 前端 `new Date()` 解析为本地时间 → 8 小时时差
  - `utc_iso()` 兜底检测：`tzinfo is None` 时自动补 `+00:00` 后缀，确保前端正确解析

### 5.8 反模式（禁止）

| 反模式 | 后果 |
|--------|------|
| 拿到需求直接写代码 | 遗漏边界条件和集成点（见 Step 1 数据库教训） |
| 场景只考虑 Happy Path | 测试环境通过但集成失败 |
| 直接在 main 上改代码 | 无法回退、污染主分支 |
| 合并前不运行全量测试 | 破坏 main 稳定性 |
| 未确认就合并到 main | 引入未经审查的代码 |
| 先改代码再补文档 | 文档与实现不一致 |

---

## 6. 数据库操作模式

```python
# 在 API 路由中使用 FastAPI 依赖注入
from database import get_db

@router.get("/api/example")
async def example(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Model).where(...))
    return result.scalars().all()

# 在 service 层中独立使用
from database import AsyncSessionLocal
async with AsyncSessionLocal() as session:
    ...
```

---

## 7. 测试模式

```python
# conftest.py 提供 fixtures:
# - _engine_cleanup: session-scoped, 一次建库
# - _reset_tables: function-scoped autouse, 每测试前后 drop/create
# - db_session: async session

@pytest.mark.asyncio
class TestFeature:
    async def test_something(self, db_session):
        # db_session 是隔离的测试数据库会话
        result = await some_function(db_session)
        assert result == expected
```

### Mock 模式

```python
# 模拟 Telegram 服务（避免真实 API 调用）
from services.telegram_client import set_telegram_service
svc = AsyncMock()
set_telegram_service(svc)
```

---

## 8. 环境变量

运行前需配置 `.env`（从 `.env.example` 复制）：

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `TG_API_ID` | Telegram API ID | 0 |
| `TG_API_HASH` | Telegram API Hash | — |
| `TG_PHONE` | 手机号 (+86...) | — |
| `TG_BOT_TOKEN` | Bot Token | — |
| `TG_PROXY_URL` | socks5://host:port | — |
| `TG_SYNC_BATCH_SIZE` | 批大小 | 500 |
| `TG_THUMB_WORKERS` | 缩略图 worker 数 | 2 |
| `TG_CACHE_MAX_SIZE_MB` | 缓存上限 (0=无限) | 0 |
| `TG_ADMIN_PASSWORD` | 管理员密码 | — |
| `TG_PORT` | 服务端口 | 8000 |

### 日志配置

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `TG_LOG_LEVEL` | 控制台日志级别 (DEBUG/INFO/WARNING/ERROR) | INFO |
| `TG_LOG_FILE` | 日志文件路径 | ./data/app.log |
| `TG_LOG_ROTATION` | 轮转条件 (如 "10 MB", "1 day") | 10 MB |
| `TG_LOG_RETENTION` | 保留历史文件数 | 5 |

---

## 9. 快速命令参考

```bash
# 安装依赖
uv venv && source .venv/bin/activate && uv pip install -e ".[dev]"

# 运行服务
uv run uvicorn main:app --host 0.0.0.0 --port 8000 --reload

# 运行测试
uv run pytest tests/ -v

# 单个测试文件
uv run pytest tests/test_auth_api.py -v

# 覆盖率
uv run pytest tests/ --cov=. --cov-report=term-missing

# Git 提交
git add tg_file_viewer/
git commit -m "feat(step-N): description ✅ N/N PASS"
```
