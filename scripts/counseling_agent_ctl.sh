#!/bin/bash
# 心理咨询 AI 助手的服务开关：管理 launchd 常驻服务。
# 由使用者交互式 shell 调用（不是 launchd exec 本文件），配合 ~/.zshrc 里的两个别名：
#   start-counseling-agent / stop-counseling-agent
# 用法：bash counseling_agent_ctl.sh {start|stop|restart|status|web-start|web-stop}

UID_NUM="$(id -u)"
LA="$HOME/Library/LaunchAgents"
STREAMLIT_SERVICE="com.andytsang.aitherapist.streamlit"

# 应用服务（唤醒入口 + 网页 + 两个看门狗）——start/stop 管全部。
APP_SERVICES=(
  "com.andytsang.aitherapist.wakegateway"
  "$STREAMLIT_SERVICE"
  "com.andytsang.aitherapist.chatmemorywatcher"
  "com.andytsang.aitherapist.rawingestwatcher"
)

start_service() {
  local service="$1"
  local target="gui/$UID_NUM/$service"
  if ! launchctl print "$target" >/dev/null 2>&1; then
    launchctl bootstrap "gui/$UID_NUM" "$LA/$service.plist" || return 1
  fi
  launchctl kickstart "$target"
}

web_start() {
  if start_service "$STREAMLIT_SERVICE"; then
    echo "✅ Streamlit 启动命令已发送（http://localhost:8502）"
  else
    echo "❌ Streamlit 启动失败，请检查 /tmp/streamlit.log" >&2
    return 1
  fi
}

web_stop() {
  launchctl kill SIGTERM "gui/$UID_NUM/$STREAMLIT_SERVICE" 2>/dev/null || true
  echo "🛑 已停止 Streamlit；8501 唤醒入口仍在运行"
}

start() {
  for s in "${APP_SERVICES[@]}"; do
    if ! start_service "$s" 2>/dev/null; then
      echo "❌ 启动失败：$s" >&2
      return 1
    fi
  done
  echo "✅ 已启动唤醒入口 + 网页 + 看门狗"
  echo
  status
}

stop() {
  for s in "${APP_SERVICES[@]}"; do
    launchctl bootout "gui/$UID_NUM/$s" 2>/dev/null || true
  done
  echo "🛑 已停止网页 + 看门狗"
}

status() {
  echo "— launchd 服务（第二列 0 = 正常）—"
  launchctl list | grep aitherapist || echo "（没有正在运行的服务）"
  echo "— Wake gateway —"
  if curl -s -m 3 http://localhost:8501/_stcore/health 2>/dev/null | grep -q ok; then
    echo "  http://localhost:8501  ✅ ok（固定入口）"
  else
    echo "  未响应（自动唤醒不可用）"
  fi
  echo "— Streamlit —"
  if curl -s -m 3 http://localhost:8502/_stcore/health 2>/dev/null | grep -q ok; then
    echo "  http://localhost:8502  ✅ ok"
  else
    echo "  已停止或仍在启动（访问 8501 可自动唤醒）"
  fi
}

case "$1" in
  start) start ;;
  stop) stop ;;
  restart) stop; sleep 2; start ;;
  web-start) web_start ;;
  web-stop) web_stop ;;
  status) status ;;
  *) echo "用法: bash $0 {start|stop|restart|status|web-start|web-stop}"; exit 1 ;;
esac
