#!/usr/bin/env bash
# ============================================================
# tg_file_viewer 开发模式一键启动
#
# 用法:
#   chmod +x scripts/dev.sh
#   ./scripts/dev.sh
#
# 功能:
#   - 自动清理端口 8000/5173 残留进程
#   - 后台启动 uvicorn (--reload 热重载)
#   - 前台启动 Vite dev server (热重载)
#   - Ctrl+C 自动清理所有进程
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

BACKEND_PORT=8000
FRONTEND_PORT=5173
BACKEND_PID=""

# ── 清理函数 ──────────────────────────────────────────────
cleanup() {
    echo ""
    echo "🛑 正在停止服务..."
    if [ -n "$BACKEND_PID" ] && kill -0 "$BACKEND_PID" 2>/dev/null; then
        kill "$BACKEND_PID" 2>/dev/null
        echo "   ✅ 后端已停止 (PID $BACKEND_PID)"
    fi
    # 再次确认端口释放
    fuser -k ${BACKEND_PORT}/tcp 2>/dev/null || true
    fuser -k ${FRONTEND_PORT}/tcp 2>/dev/null || true
    echo "   ✅ 端口已释放"
    exit 0
}

trap cleanup EXIT INT TERM

# ── 1. 清理端口残留 ──────────────────────────────────────
echo "🔍 检查端口占用..."
fuser -k ${BACKEND_PORT}/tcp 2>/dev/null && echo "   ⚠️  已释放端口 ${BACKEND_PORT}" || echo "   ✅ 端口 ${BACKEND_PORT} 空闲"
fuser -k ${FRONTEND_PORT}/tcp 2>/dev/null && echo "   ⚠️  已释放端口 ${FRONTEND_PORT}" || echo "   ✅ 端口 ${FRONTEND_PORT} 空闲"

# ── 2. 后台启动后端 ──────────────────────────────────────
echo ""
echo "🚀 启动后端 (uvicorn --reload :${BACKEND_PORT})..."
cd "$PROJECT_DIR"
uv run uvicorn main:app --host 0.0.0.0 --port ${BACKEND_PORT} --reload &
BACKEND_PID=$!

# ── 3. 等待后端就绪 ──────────────────────────────────────
echo "⏳ 等待后端就绪..."
for i in $(seq 1 30); do
    if curl -s http://localhost:${BACKEND_PORT}/api/health > /dev/null 2>&1; then
        echo "   ✅ 后端已就绪"
        break
    fi
    if [ "$i" -eq 30 ]; then
        echo "   ❌ 后端启动超时，请检查日志"
        exit 1
    fi
    sleep 1
done

# ── 4. 前台启动前端 ──────────────────────────────────────
echo ""
echo "🎨 启动前端 (Vite dev :${FRONTEND_PORT})..."
echo "   前端访问: http://localhost:${FRONTEND_PORT}"
echo "   API 代理: http://localhost:${FRONTEND_PORT}/api → :${BACKEND_PORT}"
echo ""
echo "按 Ctrl+C 停止所有服务"
echo "════════════════════════════════════════════════════════"

cd "$PROJECT_DIR/frontend"
pnpm dev
