# 开发日志 (CHANGELOG)

## fix: 修复登出后认证状态 + 发现频道按钮无响应 ✅ 180/180 PASS

### 问题
1. **登出后无法重新登录**：`logout()` 中调用 `reset_telegram_service()` 销毁全局 service 实例，导致 `auth_status()` 返回 `not_configured`，前端检测后不显示登录界面
2. **"发现频道"按钮点击无效果**：按钮 `@click` 只切换 `showDiscover` 布尔值，未调用 `loadDiscover()` 请求 API，面板展开但始终显示"未发现频道"
3. **认证后前端状态不更新**：AUTH/LOGOUT 成功后 Header 的授权状态图标不刷新，需手动刷新页面

### 修复

| 文件 | 变更 |
|------|------|
| `api/auth.py` | 移除 `reset_telegram_service` 导入和调用；`logout()` 中只调用 `svc.logout()`，不再销毁实例 |
| `frontend/src/App.vue` | 新增 `handleAuthChanged()` 监听 `app-auth-changed` 事件 → 调用 `checkAuth()` 刷新状态 |
| `frontend/src/views/AuthView.vue` | 验证码通过/2FA通过/登出后 dispatch `app-auth-changed` 自定义事件 |
| `frontend/src/views/ChannelsView.vue` | ① 按钮 `@click` 改为 `toggleDiscover`（调用 `loadDiscover()`）；② 清理死代码（`discoverWatcher`、`origToggleDiscover`、`defineExpose`） |

### 场景→测试映射

| 场景 ID | 场景描述 | 对应测试函数 | 类型 |
|---------|---------|-------------|------|
| S1 | 登出后 auth_status 返回 logged_out（可重新登录） | `test_logout_success` | 单元 |
| S2 | 发现频道按钮触发 API 请求 | Vue 组件行为（按钮绑定 `toggleDiscover`） | 前端 |
| S3 | 登录/登出后 Header 授权图标自动刷新 | `app-auth-changed` 事件 → `checkAuth()` | 前端 |

### 测试
- 全量 pytest 回归：180/180 PASS ✅

---

## Step 10: Docker 多阶段构建 + HF Space 部署 ✅

### 变更内容
| 文件 | 操作 | 说明 |
|------|------|------|
| `Dockerfile` | 新增 | 多阶段构建：node:20-alpine pnpm build → python:3.11-slim uv 运行 |
| `.dockerignore` | 新增 | 排除文档/日志/数据/依赖缓存，保留 lock 文件确保可重现 |
| `README.md` | 更新 | 顶部 HF Space 元数据 + Docker/HF Space 部署章节 |
| `AGENT.md` | 更新 | 更新 Current Phase 为 Step 10 |
| `CHANGELOG.md` | 更新 | 本条目 |

### 技术决策
| 决策 | 选择 | 说明 |
|:---|:---|:---|
| 构建方式 | 多阶段 Dockerfile | Stage 1: pnpm build → Stage 2: Python runtime |
| Python 包管理 | uv sync --no-dev --frozen | 与项目一致，产物更小 |
| 基础镜像 | python:3.11-slim | 轻量 + Pillow 系统依赖 |
| 默认端口 | 7860 | HF Spaces 默认检测端口 |
| 数据目录 | /data | 配合 HF Persistent Storage |
| 前端 serve | FastAPI StaticFiles SPA | 单端口一体化部署 |

### 场景覆盖
- S1: Docker 本地构建运行 (前端构建 → Python 运行 → 健康检查通过)
- S2: HF Space 部署 (Dockerfile 自动构建 → 访问 Space URL → API + 前端正常)
- S3: 首次启动无数据库 (init_db 自动建表 → ensure_initialized seed 默认配置)
- S4: 重新部署后 session 过期 (Telethon 要求重新认证 → Auth 页面登录)
- S5: 环境变量注入 (TG_API_ID/TG_API_HASH → Settings 正确加载)

### 测试
- 全量 pytest 回归 ✅

---

## feat: 新增前后端启动脚本

### 变更内容
| 文件 | 操作 | 说明 |
|------|------|------|
| `scripts/dev.sh` | 新增 | 开发模式一键启动：自动清理端口 → 后台 uvicorn --reload → 前台 Vite dev |
| `scripts/start.sh` | 新增 | 生产模式启动：pnpm build → uvicorn serve SPA |
| `README.md` | 更新 | 项目结构新增 scripts/，运行章节改为脚本说明 |
| `AGENT.md` | 更新 | 新增场景设计和变更矩阵 |

### 场景覆盖
- S1: 首次开发启动 (正常流程)
- S2: 端口被占用自动清理 (异常处理)
- S3: 生产模式构建+启动 (正常流程)

---

## Step 9: Vue 3 + Tailwind 前端 ✅ 180/180 PASS

### 技术决策
| 决策 | 选择 | 说明 |
|:---|:---|:---|
| UI 框架 | Tailwind CSS 纯手写 | 无第三方 UI 库 |
| 夜间模式 | 支持 | `class` 策略，localStorage 持久化 |
| 文件展示 | 卡片网格 | 响应式 1→4 列，含缩略图预览 |
| 包管理 | pnpm | pnpm 11.5.0 |

### 新增文件
| 文件 | 说明 |
|------|------|
| `frontend/package.json` | Vue 3 + Vite + Tailwind + Axios + Vue Router |
| `frontend/vite.config.js` | dev proxy (/api, /thumbnails, /cache → :8000) |
| `frontend/tailwind.config.js` | darkMode: 'class', 自定义 sidebar 色 |
| `frontend/postcss.config.js` | Tailwind + Autoprefixer 管道 |
| `frontend/index.html` | SPA 入口 + dark class body 初始化 |
| `frontend/src/main.js` | createApp + router + global CSS |
| `frontend/src/style.css` | Tailwind directives + 自定义滚动条 + toast 过渡 |
| `frontend/src/App.vue` | 侧边栏 + Header(健康/授权/暗色切换) + toast 系统 |
| `frontend/src/composables/useDarkMode.js` | 暗色模式状态管理 (localStorage) |
| `frontend/src/api/index.js` | 7 个 API 模块 (auth/channels/files/sync/thumb/cache/config) |
| `frontend/src/router/index.js` | 8 条路由 (懒加载) |
| `frontend/src/views/DashboardView.vue` | 统计卡片: 频道数/文件数/缓存使用率/缩略图任务 + 最近同步 |
| `frontend/src/views/AuthView.vue` | 三步登录: 发送验证码 → 验证码 → 2FA |
| `frontend/src/views/ChannelsView.vue` | 频道 CRUD: 列表/添加/发现/删除确认 |
| `frontend/src/views/FilesView.vue` | 频道选择器 + 卡片网格 + 分页 + 下载/缓存/缩略图 |
| `frontend/src/views/SyncView.vue` | 触发同步 + 实时轮询进度 + 任务历史 + 取消 |
| `frontend/src/views/ThumbnailsView.vue` | 统计 + 状态筛选 + 批量生成 + 取消 |
| `frontend/src/views/CacheView.vue` | 统计面板 + 使用率条 + 手动淘汰 |
| `frontend/src/views/SettingsView.vue` | 配置列表 + 编辑弹窗 + 只读保护 + admin 密码 |
| `frontend/dist/` | 生产构建产物 (13 文件, ~180KB gzip) |

### 修改文件
| 文件 | 变更 |
|------|------|
| `main.py` | 新增生产模式前端挂载 (`frontend/dist` → StaticFiles html=True) |
| `AGENT.md` | Step 9 标记完成 |
| `CHANGELOG.md` | 本条目 |
| `README.md` | 前端架构 + 开发命令 |

### 关键设计决策
1. **API 代理**: 开发模式通过 `vite.config.js` proxy 转发 `/api/*` → `localhost:8000`，生产模式 FastAPI 直接挂载静态文件
2. **Toast 系统**: 通过 CustomEvent `app-toast` 实现全局通知，API 拦截器统一分发错误 toast
3. **暗色模式**: `useDarkMode` composable 管理 `<html class="dark">`，`localStorage` 持久化，Initial load 检测系统偏好
4. **路由懒加载**: 所有 8 个视图使用 `() => import(...)` 动态导入，构建时自动 code-split
5. **响应式卡片网格**: `grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4`

### 测试统计: 180/180 PASS (回归全部通过) ✅

---

## Step 8: 配置管理 API — 热更新 DB config ✅ 12/12 PASS

### 新增功能
- **`GET /api/config`**: 列出所有注册配置项（含 key、value、editable、updated_at）
- **`GET /api/config/{key}`**: 获取单个配置值
- **`PUT /api/config/{key}`**: 更新配置值（admin-only，需 `X-Admin-Password` header，立即热更新生效）
- **值类型校验**: int/float/bool 类型自动校验及范围限制
- **只读保护**: `api_id`、`api_hash` 等 6 个敏感 key 禁止通过 API 修改
- **管理员认证**: 复用 `config.py::is_admin()` 通过 `X-Admin-Password` header 认证

### 新增文件
| 文件 | 说明 |
|------|------|
| `api/config.py` | 配置管理 API: GET 列表/单项, PUT 更新 (admin-auth + 校验) |
| `tests/test_config_api.py` | 配置 API 测试 (12 tests) |

### 修改文件
| 文件 | 变更 |
|------|------|
| `config.py` | 新增 `list_all_configs`、`validate_config_value`、`READONLY_CONFIG_KEYS`、`ALL_CONFIG_KEYS`、`CONFIG_VALUE_SCHEMA` |
| `main.py` | 注册 `config_router` |
| `tests/conftest.py` | 修复：添加 `import models`（修复 `Base.metadata` 为空导致 `_reset_tables` 不建表的问题） |

### 场景→测试映射
| 场景 ID | 场景描述 | 对应测试函数 | 类型 |
|---------|---------|-------------|------|
| S1 | 列出所有配置 | `test_list_all_config` | 单元 |
| S2 | 获取单个配置 | `test_get_single_config` | 单元 |
| S3 | 更新配置（管理员） | `test_update_config` | 集成 |
| S4 | 更新不存在的 key | `test_update_nonexistent_key` | 单元 |
| S5 | 未提供密码 | `test_update_no_password` | 单元 |
| S6 | 密码错误 | `test_update_wrong_password` | 单元 |
| S7 | 值类型校验失败 | `test_update_invalid_type` | 单元 |
| — | 获取不存在的 key | `test_get_nonexistent_key` | 单元 |
| — | 更新只读 key | `test_update_readonly_key` | 单元 |
| — | 值超出范围 | `test_update_value_out_of_range` | 单元 |
| — | 布尔值校验 | `test_update_bool_invalid` | 单元 |
| — | 浮点数校验 | `test_update_float_valid` | 单元 |

### 关键设计决策
- **只读保护列表**: `api_id`, `api_hash`, `phone`, `bot_token`, `proxy_url`, `admin_password` — Telegram 凭据不应通过 API 修改（会导致 session 不一致或安全漏洞）
- **类型校验 schema**: `CONFIG_VALUE_SCHEMA` 定义每个 key 的类型及 min/max 范围，利用现有 Settings 字段类型信息
- **热更新**: 已有 `get_settings()` 每次都从 DB 读取，`PUT` 更新后下次调用立即生效，无需重启
- **不可新增/删除**: API 只操作 `ALL_CONFIG_KEYS` 中注册的 key，保持与 Settings 类字段同步
- **conftest 修复**: 添加 `import models` 确保 `Base.metadata` 包含所有 ORM 表，解决测试中 `_reset_tables` fixture 不建表的问题

### 测试统计
- Step 8 新增: 12/12 ✅
- **预期总计: 180/180 PASS** (168 + 12)

---

## Step 7: 缓存管理器 — LRU 淘汰 + 动态上限 ✅ 17/17 PASS

### 新增功能
- **`File` 模型新增列**: `cached_at` / `accessed_at` (DateTime, nullable) — 用于 LRU 淘汰追踪
- **`CacheManager` 服务**: LRU 淘汰策略 (accessed_at ASC)、动态上限实时读取、磁盘文件缺失容错
- **`GET /api/cache/stats`**: 缓存统计概览（总量、文件数、使用率）
- **`POST /api/cache/evict`**: 手动触发 LRU 淘汰至配置上限
- **`_ensure_cached` 集成**: 下载前预检查 + 下载后后检查，两阶段淘汰防止磁盘撑满

### 新增文件
| 文件 | 说明 |
|------|------|
| `services/cache_manager.py` | LRU 淘汰引擎：预检查、后检查、动态上限、缺失容错 |
| `api/cache.py` | 缓存管理 API：stats + manual evict |
| `tests/test_cache_manager.py` | 缓存管理器测试 (17 tests) |

### 修改文件
| 文件 | 变更 |
|------|------|
| `models.py` | `File` 表新增 `cached_at`、`accessed_at` 列 (nullable) |
| `api/files.py` | `_ensure_cached` 集成 CacheManager（预检查 → 下载 → 后检查）；`_file_to_dict` 暴露新时间戳字段 |
| `main.py` | 注册 `cache_router`；lifespan 中创建 cache 目录 |

### 场景→测试映射
| 场景 ID | 场景描述 | 对应测试函数 | 类型 |
|---------|---------|-------------|------|
| S1 | 下载触发 LRU 淘汰 | `test_evict_on_pre_check` | 集成 |
| S2 | 查看缓存统计 | `test_cache_stats` / `test_cache_stats_empty` | 单元 |
| S3 | 手动淘汰 | `test_manual_evict` / `test_manual_evict_already_under` | 单元 |
| S4 | 无限缓存模式 | `test_unlimited_cache` / `test_unlimited_cache_evict_manual` | 单元 |
| S5 | 单文件超限（无其他文件）| `test_single_file_exceeds_limit` / `test_single_file_exceeds_limit_with_other_files` | 单元 |
| S6 | 空间完全不够 | `test_insufficient_space` / `test_insufficient_space_no_evictable` | 单元 |
| S7 | 淘汰时文件缺失 | `test_evict_missing_file` | 单元 |
| S8 | 动态修改上限 | `test_dynamic_limit` | 集成 |

### 关键设计决策
- **两阶段淘汰**: 下载前预检查（`check_and_evict` with new_file_size）避免浪费带宽；下载后后检查（`post_download_check`）处理文件大小不准确场景
- **LRU 排序**: `COALESCE(accessed_at, '1970-01-01')` — NULL 值视为最旧，最先淘汰
- **实时读取**: 每次操作通过 `get_settings(db_session)` 实时读取 `cache_max_size_mb`，支持热更新
- **缺失容错**: 淘汰时磁盘文件不存在 → 跳过删除，只清 DB 字段，继续下一个
- **无限模式**: `cache_max_size_mb=0` 跳过所有检查和淘汰，生产环境建议设置合理值
- **DB 统计代替磁盘扫描**: `SUM(file_size) WHERE is_cached=true` 计算缓存大小，避免遍历文件系统

### 测试统计
- Step 7 新增: 17/17 ✅
- **预期总计: 168/168 PASS** (151 + 17)

---

## Step 6: 缩略图任务队列 (PriorityQueue) ✅ 151/151 PASS

### 新增文件
| 文件 | 说明 |
|------|------|
| `services/task_queue.py` | 生产者-消费者 PriorityQueue worker pool + Pillow 缩略图生成 |
| `api/thumbnails.py` | 缩略图 API: 手动触发、批量提交、任务列表、详情、统计、取消 |
| `tests/test_task_queue.py` | Worker pool 测试 |
| `tests/test_thumbnails_api.py` | 缩略图 API 测试 |

### 修改文件
| 文件 | 变更 |
|------|------|
| `main.py` | 注册 `thumb_router`；lifespan 中启停 worker pool |
| `AGENT.md` | Step 6 场景文档 (S1-S12) + 场景→测试映射 |
| `CHANGELOG.md` | 本条目 |

### 关键设计决策
- **asyncio.PriorityQueue**: 低延迟、无忙等待；服务重启时从 DB 恢复
- **Worker pool**: 在 lifespan 启停，与 TelegramService 一致的生命周期模式
- **缩略图目录**: `thumbnails/{channel_id}/{file_id}.jpg`（与 cache 目录结构一致）
- **仅支持图片缩略图**: 本步只处理 photo/sticker 类型（Pillow）；视频需 ffmpeg（未来）
- **优先级**: photo(3) > sticker(4) > video(4) > document(5)
- **失败重试**: 3 次 + 指数退避 (1s, 2s, 4s)
- **文件缓存集成**: 生成缩略图前复用 `api/files.py` 的 `_download_from_telegram` 确保文件已缓存

### 场景→测试映射
| 场景 ID | 场景描述 | 对应测试函数 | 类型 |
|---------|---------|-------------|------|
| S1 | 手动触发单文件 | `test_trigger_single_file` | 集成 |
| S2 | 查询任务列表过滤 | `test_list_jobs_with_filter` | 单元 |
| S3 | 批量提交 | `test_generate_batch` | 集成 |
| S4 | 查看单个任务 | `test_get_job_detail` | 单元 |
| S5 | 统计概览 | `test_stats` | 单元 |
| S6 | 文件不存在 | `test_trigger_file_not_found` | 单元 |
| S7 | 重复提交冲突 | `test_trigger_duplicate_job` | 单元 |
| S8 | 取消等待中任务 | `test_cancel_pending_job` | 单元 |
| S9 | 取消已完成任务 | `test_cancel_completed_job` | 单元 |
| S10 | 失败后重试成功 | `test_retry_success` | 单元 |
| S11 | 达到最大重试 | `test_retry_exhausted` | 单元 |
| S12 | 启动时恢复 pending | `test_load_pending_on_startup` | 集成 |

### 测试统计
- 新增: 24/24 ✅ (task_queue 11 + thumbnails_api 13)
- **总计: 151/151 PASS ✅**

---

## Step 5: 同步引擎 — Telethon iter → DB ✅ 127/127 PASS

### 新增文件
| 文件 | 说明 |
|------|------|
| `services/sync_engine.py` | 同步引擎核心: iter_messages + 媒体提取(photo/video/audio/sticker/document) + 去重(批量 SELECT + INSERT) + SyncTask 进度追踪 + 取消支持 |
| `api/sync.py` | 同步 API: POST trigger-sync(202, 后台异步), GET list-tasks, GET task-detail, POST cancel |
| `tests/test_sync_engine.py` | 同步引擎测试 (12): 媒体提取(7) + 集成同步(5) |
| `tests/test_sync_api.py` | 同步 API 测试 (12): trigger(4) + list(3) + get(2) + cancel(3) |

### 修改文件
| 文件 | 变更 |
|------|------|
| `main.py` | 注册 `sync_router` |
| `AGENT.md` | Step 5 场景文档 (S1-S10) + 场景→测试映射 |
| `CHANGELOG.md` | 本条目 |

### 关键设计决策
- **后台异步同步**: API 返回 202 后通过 `asyncio.create_task()` 后台运行，通过 `_running_syncs` dict 管理任务生命周期
- **去重策略**: 批量消息中先 SELECT 已存在的 message_id（单次查询），只 INSERT 新记录，避免 N+1
- **增量同步**: 频道 `last_sync` 有值时传入 `offset_date` 只拉取新消息
- **type 检测**: 通过 `DocumentAttributeVideo/Audio/Sticker` 等属性名检测文件类型
- **file_reference**: 只存储真实 bytes 的 `file_reference`（`isinstance` 检查），用于后续下载/缩略图
- **取消机制**: `POST /api/sync/tasks/{id}/cancel` 设置 status=cancelled，同步循环通过 `db.refresh()` 检测并停止

### 场景→测试映射
| 场景 ID | 场景描述 | 对应测试函数 | 类型 |
|---------|---------|-------------|------|
| S1 | 正常同步 | `test_sync_full` / `test_sync_triggered` | 集成 |
| S2 | 增量同步 | `test_sync_incremental` | 集成 |
| S3 | 频道无文件 | `test_sync_empty_channel` | 集成 |
| S4 | 查询任务列表 | `test_list_with_tasks` | 单元 |
| S5 | 查询单个任务 | `test_get_task` | 单元 |
| S6 | 频道不存在 | `test_sync_channel_not_found` | 单元 |
| S7 | 未授权 | `test_sync_unauthorized` | 单元 |
| S8 | 同步进行中冲突 | `test_sync_already_running` | 单元 |
| S9 | 取消同步 | `test_cancel_running_task` | 单元 |
| S10 | 取消已完成任务 | `test_cancel_non_running_task` | 单元 |

### 测试统计
- Step 1-4: 103/103 ✅
- Step 5 新增: 24/24 ✅ (sync_engine 12 + sync_api 12)
- **总计: 127/127 PASS ✅**

---

## Step 4: 文件列表 / 下载 / 缓存 API ✅ 103/103 PASS

### 新增功能
- `GET /api/channels/{channel_id}/files`: 分页文件列表，支持 offset/limit
- `GET /api/files/{file_id}`: 获取单个文件详情
- `GET /api/files/{file_id}/download`: 流式下载（缓存优先，未缓存从 Telegram 拉取）
- `POST /api/files/{file_id}/cache`: 主动缓存文件（从 Telegram 下载到本地）
- `DELETE /api/files/{file_id}/cache`: 清除文件缓存

### 场景→测试映射
| 场景 ID | 场景描述 | 对应测试函数 | 类型 |
|---------|---------|-------------|------|
| S1 | 频道文件列表分页 | `test_list_files_paginated` | 单元 |
| S2 | 频道文件列表默认分页 | `test_list_files_default_pagination` | 单元 |
| S3 | 频道不存在 | `test_list_files_channel_not_found` | 单元 |
| S4 | 频道文件为空 | `test_list_files_empty` | 单元 |
| S5 | 文件详情 | `test_get_file_detail` | 单元 |
| S6 | 文件不存在 | `test_get_file_not_found` | 单元 |
| S7 | 缓存文件 | `test_cache_file` | 集成 |
| S8 | 缓存不授权 | `test_cache_file_unauthorized` | 单元 |
| S9 | 缓存文件不存在 | `test_cache_file_not_found` | 单元 |
| S10 | 幂等缓存 | `test_cache_file_already_cached` | 单元 |
| S11 | 清除缓存 | `test_delete_cache` | 单元 |
| S12 | 幂等删除缓存 | `test_delete_cache_not_cached` | 单元 |
| S13 | 下载已缓存文件 | `test_download_cached_file` | 集成 |
| S14 | 下载未授权 | `test_download_unauthorized` | 单元 |

### 新增文件
| 文件 | 说明 |
|------|------|
| `api/files.py` | 文件 CRUD + 下载 + 缓存管理路由 |
| `tests/test_files_api.py` | 文件 API 测试 (14 tests) |

### 修改文件
| 文件 | 变更 |
|------|------|
| `main.py` | 注册 `files_router` |
| `AGENT.md` | Step 4 场景文档 |
| `CHANGELOG.md` | 本条目 |

### 关键设计决策
- **分页**: offset/limit 模式，默认 limit=50
- **缓存目录**: `data/cache/{channel_id}/{file_id}_{safe_filename}`
- **下载流式**: 使用 Telethon `iter_download()` + FastAPI `StreamingResponse`
- **幂等缓存**: 已缓存文件不重复下载
- **缓存清除**: 已缓存文件从磁盘删除 + DB 字段清空

### 测试统计
- 新增: 14/14 ✅
- **总计: 103/103 PASS ✅**

---

## Step 3: 频道管理 API (CRUD) ✅ 84/84 PASS → 频道发现功能

### 新增功能
- `GET /api/channels/discover`: 从 Telegram dialogs 发现用户关注的频道，自动标记 `already_tracked`
- `_channel_to_dict` 新增 `already_tracked` 字段

### 场景→测试映射
| 场景 ID | 场景描述 | 对应测试函数 | 类型 |
|---------|---------|-------------|------|
| S1 | 通过 username 添加频道 | `test_create_channel_by_username` | 集成 |
| S2 | 列表所有频道 | `test_list_channels` | 单元 |
| S3 | 获取单个频道 | `test_get_channel` | 单元 |
| S4 | 删除频道级联文件 | `test_delete_channel_cascade` | 集成 |
| S5 | 添加不存在的频道 | `test_create_channel_not_found` | 单元 |
| S6 | 重复添加频道 | `test_create_channel_duplicate` | 单元 |
| S7 | 未授权添加 | `test_create_channel_unauthorized` | 单元 |
| S8 | 查询不存在的频道 | `test_get_channel_not_found` | 单元 |
| S9 | 发现频道 | `test_discover_channels` | 集成 |
| S10 | 未授权 discover | `test_discover_channels_unauthorized` | 单元 |
| S11 | 无频道可发现 | `test_discover_channels_empty` | 单元 |
| S12 | 全部已添加 | `test_discover_channels_all_tracked` | 单元 |
| S13 | get_dialogs 异常 | `test_discover_channels_dialogs_error` | 单元 |

### 修改文件
| 文件 | 变更 |
|------|------|
| `api/channels.py` | 新增 `GET /api/channels/discover`；`_channel_to_dict` 支持 `already_tracked`；`_require_authorized_client` 提取为公共函数 |
| `tests/test_channels_api.py` | 新增 5 个 discover 测试 |
| `AGENT.md` | 新增 S9-S13 场景 |
| `CHANGELOG.md` | 本条目 |

### 关键设计决策
- **发现机制**: 使用 Telethon `client.iter_dialogs()` 获取对话列表，过滤 `Channel` 类型
- **already_tracked**: 通过查询数据库对比 tg_id 判断频道是否已添加
- **性能**: `iter_dialogs(limit=200)` 只返回对话元数据，不拉取历史消息

### 测试统计
- Step 1-2: 70/70 ✅
- Step 3 (已有): 14/14 ✅
- Step 3 (discover 新增): 5/5 待验证
- 预期总计: 89/89 PASS

---

## Fix: 配置管理与数据库初始化修复

### 问题
- `main.py` 中混用 `os.environ.get()` 和 `settings` 对象读取配置，不一致
- `database.py` 模块级代码在 `.env` 加载前执行，导致 `DB_PATH` 使用了默认值而非 `.env` 中的值
- `init_db()` 调用时 `models.py` 未被导入，`Base.metadata` 不包含 ORM 模型，导致 `app_config` 等表未被创建

### 修改文件
| 文件 | 变更 |
|------|------|
| `main.py` | 1) 调整导入顺序：`from config import Settings` 移到 `from database import init_db` 之前；2) `os.environ.get("TG_DB_PATH")` → `settings.tg_db_path`；3) `os.environ.get("TG_DATA_DIR")` → `settings.tg_data_dir`；4) 移除无用的 `import os`；5) `init_db()` 前添加 `import models` 注册所有 ORM 模型 |

### 关键设计决策
- **单一配置源**: 所有配置读取统一通过 `Settings` 对象，默认值仅在 `Settings` 类中定义一次
- **导入顺序保证**: `config.py`(加载 .env) → `Settings()` → `database.py`(模块级代码读取 os.environ) → `models.py`(注册 ORM) → `init_db()`(创建表)
- `database.py` 中保留 `os.environ.get()` 合理：模块级变量需在导入时立即确定值，此时环境变量已由 `config.py` 加载

### 测试统计
- **总计: 70/70 PASS ✅**

---

## Step 2.5: 日志子系统 — loguru 统一日志管理 ✅ 70/70 PASS

### 新增文件
| 文件 | 说明 |
|------|------|
| `logging_config.py` | 集中日志配置: loguru 初始化、控制台彩色输出、文件轮转 (10MB×5)、错误日志分离、第三方库静默 |
| `middleware/__init__.py` | 中间件包 |
| `middleware/logging.py` | HTTP 请求日志中间件: 记录 method + path + status + 耗时(ms) |
| `tests/test_logging.py` | 日志系统测试 (13): setup_logging、中间件、Settings 日志字段 |

### 修改文件
| 文件 | 变更 |
|------|------|
| `pyproject.toml` | 添加 `loguru>=0.7` 依赖 |
| `config.py` | 新增 4 个日志配置字段: TG_LOG_LEVEL/LOG_FILE/LOG_ROTATION/LOG_RETENTION; ensure_initialized 添加 seed 数量日志 |
| `main.py` | 启动时 `setup_logging()`; 全链路 lifespan 日志; 注册请求日志中间件; `import os` 提前 |
| `database.py` | `init_db` 添加成功/失败日志 |
| `api/auth.py` | `import logging` → `from loguru import logger` |
| `services/telegram_client.py` | `import logging` → `from loguru import logger` |
| `.env` / `.env.example` | 新增 4 个日志配置环境变量 |
| `README.md` | 更新测试统计、目录结构、环境变量表 |
| `AGENT.md` | 更新目录结构、快速命令 |

### 关键设计决策
- **loguru 替代标准 logging**: 代码量减少约 40%，一行 `from loguru import logger` 替代 `logging.getLogger(__name__)`
- **双输出**: 控制台 (INFO+, 彩色) + 文件 (DEBUG+, 轮转) + 错误文件 (ERROR+, 独立)
- **轮转策略**: 10MB 单文件轮转，保留 5 个备份
- **第三方静默**: telethon/httpx/httpcore/asyncio/aiosqlite 自动设为 WARNING
- **请求日志中间件**: 每个 HTTP 请求记录 method/path/status/耗时

### 测试统计
- Step 1: 30/30 ✅
- Step 2: 27/27 ✅
- Step 2.5 (日志): 13/13 ✅
- **总计: 70/70 PASS ✅**

---

## Doc: 项目文档 — README.md + AGENT.md

### 新增文件
| 文件 | 说明 |
|------|------|
| `README.md` | 完整项目文档: 背景、架构、数据库设计、API 概览、开发计划、快速开始、测试、部署 |
| `AGENT.md` | AI Agent 开发指南: 目录结构、核心模式、开发规则、命令参考 |

### 修改文件
| 文件 | 变更 |
|------|------|
| `pyproject.toml` | 添加 `[tool.hatch.build.targets.wheel]` 配置，修复 `uv run` 构建错误 |

### 测试统计
- 53/53 PASS ✅ (无代码变动)

---

## Step 2: Telegram 客户端 + 认证 API ✅ 53/53 PASS → 57/57 PASS

### 新增文件
| 文件 | 说明 |
|------|------|
| `services/telegram_client.py` | Telethon 客户端封装: TelegramService, AuthState 状态机, 全局实例管理, proxy 解析 |
| `api/auth.py` | 认证路由: POST send-code, POST verify-code, POST verify-2fa, GET status, POST logout |
| `tests/test_telegram_client.py` | Telegram 客户端测试 (13 → 15) |
| `tests/test_auth_api.py` | 认证 API 端点测试 (9) |

### 修改文件
| 文件 | 变更 |
|------|------|
| `main.py` | 注册 auth_router + lifespan 中初始化 TelegramService（含代理配置） |
| `config.py` | 修复 pydantic-settings 双重前缀 Bug：`tg_*` 字段增加 `Field(validation_alias=...)` 正确映射 `TG_API_ID`/`TG_PROXY_URL` 等环境变量 |
| `tests/test_config.py` | 新增 `test_proxy_url_from_env`、`test_proxy_url_default_none`；重构 `test_settings_defaults` → `test_settings_field_types` |

### 关键设计决策
- **AuthState 状态机**: DISCONNECTED → CONNECTING → CODE_SENT → (CODE_VERIFIED|2FA_REQUIRED) → AUTHORIZED
- **全局服务**: `get/set/reset_telegram_service()` 单例模式；启动时从 `Settings` 初始化并注入 proxy
- **认证流**: send_code → verify_code → (verify_2fa) → authorized
- **proxy 解析**: 使用 `python_socks.ProxyType`，支持 socks5/socks4
- **代码简化**: `_ensure_client()` 不过度检查 is_connected，依赖 Telethon 自身幂等性
- **env_prefix Bug 修复**: pydantic-settings 的 `env_prefix: "TG_"` + 字段名 `tg_proxy_url` → 查找 `TG_TG_PROXY_URL`（双重前缀）。使用 `Field(validation_alias="TG_PROXY_URL")` 显式指定环境变量名

### 测试统计
- Step 1: 30/30 ✅
- Step 2 原有: 23/23 ✅
- 本次新增: 4/4 ✅ (config: 2, telegram_client: 2)
- **总计: 57/57 PASS ✅**

---

## Step 1: 项目骨架 — 目录结构、数据库、配置、模型 ✅ 30/30 PASS

### 新增文件
| 文件 | 说明 |
|------|------|
| `pyproject.toml` | 项目依赖配置 (FastAPI, Telethon, SQLAlchemy+aiosqlite, uv) |
| `.env.example` | 环境变量模板 (TG_ 前缀, 17 项配置) |
| `.gitignore` | Git 忽略规则 |
| `database.py` | 异步 SQLite 引擎 + AsyncSessionLocal + @asynccontextmanager + FastAPI get_db |
| `models.py` | 5 张 ORM 表: channels, files (FK→channels), sync_tasks, thumb_jobs, app_config |
| `config.py` | Settings(pydantic-settings) + DB 动态配置 (DB > env > default 优先级) |
| `main.py` | FastAPI 应用入口 + lifespan + CORS + 静态文件挂载 |
| `api/__init__.py` | API 路由包 |
| `services/__init__.py` | 服务层包 |
| `tests/conftest.py` | pytest fixtures: session-scoped engine init + function-scoped table reset |
| `tests/test_database.py` | 数据库连接和 CRUD 测试 (8) |
| `tests/test_config.py` | 配置管理测试 (10) |
| `tests/test_models.py` | ORM 模型约束测试 (12) |

### 测试统计: 30/30 PASS ✅
