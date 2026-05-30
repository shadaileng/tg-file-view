# TG File Viewer

> 单体 Telegram 频道文件预览服务 — 替代三服务架构，整合文件同步、预览、缩略图生成、缓存管理。

[![Tests](https://img.shields.io/badge/tests-70/70%20PASS-brightgreen)](tests/)
[![Python](https://img.shields.io/badge/python-3.11+-blue)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688)](https://fastapi.tiangolo.com)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)

---

## 目录

- [项目背景](#项目背景)
- [技术栈](#技术栈)
- [项目结构](#项目结构)
- [架构设计](#架构设计)
- [数据库设计](#数据库设计)
- [API 概览](#api-概览)
- [开发计划](#开发计划)
- [快速开始](#快速开始)
- [测试](#测试)
- [部署](#部署)
- [开发规则](#开发规则)

---

## 项目背景

**为什么重构？** 原有三服务架构（`tg-bot-server` + `tg_channel_sync` + `tg_sync_manager`）存在以下痛点：

| 问题 | 原架构 | 新架构 |
|------|--------|--------|
| 部署复杂度 | 3 个服务，各自配置、认证 | 1 个单体服务 |
| 服务间依赖 | 链式调用，故障隔离难 | 内部直接调用 |
| 配置管理 | 各服务独立 `.env` | 统一 DB 动态配置 |
| 前端 | 无 | Vue 3 + Tailwind CSS |

**设计目标**：单体服务，统一管理 Telegram 频道的文件同步、缩略图生成、预览和缓存。

---

## 技术栈

| 层级 | 技术 | 工具 |
|------|------|------|
| Web 框架 | FastAPI 0.115+ | — |
| 异步 | asyncio + uvicorn | — |
| Telegram | Telethon 1.32+ | cryptg 加密加速 |
| 数据库 | SQLAlchemy 2.0 + aiosqlite | — |
| 配置 | pydantic-settings + python-dotenv | — |
| 日志 | loguru 0.7+ | 控制台彩色 + 文件轮转 |
| 图片处理 | Pillow 10+ | — |
| 代理 | python-socks 2.0+ | socks5/socks4 |
| 前端 | Vue 3 + Vite + Tailwind | pnpm |
| 包管理 | uv (后端) / pnpm (前端) | — |
| 测试 | pytest + pytest-asyncio + httpx | — |
| 部署 | 本地 / Docker / HF Space | — |

---

## 项目结构

```
tg_file_viewer/
├── main.py                 # FastAPI 入口，lifespan，路由注册
├── config.py               # Settings + DB 动态配置 (DB > env > default)
├── database.py             # 异步 SQLite 引擎 + 会话管理
├── models.py               # 5 张 ORM 表 (channels/files/sync_tasks/thumb_jobs/app_config)
├── logging_config.py       # loguru 日志配置 (控制台 + 文件轮转)
├── pyproject.toml          # uv 项目配置
├── .env.example            # 环境变量模板
│
├── middleware/              # 中间件
│   ├── __init__.py
│   └── logging.py          # 请求日志中间件
├── api/                    # API 路由层
│   ├── __init__.py
│   ├── auth.py             # 认证路由 (已实现 ✅)
│   ├── channels.py         # 频道管理 (待实现)
│   ├── files.py            # 文件列表/预览 (待实现)
│   ├── sync.py             # 同步触发/管理 (待实现)
│   ├── thumbnails.py       # 缩略图管理 (待实现)
│   └── config.py           # 配置管理 API (待实现)
│
├── services/               # 业务逻辑层
│   ├── __init__.py
│   ├── telegram_client.py  # Telethon 客户端封装 (已实现 ✅)
│   ├── sync_engine.py      # 同步引擎 (待实现)
│   ├── task_queue.py       # 生产者-消费者任务池 (待实现)
│   └── cache_manager.py    # LRU 缓存管理器 (待实现)
│
├── frontend/               # Vue 3 前端 (待实现)
│
├── tests/                  # 测试
│   ├── conftest.py         # pytest fixtures
│   ├── test_database.py    # DB 测试 (8)
│   ├── test_config.py      # 配置测试 (10)
│   ├── test_models.py      # 模型测试 (12)
│   ├── test_telegram_client.py  # Telegram 客户端测试 (15)
│   ├── test_auth_api.py    # 认证 API 测试 (9)
│   └── test_logging.py     # 日志系统测试 (13)
│
├── data/                   # 运行时数据
│   ├── db.sqlite           # SQLite 数据库
│   ├── thumbnails/         # 缩略图文件
│   └── cache/              # 缓存文件
│
└── CHANGELOG.md            # 开发日志
```

---

## 架构设计

### 配置层级（三层优先级）

```
DB app_config > .env 环境变量 > Settings 默认值
     │               │               │
     │  动态热更新    │   首次 seed   │   硬编码兜底
     ▼               ▼               ▼
```

- **`Settings`** (pydantic-settings): 从环境变量加载，`TG_` 前缀
- **`app_config`** 表: 运行时通过 API 动态修改，重启生效
- **`ensure_initialized()`**: 启动时从 `.env` 写入 DB（仅首次）

### 认证流程

```
TelegramService (全局单例)
    │
    ├─ send_code()    → 发送验证码  → CODE_SENT
    ├─ verify_code()  → 验证码登录   → AUTHORIZED | 2FA_REQUIRED
    ├─ verify_2fa()   → 两步验证     → AUTHORIZED
    └─ logout()       → 登出        → LOGGED_OUT
```

- 支持**用户手机号**和 **Bot Token** 两种认证
- 支持 socks5/socks4 代理
- 会话文件持久化（`tg_file_viewer.session`）

### 数据模型关系

```
Channel (1) ──< (N) File
    │   tg_id (unique)
    │   username
    │   title
    │   file_count
    │   total_size
    │   last_sync
    │
    └── File
        │   channel_id (FK)
        │   message_id
        │   file_name, file_size
        │   mime_type, file_type
        │   thumb_path, thumb_type
        │   cache_path, is_cached
        │   tg_ref (Telethon引用)

SyncTask   — 每次同步产生一条记录
ThumbJob   — 每个缩略图任务可追踪、可管理
AppConfig  — key-value 动态配置
```

---

## 数据库设计

| 表名 | 说明 | 关键字段 |
|------|------|----------|
| `channels` | Telegram 频道 | tg_id (unique), username, title, file_count, last_sync |
| `files` | 文件元数据 | channel_id (FK), message_id, file_type, thumb_path, cache_path, is_cached |
| `sync_tasks` | 同步任务追踪 | UUID, status, total_files, synced_files, errors |
| `thumb_jobs` | 缩略图生成任务 | UUID, status, priority, strategy, attempt, max_retries |
| `app_config` | 动态配置 | key (PK), value, updated_at |

---

## API 概览

### 已实现 ✅

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/` | 根路径，服务信息 |
| GET | `/api/health` | 健康检查 |
| POST | `/api/auth/send-code` | 发送 Telegram 验证码 |
| POST | `/api/auth/verify-code` | 验证登录码 |
| POST | `/api/auth/verify-2fa` | 两步验证 |
| GET | `/api/auth/status` | 认证状态 |
| POST | `/api/auth/logout` | 登出 |

### 待实现 ⏳

| 方法 | 路径 | 说明 |
|------|------|------|
| GET/POST | `/api/channels` | 频道列表 / 新增 |
| GET/DELETE | `/api/channels/{id}` | 频道详情 / 删除 |
| GET | `/api/channels/{id}/files` | 频道文件列表 |
| GET | `/api/files/{id}` | 文件详情 |
| GET | `/api/files/{id}/download` | 文件下载 |
| POST | `/api/files/{id}/cache` | 主动缓存 |
| DELETE | `/api/files/{id}/cache` | 清除缓存 |
| POST | `/api/sync/{channel_id}` | 触发同步 |
| GET | `/api/sync/tasks` | 同步任务列表 |
| GET | `/api/sync/tasks/{id}` | 任务详情 |
| POST | `/api/sync/tasks/{id}/cancel` | 取消同步 |
| GET | `/api/thumbnails/jobs` | 缩略图任务列表 |
| POST | `/api/thumbnails/jobs/{id}/cancel` | 取消缩略图任务 |
| POST | `/api/thumbnails/jobs/{id}/retry` | 重试缩略图任务 |
| DELETE | `/api/thumbnails/{id}` | 删除缩略图文件 |
| GET | `/api/config` | 获取所有配置 |
| PUT | `/api/config/{key}` | 更新配置项 |

---

## 开发计划

采用 **TDD（测试驱动开发）** 模式，10 步迭代：

| 步骤 | 内容 | 测试数 | 状态 |
|------|------|--------|------|
| Step 1 | 项目骨架 — 目录、数据库、配置、模型 | 30 | ✅ 已完成 |
| Step 2 | Telegram 客户端 + 认证 API | 23 | ✅ 已完成 |
| Step 3 | 频道管理 API | ~12 | ⏳ 待实施 |
| Step 4 | 文件列表 / 下载 API | ~14 | ⏳ 待实施 |
| Step 5 | 同步引擎 (Telethon iter_messages) | ~16 | ⏳ 待实施 |
| Step 6 | 缩略图任务队列 (生产者-消费者) | ~18 | ⏳ 待实施 |
| Step 7 | 缓存管理器 (LRU, 动态上限) | ~10 | ⏳ 待实施 |
| Step 8 | 配置管理 API (热更新) | ~8 | ⏳ 待实施 |
| Step 9 | Vue 3 + Tailwind 前端 | ~15 | ⏳ 待实施 |
| Step 10 | Docker 多阶段构建 + HF Space 部署 | ~5 | ⏳ 待实施 |

**当前进度**: 2/10 步完成，70 个测试全部通过。

---

## 快速开始

### 环境要求

- Python 3.11+
- uv (Python 包管理)
- pnpm (前端包管理，暂不需要)
- Telegram API 凭证 ([my.telegram.org](https://my.telegram.org))

### 安装

```bash
# 克隆项目
cd tg_file_viewer

# 创建虚拟环境并安装依赖
uv venv
source .venv/bin/activate
uv pip install -e ".[dev]"

# 配置环境变量
cp .env.example .env
# 编辑 .env，填入 TG_API_ID 和 TG_API_HASH
```

### 运行

```bash
# 启动开发服务器
uvicorn main:app --host 0.0.0.0 --port 8000 --reload

# 或使用 uv
uv run uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

### 认证

```bash
# 1. 发送验证码
curl -X POST http://localhost:8000/api/auth/send-code

# 2. 验证登录码
curl -X POST http://localhost:8000/api/auth/verify-code \
  -H "Content-Type: application/json" \
  -d '{"code": "12345"}'

# 3. 如需要两步验证
curl -X POST http://localhost:8000/api/auth/verify-2fa \
  -H "Content-Type: application/json" \
  -d '{"password": "your_2fa_password"}'

# 4. 检查状态
curl http://localhost:8000/api/auth/status
```

---

## 测试

```bash
# 运行所有测试
uv run pytest tests/ -v

# 运行特定模块
uv run pytest tests/test_auth_api.py -v

# 带覆盖率报告
uv run pytest tests/ --cov=. --cov-report=term

# 当前测试统计
# ✅ 70/70 PASS (Step 1: 30 + Step 2: 27 + Step 2.5: 13)
```

测试采用 **pytest + pytest-asyncio**，测试数据库与生产隔离：
- session-scoped：一次建库
- function-scoped：每个测试前后 drop/create 表
- 测试数据目录：`tests/test_data/`（自动清理）

---

## 部署

### 本地运行

```bash
cd tg_file_viewer
uv run uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

> 注意：必须使用 `uv run` 启动以确保虚拟环境和依赖正确加载。启动时自动初始化数据库（`data/db.sqlite`），所有表由 `models.py` 注册。

### Docker（待实现）

多阶段构建：构建前端 → 打包后端 → 生产镜像。

### Hugging Face Space（待实现）

`Dockerfile` + `README.md` 适配 HF Space 环境。

---

## 开发规则

1. **TDD 模式**：先写测试 🔴 → 最小实现 🟢 → 重构 🔧
2. **测试必须通过**：每步完成后全量测试 100% 通过
3. **记录开发日志**：更新 `CHANGELOG.md`
4. **Git 提交**：使用 `feat(step-N):` 格式，标注测试结果
5. **异步优先**：数据库、HTTP、文件操作全部使用 asyncio
6. **DB 配置优先**：运行时配置优先从 `app_config` 表读取

### Commit 格式

```
feat(step-N): description ✅ N/N PASS

细分变更说明

Tests: N/N PASS
```

---

## License

MIT
