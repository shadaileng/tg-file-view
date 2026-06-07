# AGENT.md — tg_file_viewer 项目开发指南

> AI Agent 快速上手文档：架构、规则、开发计划、操作指令。
>
> **⚠️ 强制指令：收到任何开发/修改需求时，必须先阅读本文件全部内容，
> 然后严格按照第 5 节「开发规则」执行，跳过任意一步需用户明确确认。**

---

## 1. 项目概览

**tg_file_viewer** 是一个单体 FastAPI 服务，替代原有的三服务架构（tg-bot-server / tg_channel_sync / tg_sync_manager），整合 Telegram 频道文件查看、同步、预览、缩略图生成和缓存管理。

- **技术栈**: FastAPI + Telethon + SQLAlchemy/aiosqlite + Vue 3 + Tailwind
- **包管理**: uv (后端) / pnpm (前端)
- **测试**: pytest + pytest-asyncio (后端) + Vitest (前端), TDD 模式
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
│   └── utils.py          # ✅ 工具函数 (utc_iso 等)
├── middleware/           # 中间件层
│   ├── logging.py       # ✅ 请求日志: method + path + status + 耗时ms
├── services/            # 业务逻辑层
│   ├── telegram_client.py  # ✅ TelegramService: Telethon封装, AuthState状态机, 全局单例
│   ├── sync_engine.py      # ✅ 同步引擎 (iter_messages + 去重 + 批量INSERT)
│   ├── task_queue.py       # ✅ 生产者-消费者 PriorityQueue 缩略图任务池
│   └── cache_manager.py    # ✅ LRU淘汰, 动态上限, 手动触发
├── tests/               # 后端测试 (pytest)
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
│   ├── test_task_queue.py     # ✅ 17 tests
│   ├── test_thumbnails_api.py # ✅ 13 tests
│   ├── test_config_api.py    # ✅ 12 tests
│   ├── test_cache_manager.py  # ✅ 12 tests
│   ├── test_post_sync_thumb.py # ✅ 15 tests
│   └── test_data/          # 测试数据
├── frontend/            # ✅ Vue 3 + Vite + Tailwind (vitest, 60 tests)
│   ├── src/views/        # 8 个视图组件
│   ├── src/api/          # Axios API 封装
│   ├── src/composables/  # Vue composables (useDarkMode)
│   ├── src/tests/        # 11 个测试文件, 60 tests
│   └── ...
├── data/                # 运行时数据 (db.sqlite, thumbnails/, cache/)
├── CHANGELOG.md         # 开发日志 (倒叙, 每步更新的详细记录)
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

## 4. 开发进度

| Step | 内容 | 测试数 | 状态 |
|------|------|:------:|:----:|
| 1 | 项目骨架 + DB + 配置 + 模型 | 30 | ✅ |
| 2 | Telegram 客户端 + 认证 API | 23 | ✅ |
| 3 | 频道管理 API (CRUD) | 19 | ✅ |
| 4 | 文件列表 / 下载 / 缓存 API | 14 | ✅ |
| 5 | 同步引擎 (Telethon iter → DB) | 24 | ✅ |
| 6 | 缩略图任务队列 (PriorityQueue) | 24 | ✅ |
| 7 | 缓存管理器 (LRU, 动态上限) | 17 | ✅ |
| 8 | 配置管理 API (热更新 DB config) | 12 | ✅ |
| 9 | Vue 3 + Tailwind 前端 (+ Vitest) | 60 | ✅ |
| 10 | Docker + HF Space 部署 | ~5 | ⏳ |

---

## 5. 开发规则 (必须遵守)

### 5.0 开发全生命周期

任何功能开发/修改必须走完整闭环：

```
需求分析 → 场景设计 → 文档先行 → 切分支 → TDD → 全量测试 → 合并确认
```

### 5.1 需求分析与场景设计

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
- 记录在 AGENTS.md 开发日志之前

#### 5.1.3 文档先行
分析完成后，**先更新文档，再写代码**：
- `AGENTS.md`：记录场景表格
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
- [ ] 后端测试通过 (`uv run pytest tests/ -v`)
- [ ] 前端测试通过 (`pnpm test`)
- [ ] diff 确认无意外改动
- [ ] 文档已同步更新
- [ ] 无 merge conflict
- [ ] 开发日志已记录

#### 5.2.3 合并确认
> **⚠️ 强制规则：必须先使用 `question` 工具获取用户明确肯定答复后，方可执行 `git merge --no-ff`。
> 违反此规则属于严重违规。**

1. 确认检查清单全部 ✔
2. 用 `question` 工具向用户提问「确认合并到 main？」
3. 收到用户明确肯定答复后执行合并
4. 使用 `git merge --no-ff` 保留分支历史
5. 合并后更新 `CHANGELOG.md` 记录测试统计

**禁止**：`--no-edit` 自动合并、未确认 push main、force push main

### 5.3 TDD 开发流程

```
🔴 写测试 → 🟢 最小实现 → 🔧 重构 → 📝 更新CHANGELOG.md → ✅ git commit
```

### 5.4 测试要求

- 每步完成后运行 **全量测试**，必须 **100% 通过**
- 后端：`uv run pytest tests/ -v`
- 前端：`pnpm test` (在 `frontend/` 目录下)
- 覆盖范围：单元测试 + 集成测试 + 回归测试
- 新增测试文件：`tests/test_<module>.py` (后端) / `frontend/src/tests/<name>.test.js` (前端)

#### 5.4.1 场景全覆盖

每条 GIVEN/WHEN/THEN 场景必须有对应的自动化测试。

| 类型 | 命名规范 | 示例 |
|------|---------|------|
| Happy Path | `test_{module}_{happy}` | `test_channel_create_success` |
| 边界条件 | `test_{module}_{edge}` | `test_channel_create_empty_input` |
| 异常流程 | `test_{module}_{error}` | `test_channel_create_duplicate` |

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

每步完成后在 `CHANGELOG.md` **顶部**追加：
- 步骤标题 + 状态
- 新增/修改文件表格
- 关键设计决策
- 测试统计

### 5.7 代码风格

- **异步优先**：所有 I/O 操作使用 async/await
- **类型标注**：所有函数参数和返回值有类型标注
- **异常处理**：API 层捕获并转为 HTTPException
- **无 emoji**：代码中不使用 emoji（测试/CHANGELOG 除外）
- **配置统一来源**：`main.py` 中所有配置读取**必须**通过 `settings` 对象，禁止 `os.environ.get()`
- **UTC 时间戳规范**（三原则）：
  1. **存储层**：所有 `DateTime` 列必须 `DateTime(timezone=True)`，`default` 用 `lambda: datetime.now(timezone.utc)`
  2. **序列化层**：API 输出的 datetime 字符串**一律经过 `api/utils.py::utc_iso()`**，禁止裸调 `.isoformat()`
  3. **写入层**：所有手动赋值 datetime 的地方用 `datetime.now(timezone.utc)`
  
  为什么：SQLite + SQLAlchemy 读回时可能丢失 tzinfo → 裸 `.isoformat()` 产生无时区字符串 → 前端 `new Date()` 解析为本地时间 → 8 小时时差。`utc_iso()` 兜底检测后自动补 `+00:00`。

### 5.8 反模式（禁止）

| 反模式 | 后果 |
|--------|------|
| 拿到需求直接写代码 | 遗漏边界条件和集成点 |
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

### 后端 (pytest)

```python
# conftest.py 提供 fixtures:
# - _engine_cleanup: session-scoped, 一次建库
# - _reset_tables: function-scoped autouse, 每测试前后 drop/create
# - db_session: async session

@pytest.mark.asyncio
class TestFeature:
    async def test_something(self, db_session):
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

### 前端 (Vitest)

```javascript
// vi.hoisted() + vi.mock() 模式注入 Mock
const mockList = vi.hoisted(() => vi.fn())
vi.mock('../api/index', () => ({ channelsApi: { list: mockList } }))

it('测试示例', async () => {
  mockList.mockResolvedValue({ data: [...] })
  const wrapper = mount(MyView)
  await flushPromises()
  expect(wrapper.text()).toContain('...')
})
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
| `TG_THUMB_JOB_TIMEOUT` | 单任务超时秒数 (0=关闭) | 600 |
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

## 9. 场景登记表

### S — 文件浏览无限滚动 + 混合翻页 + 流式预览

| # | 类型 | GIVEN | WHEN | THEN |
|---|------|-------|------|------|
| A1 | 正常流程 | 频道有 120 个文件，limit=50 | 用户滚动到底部 | 追加加载下一批（offset+=50），files 追加，页码不变 |
| A2 | 正常流程 | 文件已加载部分 | 用户输入页码 "3" 后按 Enter | offset=100，替换模式加载，滚动到顶部 |
| A3 | 边界条件 | totalFiles ≤ limit | 页面加载完成 | 不显示分页控件，不显示 sentinel |
| A4 | 边界条件 | 正在 loadingMore | 再次滚动到底部 | 忽略，不重复请求 |
| A5 | 边界条件 | files.length >= totalFiles | 滚动到底部 | 不触发加载，显示「已加载全部」 |
| A6 | 边界条件 | 切换频道 | 点击另一个频道按钮 | offset=0，files 替换，页码归 1 |
| A7 | 异常流程 | 网络错误 | loadMore 失败 | 保留已加载文件，loadingMore 重置，可重试 |
| B1 | 正常流程 | 用户点击图片文件 | 调用 handleView | preview.url = `/api/files/{id}/view`，浏览器渐进渲染 |
| B2 | 正常流程 | 用户点击视频文件 | 调用 handleView | preview.type = 'video'，`<video src="...">` 边下边播 |
| B3 | 异常流程 | 图片加载 404 | img.onerror | preview.error 显示错误信息 |

---

## 10. 快速命令参考

```bash
# ── 后端 ──
uv venv && source .venv/bin/activate && uv pip install -e ".[dev]"   # 安装依赖
uv run uvicorn main:app --host 0.0.0.0 --port 8000 --reload         # 运行服务
uv run pytest tests/ -v                                              # 全量测试
uv run pytest tests/test_auth_api.py -v                              # 单文件测试
uv run pytest tests/ --cov=. --cov-report=term-missing               # 覆盖率

# ── 前端 ──
pnpm install          # 安装依赖 (在 frontend/)
pnpm run dev          # 开发模式
pnpm test             # 运行全部前端测试 (vitest)
pnpm run test:watch   # 监听模式
pnpm exec vitest src/tests/AuthView.test.js   # 单文件测试

# ── Git 提交 ──
git add .
git commit -m "feat(step-N): description ✅ N/N PASS"
```
