#!/bin/bash
# Streamlit UI 重启脚本
# 固定使用 8502 端口，自动处理端口冲突

set -e

# 配置
PORT=8502
APP_FILE="app.py"
LOG_FILE="/tmp/streamlit_8502.log"
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "🔄 Streamlit UI 重启脚本"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# 1. 检查并杀掉占用端口的进程
echo "📡 [1/4] 检查端口 $PORT..."
PID=$(lsof -ti:$PORT 2>/dev/null || true)

if [ -n "$PID" ]; then
    echo "   ⚠️  端口 $PORT 被进程 $PID 占用"
    echo "   🔫 正在终止进程..."
    kill -9 $PID 2>/dev/null || true
    sleep 1
    echo "   ✅ 旧进程已终止"
else
    echo "   ✅ 端口 $PORT 空闲"
fi

# 2. 切换到项目目录
echo ""
echo "📂 [2/4] 切换到项目目录..."
cd "$PROJECT_DIR"
echo "   📍 当前目录: $(pwd)"

# 3. 激活虚拟环境并启动 Streamlit
echo ""
echo "🚀 [3/4] 启动 Streamlit..."

if [ ! -d ".venv" ]; then
    echo "   ❌ 错误：虚拟环境 .venv 不存在"
    exit 1
fi

# 清空日志文件
> "$LOG_FILE"

# 启动 Streamlit（后台运行）
source .venv/bin/activate
nohup streamlit run "$APP_FILE" \
    --server.port="$PORT" \
    --server.headless=true \
    > "$LOG_FILE" 2>&1 &

NEW_PID=$!
echo "   🆔 新进程 PID: $NEW_PID"
echo "   📝 日志文件: $LOG_FILE"

# 4. 等待并验证启动
echo ""
echo "⏳ [4/4] 等待启动..."
sleep 5

# 检查进程是否存在
if ! ps -p $NEW_PID > /dev/null 2>&1; then
    echo "   ❌ 启动失败！进程已退出"
    echo ""
    echo "━━━━━━━━━━━━ 错误日志 ━━━━━━━━━━━━"
    tail -20 "$LOG_FILE"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    exit 1
fi

# 检查端口是否可访问
if curl -s http://localhost:$PORT > /dev/null 2>&1; then
    echo "   ✅ 启动成功！"
else
    echo "   ⚠️  进程运行中，但端口尚未响应（可能仍在初始化）"
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "✨ Streamlit UI 已启动"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "📍 访问地址: http://localhost:$PORT"
echo "🆔 进程 PID:  $NEW_PID"
echo "📝 查看日志:  tail -f $LOG_FILE"
echo "🛑 停止服务:  kill $NEW_PID"
echo ""
echo "💡 提示：使用 Ctrl+C 或运行 'kill $NEW_PID' 停止服务"
echo ""
