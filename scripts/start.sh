#!/usr/bin/env bash
# ============================================================
# tg_file_viewer 生产模式启动
#
# 用法:
#   chmod +x scripts/start.sh
#   ./scripts/start.sh
#
# 功能:
#   - 构建 Vue 前端 (pnpm build → dist/)
#   - 启动 uvicorn (单端口 8000，后端 serve SPA)
#   - 自动清理端口 8000 残留进程
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PORT=8000

# ── 1. 安装 & 构建前端 ────────────────────────────────────
echo "📦 构建前端..."
cd "$PROJECT_DIR/frontend"

if [ ! -d "node_modules" ]; then
    echo "   ⏳ 安装前端依赖 (pnpm install)..."
    pnpm install
fi

pnpm build
echo "   ✅ 前端构建完成 → frontend/dist/"

cd "$PROJECT_DIR"

# ── 2. 清理端口 ───────────────────────────────────────────
echo ""
echo "🔍 检查端口 ${PORT}..."
fuser -k ${PORT}/tcp 2>/dev/null && echo "   ⚠️  已释放端口 ${PORT}" || echo "   ✅ 端口 ${PORT} 空闲"

# ── 3. 启动后端 (生产模式) ────────────────────────────────
echo ""
echo "🚀 启动服务 (生产模式)"
echo "   访问: http://localhost:${PORT}"
echo "   (后端 serve SPA + API，无需单独启动前端)"
echo ""
echo "按 Ctrl+C 停止"
echo "════════════════════════════════════════════════════════"

uv run uvicorn main:app --host 0.0.0.0 --port ${PORT}
