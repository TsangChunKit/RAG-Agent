"""后台看门狗：如果距离最近一次聊天已经过了 IDLE_MINUTES 分钟，且这段新对话还没被汇总过，
就自动跑一次 update_chat_memory() + build_chat_graph()（都用 config.GEMINI_SUMMARY_MODEL，
便宜的模型；build_chat_graph 只喂已有的 data/graph.json 当参考 + 聊天记录，不重新处理
全部咨询摘要，所以重跑成本只取决于聊天量，和真实咨询语料库大小无关）。

这是一个独立的常驻进程，不依赖浏览器标签页是否打开——用法：
    nohup python -m scripts.chat_memory_watcher [--workspace <workspace_id>] > /tmp/chat_memory_watcher.log 2>&1 &

用一个 marker 文件（data/chat_sessions/.last_memory_run）记录"上次处理到哪个时间点"，
避免在空闲期间反复重跑同一批已经处理过的对话。

支持 workspace：通过命令行参数指定 workspace。
"""
import json
import sys
import time
from datetime import datetime, timezone
from typing import Optional

from config import CHAT_GRAPH_JSON_PATH, CHAT_SESSIONS_DIR
from scripts.build_chat_graph import build_chat_graph
from scripts.chat_store import list_sessions
from scripts.update_chat_memory import update_chat_memory

IDLE_MINUTES = 30
CHECK_INTERVAL_SECONDS = 120  # 每 2 分钟检查一次，足够及时又不空转浪费


def _marker_path(workspace_id: Optional[str] = None):
    """获取 marker 文件路径（workspace 感知）。"""
    return CHAT_SESSIONS_DIR(workspace_id) / ".last_memory_run"


def _latest_session_update(workspace_id: Optional[str] = None) -> datetime | None:
    """获取最新会话更新时间（workspace 感知）。"""
    sessions = list_sessions(workspace_id)
    if not sessions:
        return None
    return datetime.fromisoformat(max(s["updated_at"] for s in sessions))


def _last_memory_run(workspace_id: Optional[str] = None) -> datetime | None:
    """获取上次运行时间（workspace 感知）。"""
    marker_path = _marker_path(workspace_id)
    if not marker_path.exists():
        return None
    try:
        return datetime.fromisoformat(marker_path.read_text().strip())
    except ValueError:
        return None


def _mark_memory_run(ts: datetime, workspace_id: Optional[str] = None) -> None:
    """标记运行时间（workspace 感知）。"""
    marker_path = _marker_path(workspace_id)
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    marker_path.write_text(ts.isoformat())


def check_and_update(workspace_id: Optional[str] = None) -> bool:
    """检查一次；如果确实触发了更新就返回 True，方便测试（workspace 感知）。"""
    latest_update = _latest_session_update(workspace_id)
    if latest_update is None:
        return False

    last_run = _last_memory_run(workspace_id)
    if last_run is not None and last_run >= latest_update:
        return False  # 这批新对话已经处理过了

    idle_seconds = (datetime.now(timezone.utc) - latest_update).total_seconds()
    if idle_seconds < IDLE_MINUTES * 60:
        return False  # 还没空闲够，可能还在聊

    print(f"[chat_memory_watcher] 距最近一次对话已 {idle_seconds / 60:.1f} 分钟，触发更新…", flush=True)
    update_chat_memory(workspace_id=workspace_id)
    print("[chat_memory_watcher] 已更新 CHAT_MEMORY.md", flush=True)

    chat_graph = build_chat_graph(workspace_id=workspace_id)
    chat_graph_path = CHAT_GRAPH_JSON_PATH(workspace_id)
    chat_graph_path.parent.mkdir(parents=True, exist_ok=True)
    chat_graph_path.write_text(json.dumps(chat_graph, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[chat_memory_watcher] 已更新 AI 对话记忆心智地图（{len(chat_graph['nodes'])} 个新节点）", flush=True)

    _mark_memory_run(datetime.now(timezone.utc), workspace_id)
    return True


if __name__ == "__main__":
    workspace_id = None
    if "--workspace" in sys.argv:
        idx = sys.argv.index("--workspace")
        if idx + 1 < len(sys.argv):
            workspace_id = sys.argv[idx + 1]

    print(
        f"[chat_memory_watcher] 启动，每 {CHECK_INTERVAL_SECONDS}s 检查一次，"
        f"空闲 {IDLE_MINUTES} 分钟后自动更新 AI 对话记忆（workspace: {workspace_id or 'default'}）",
        flush=True,
    )
    while True:
        try:
            check_and_update(workspace_id)
        except Exception as e:  # noqa: BLE001
            print(f"[chat_memory_watcher] 出错: {e}", flush=True)
        time.sleep(CHECK_INTERVAL_SECONDS)
