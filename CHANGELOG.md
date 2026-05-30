# 开发日志 (CHANGELOG)

## Step 1: 项目骨架 — 目录结构、数据库、配置、模型 ✅ 30/30 PASS

### 新增文件
| 文件 | 说明 |
|------|------|
| `pyproject.toml` | 项目依赖配置 (FastAPI, Telethon, SQLAlchemy+aiosqlite, uv) |
| `.env.example` | 环境变量模板 (TG_ 前缀, 17 项配置) |
| `.gitignore` | Git 忽略规则 |
| `database.py` | 异步 SQLite 引擎 + AsyncSessionLocal + `@asynccontextmanager` get_session + FastAPI get_db 依赖 |
| `models.py` | 5 张 ORM 表: channels, files (FK→channels), sync_tasks, thumb_jobs, app_config |
| `config.py` | Settings(pydantic-settings) + DB 动态配置 (DB > env > default 优先级) |
| `main.py` | FastAPI 应用入口 + lifespan + CORS + 静态文件挂载(/thumbnails, /cache) |
| `api/__init__.py` | API 路由包 |
| `services/__init__.py` | 服务层包 |
| `tests/conftest.py` | pytest fixtures: session-scoped engine init + function-scoped table reset |
| `tests/test_database.py` | 数据库连接和 CRUD 测试 (8) |
| `tests/test_config.py` | 配置管理测试: env/Settings/DB-config/get_settings/ensure_initialized (10) |
| `tests/test_models.py` | ORM 模型约束测试: 创建/默认值/唯一约束/FK (12) |

### 关键设计决策
- **配置优先级**: DB `app_config` > 环境变量 > 默认值
- **数据库**: `aiosqlite` + SQLAlchemy async，`check_same_thread=False`
- **测试隔离**: session-scoped 建库 + function-scoped drop/create tables
- **tg_ref**: 同步时设置，默认为 None（非自动生成）
- **File.channel_id**: 使用 ForeignKey("channels.id") 关联

### 测试统计
- **新增**: 30 个测试
- **通过**: 30/30 ✅
- **失败**: 0
- **警告**: 1 (SAWarning, 测试场景预期的键冲突警告)
