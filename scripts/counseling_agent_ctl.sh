#!/bin/bash
# 心理咨询 AI 助手的服务开关：管理 launchd 常驻服务。
# 由使用者交互式 shell 调用（不是 launchd exec 本文件），配合 ~/.zshrc 里的两个别名：
#   start-counseling-agent / stop-counseling-agent
# 用法：bash counseling_agent_ctl.sh {start|stop|restart|status}

UID_NUM="$(id -u)"
LA="$HOME/Library/LaunchAgents"

# 应用服务（网页 + 聊天记忆看门狗 + raw 逐字稿入库看门狗）——start/stop 主要管这几个
APP_SERVICES=(
  "com.andytsang.aitherapist.streamlit"
  "com.andytsang.aitherapist.chatmemorywatcher"
  "com.andytsang.aitherapist.rawingestwatcher"
)
# Tailscale 是整台机器的私网（远程访问要靠它），start 时确保它在，stop 时不动它
TAILSCALE_SERVICE="com.andytsang.aitherapist.tailscale"

start() {
  launchctl bootstrap "gui/$UID_NUM" "$LA/$TAILSCALE_SERVICE.plist" 2>/dev/null \
    || launchctl kickstart "gui/$UID_NUM/$TAILSCALE_SERVICE" 2>/dev/null || true
  for s in "${APP_SERVICES[@]}"; do
    # 没加载过就 bootstrap 加载；已加载就 kickstart 重新拉起
    launchctl bootstrap "gui/$UID_NUM" "$LA/$s.plist" 2>/dev/null \
      || launchctl kickstart "gui/$UID_NUM/$s" 2>/dev/null || true
  done
  echo "✅ 已启动网页 + 看门狗（并确保 Tailscale 在线）"
  echo
  status
}

stop() {
  # 只停应用服务，保留 Tailscale（它是全局私网，别的设备/服务也在用）
  for s in "${APP_SERVICES[@]}"; do
    launchctl bootout "gui/$UID_NUM/$s" 2>/dev/null || true
  done
  echo "🛑 已停止网页 + 看门狗（Tailscale 保持连接；如需断网另跑 'tailscale down'）"
}

status() {
  echo "— launchd 服务（第二列 0 = 正常）—"
  launchctl list | grep aitherapist || echo "（没有正在运行的服务）"
  echo "— Streamlit —"
  if curl -s -m 3 http://localhost:8501/_stcore/health 2>/dev/null | grep -q ok; then
    echo "  http://localhost:8501  ✅ ok"
  else
    echo "  未响应（可能还在启动，或已停止）"
  fi
  echo "— Tailscale —"
  /usr/local/bin/tailscale ip -4 2>/dev/null | sed 's/^/  私网地址: /' || echo "  未连接"
}

case "$1" in
  start) start ;;
  stop) stop ;;
  restart) stop; sleep 2; start ;;
  status) status ;;
  *) echo "用法: bash $0 {start|stop|restart|status}"; exit 1 ;;
esac
