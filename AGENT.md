# AGENT.md — tg_file_viewer 项目开发指南

> AI Agent 快速上手文档：架构、规则、开发计划、操作指令。

---

## Current Phase: Step 3 — 频道管理 API (CRUD)

**分支**: `feat/channel-management-api`

### API 端点设计

| 方法 | 路径 | 说明 | 请求体 |
|------|------|------|--------|
| `GET` | `/api/channels/discover` | 🆕 发现用户关注的频道（从 Telegram dialogs） | — |
| `GET` | `/api/channels` | 列表所有已添加频道 | — |
| `GET` | `/api/channels/{id}` | 获取单个频道详情 | — |
| `POST` | `/api/channels` | 添加频道（通过 username 或 tg_id） | `{ "username": "..." }` 或 `{ "tg_id": 123 }` |
| `DELETE` | `/api/channels/{id}` | 删除频道（级联删除文件） | — |

### 变更范围矩阵

| 变更点 | 影响模块 | 破坏性变更 |
|--------|---------|:---:|
| 新增 `api/channels.py` (CRUD 路由) | API 层 | 否 |
| 新增 `GET /api/channels/discover` (频道发现) | API 层 | 否 |
| 在 `main.py` 注册 `channels_router` | 入口 | 否 |
| 调用 `TelegramService.get_client()` 解析频道/dialogs | 服务层 | 否 |
| 新增 `tests/test_channels_api.py` | 测试 | 否 |
| 更新 AGENT.md / CHANGELOG.md | 文档 | 否 |

### 边界条件与失败模式

| # | 场景 | 预期行为 |
|---|------|---------|
| E1 | 添加不存在的频道（无效 username/tg_id） | 返回 404 |
| E2 | 重复添加同一频道 | 返回 409（tg_id 唯一约束） |
| E3 | Telegram 客户端未授权时添加 | 返回 400 |
| E4 | 删除不存在的频道 | 返回 404 |
| E5 | 请求体为空 / 无效字段 | 返回 422 (Pydantic 校验) |
| E6 | 发现频道时 Telegram 未授权 | 返回 400 |
| E7 | 发现频道时 get_dialogs 失败 | 返回 500 |
| E8 | 用户没有关注任何频道 | 返回 200，空数组 |

### GIVEN/WHEN/THEN 场景

#### S1 — Happy Path: 通过 username 添加频道
```
GIVEN Telegram 客户端已授权，@test_channel 真实存在
WHEN  POST /api/channels  { "username": "test_channel" }
THEN 返回 201，tg_id、username、title 已填充，file_count=0
```

#### S2 — Happy Path: 列表所有频道
```
GIVEN 数据库有 2 条 channel 记录
WHEN  GET /api/channels
THEN 返回 200，数组含 2 条记录
```

#### S3 — Happy Path: 获取单个频道
```
GIVEN channel id=1 存在
WHEN  GET /api/channels/1
THEN 返回 200，含完整字段
```

#### S4 — Happy Path: 删除频道
```
GIVEN channel id=1 存在 + 关联 3 个文件
WHEN  DELETE /api/channels/1
THEN 返回 204，channels 和 files 中相关记录均删除
```

#### S5 — Edge: 添加不存在的频道
```
GIVEN Telegram 已授权
WHEN  POST /api/channels  { "username": "not_a_real_channel_xyz" }
THEN 返回 404，detail 含 "not found"
```

#### S6 — Edge: 重复添加
```
GIVEN @test_channel 已存在
WHEN  POST /api/channels  { "username": "test_channel" }
THEN 返回 409，detail 含 "already exists"
```

#### S7 — Edge: 客户端未授权
```
GIVEN Telegram 未登录
WHEN  POST /api/channels  { "username": "any_channel" }
THEN 返回 400，detail 含 "not authorized" 或 "login first"
```

#### S8 — Edge: 查询不存在的频道
```
GIVEN channel id=999 不存在
WHEN  GET /api/channels/999
THEN 返回 404
```

#### S9 — Happy Path: 发现频道
```
GIVEN Telegram 已授权，用户关注了 3 个频道（1 个已在系统数据库中）
WHEN  GET /api/channels/discover
THEN 返回 200，数组含 3 条记录，1 条 already_tracked=true，2 条 false
```

#### S10 — Edge: 未授权时 discover
```
GIVEN Telegram 未授权
WHEN  GET /api/channels/discover
THEN 返回 400，detail 含 "not authorized"
```

#### S11 — Edge: 无频道可发现
```
GIVEN Telegram 已授权，但用户没有加入任何频道
WHEN  GET /api/channels/discover
THEN 返回 200，空数组 []
```

#### S12 — Edge: 全部频道已添加
```
GIVEN Telegram 已授权，用户关注的频道全部已在系统中
WHEN  GET /api/channels/discover
THEN 返回 200，每条记录 already_tracked=true
```

#### S13 — Edge: get_dialogs 异常
```
GIVEN Telegram 已授权，但 Telethon iter_dialogs 抛出异常
WHEN  GET /api/channels/discover
THEN 返回 500，含错误详情
```

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
│   ├── channels.py      # ✅ 频道CRUD: create(list), read(get/list), delete (14 tests)
│   ├── files.py         # ⏳ 文件列表/下载/缓存
│   ├── sync.py          # ⏳ 同步触发/管理
│   ├── thumbnails.py    # ⏳ 缩略图任务管理
│   └── config.py        # ⏳ 配置管理API
├── middleware/           # 中间件层
│   ├── logging.py       # ✅ 请求日志: method + path + status + 耗时ms
├── services/            # 业务逻辑层
│   ├── telegram_client.py  # ✅ TelegramService: Telethon封装, AuthState状态机, 全局单例
│   ├── sync_engine.py      # ⏳ 同步引擎 (iter_messages + 去重 + 批量INSERT)
│   ├── task_queue.py       # ⏳ 生产者-消费者 PriorityQueue 缩略图任务池
│   └── cache_manager.py    # ⏳ LRU缓存, 动态上限, 手动触发
├── tests/               # 测试文件 (与源文件一一对应)
│   ├── conftest.py         # fixtures: session-scoped建库, function-scoped reset tables
│   ├── test_database.py    # ✅ 8 tests
│   ├── test_config.py      # ✅ 10 tests
│   ├── test_models.py      # ✅ 12 tests
│   ├── test_telegram_client.py # ✅ 15 tests
│   ├── test_auth_api.py    # ✅ 9 tests
│   └── test_logging.py     # ✅ 13 tests
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
| 3 | 频道管理 API (CRUD) | ~12 | ✅ |
| 4 | 文件列表 / 下载 / 缓存 API | ~14 | ⏳ |
| 5 | 同步引擎 (Telethon iter → DB) | ~16 | ⏳ |
| 6 | 缩略图任务队列 (PriorityQueue) | ~18 | ⏳ |
| 7 | 缓存管理器 (LRU, 动态上限) | ~10 | ⏳ |
| 8 | 配置管理 API (热更新 DB config) | ~8 | ⏳ |
| 9 | Vue 3 + Tailwind 前端 | ~15 | ⏳ |
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
