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
