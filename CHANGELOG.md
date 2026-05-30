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
