---
name: tdd-dev-workflow
description: 完整的 TDD 开发流程：需求分析 → GIVEN/WHEN/THEN 场景 → 文档先行 → 按类型执行(api/ui/config) → 按类型测试 → 合并确认
---

# tdd-dev-workflow

严格按以下步骤执行。跳过任意一步需用户明确确认。

## Step 1 ─ 需求分析矩阵

| 维度 | 内容 |
|------|------|
| 提出者 | 谁提出的需求 |
| 解决什么问题 | 一话描述问题 |
| 变更范围 | 逐点列出：变更点、影响模块、是否破坏性变更 |

**边界条件**（至少 3 条）：列出异常场景及预期行为。

---

## Step 2 ─ GIVEN/WHEN/THEN 场景 + 变更类型标记

**格式**：
```
GIVEN [前置条件]
WHEN  [触发动作]
THEN  [预期结果]
```

- 至少 1 条正常 + 2 条异常场景
- 在 GIVEN/WHEN/THEN 上方或下方标记 **变更类型**：`api` / `ui` / `config`
- 记录到 CHANGELOG.md 变更条目下（Step 3 的预留条目中已包含）

---

## Step 3 ─ 文档先行（预期声明）

| 文件 | 更新条件 | 内容 |
|------|---------|------|
| `CHANGELOG.md` | **必须** | 在头部预留变更条目：变更范围 + 场景 + 边界条件 |
| `.env.example` | 新增环境变量时 | 加占位行 + 注释说明 |
| `AGENTS.md` | 仅在项目规则/架构**预计**变更时 | 同步更新，保持与预期一致 |

> AGENTS.md 日常开发不应因新增功能而修改，只在项目公约、目录结构、技术架构变更时更新。

---

## Step 4 ─ 切分支

命名规则：`feat/<desc>` / `fix/<desc>` / `refactor/<desc>` / `docs/<desc>` / `test/<desc>`

```bash
git checkout -b <type>/<description>
```

---

## Step 5 ─ 按变更类型执行

| 类型 | 执行方式 |
|------|---------|
| `api` / `model` | 🔴 写测试 → 🟢 最小实现 |
| `ui` / `css` | 🟢 实现 → 🖼️ 描述验证方式（预期截图区域、可能受影响的视图） |
| `config` / `文档` | 📝 直接修改 |

---

## Step 6 ─ 用户视觉确认（仅 `ui` / `css` 变更）

- 等待用户确认视觉效果
- **确认** → 继续下一步
- **需调整** → 返回 Step 5

---

## Step 7 ─ 按变更类型执行全量测试

| 变更类型 | 测试命令 |
|----------|---------|
| `api` / `model` | `uv run pytest tests/ -v` |
| `ui` / `css` | `cd frontend && pnpm test` |
| 前后端兼有 | 分别执行以上两条 |
| `config` / `文档` | 跳过 |

---

## Step 8 ─ 更新文档（实现后同步）

| 文件 | 操作 |
|------|------|
| `CHANGELOG.md` | **必须**：将 Step 3 预留条目中的预期场景更新为实际实现内容 |
| `AGENTS.md` | 仅当实现与 Step 3 预期不符时补更 |
| `README.md` | 检查以下场景并按需同步：<br>├─ 新增 API 端点 / 路由<br>├─ 新增模块 / 目录结构变更<br>├─ 新增环境变量（同步 `.env.example`）<br>├─ 数据库表 / 模型变更<br>└─ 新增前端视图 / 路由 |

---

## Step 9 ─ Git commit

```
feat(step-N): 简短描述 ✅ N/N PASS

详细变更列表

Tests: N/N PASS
```

---

## Step 10 ─ 合并确认

**合并前检查清单**：
- [ ] 全量测试通过（对应 Step 7 的一条或多条命令）
- [ ] diff 确认无意外改动
- [ ] 文档已同步更新（CHANGELOG / README / AGENTS.md / .env.example）
- [ ] 无 merge conflict

**流程**：
1. 确认检查清单全部 ✔
2. 一句话说明变更内容
3. **等待用户确认**后执行合并
4. 使用 `git merge --no-ff` 保留分支历史
