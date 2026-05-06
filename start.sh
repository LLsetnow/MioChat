#!/bin/sh
# MioChat 快速启动脚本
# 同时启动后端 (Python aiohttp) 和前端 (Vite dev server)

set -e

BACKEND_DIR="$(cd "$(dirname "$0")" && pwd)"
FRONTEND_DIR="$BACKEND_DIR/frontend"
BACKEND_PID=""

cleanup() {
    echo ""
    echo "正在关闭服务..."
    [ -n "$BACKEND_PID" ] && kill "$BACKEND_PID" 2>/dev/null
    exit 0
}
trap cleanup INT TERM

# 参数解析
START_BACKEND=1
START_FRONTEND=1
for arg in "$@"; do
    case "$arg" in
        --backend-only) START_FRONTEND=0 ;;
        --frontend-only) START_BACKEND=0 ;;
        --help)
            echo "用法: $0 [--backend-only | --frontend-only]"
            exit 0
            ;;
    esac
done

# 检查 .env
if [ "$START_BACKEND" -eq 1 ] && [ ! -f "$BACKEND_DIR/.env" ]; then
    if [ -f "$BACKEND_DIR/.env.example" ]; then
        echo "⚠️  未发现 .env 文件，正在从 .env.example 创建..."
        cp "$BACKEND_DIR/.env.example" "$BACKEND_DIR/.env"
        echo "   请编辑 .env 填入 API Key 后重新启动"
    else
        echo "❌ 未发现 .env 文件"
    fi
    exit 1
fi

# 后端
if [ "$START_BACKEND" -eq 1 ]; then
    echo "📦 安装 Python 依赖..."
    pip install -q -r "$BACKEND_DIR/requirements.txt"

    echo "🚀 启动后端 (src/server.py)..."
    cd "$BACKEND_DIR"
    python src/server.py &
    BACKEND_PID=$!
fi

# 前端
if [ "$START_FRONTEND" -eq 1 ]; then
    echo "📦 安装前端依赖..."
    cd "$FRONTEND_DIR"
    npm install --silent

    echo "🚀 启动前端 (Vite dev server)..."
    cd "$FRONTEND_DIR"
    npm run dev &
    FRONTEND_PID=$!
fi

echo ""
echo "✅ MioChat 启动中..."
echo "   后端: http://localhost:9902"
echo "   前端: http://localhost:5173"
echo ""
echo "按 Ctrl+C 停止所有服务"

wait
