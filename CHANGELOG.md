# 开发日志 (CHANGELOG)

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
