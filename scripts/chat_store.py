"""聊天会话的本地持久化：每个会话一个 JSON 文件，供 app.py 的多会话历史功能使用。
纯本地文件，不涉及网络——不进 git（见 .gitignore）。

支持 workspace 隔离：每个 workspace 有独立的 chat_sessions/ 目录。
"""
import json
import uuid
from datetime import datetime, timezone
from typing import Optional

from config import CHAT_SESSIONS_DIR


def new_session_id() -> str:
    return uuid.uuid4().hex[:12]


def _session_path(session_id: str, workspace_id: Optional[str] = None):
    """获取 session 文件路径（workspace 感知）。"""
    return CHAT_SESSIONS_DIR(workspace_id) / f"{session_id}.json"


def list_sessions(workspace_id: Optional[str] = None) -> list[dict]:
    """返回所有会话的元信息（workspace 感知）。

    返回 id/title/updated_at，按更新时间倒序，不含 messages 正文（省内存）。
    """
    sessions = []
    sessions_dir = CHAT_SESSIONS_DIR(workspace_id)
    if not sessions_dir.exists():
        return sessions
    for f in sessions_dir.glob("*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            sessions.append(
                {
                    "id": data["id"],
                    "title": data.get("title") or "新对话",
                    "updated_at": data.get("updated_at", ""),
                }
            )
        except (json.JSONDecodeError, KeyError):
            continue
    sessions.sort(key=lambda s: s["updated_at"], reverse=True)
    return sessions


def load_session(session_id: str, workspace_id: Optional[str] = None) -> dict:
    """加载会话（workspace 感知）。"""
    path = _session_path(session_id, workspace_id)
    if not path.exists():
        return {"id": session_id, "title": "新对话", "messages": []}
    return json.loads(path.read_text(encoding="utf-8"))


def save_session(session_id: str, title: str, messages: list[dict], created_at: str | None = None, workspace_id: Optional[str] = None) -> None:
    """保存会话（workspace 感知）。"""
    sessions_dir = CHAT_SESSIONS_DIR(workspace_id)
    sessions_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat()
    data = {
        "id": session_id,
        "title": title,
        "created_at": created_at or now,
        "updated_at": now,
        "messages": messages,
    }
    _session_path(session_id, workspace_id).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def delete_session(session_id: str, workspace_id: Optional[str] = None) -> None:
    """删除会话（workspace 感知）。"""
    path = _session_path(session_id, workspace_id)
    if path.exists():
        path.unlink()


def make_title(first_message: str) -> str:
    text = first_message.strip().replace("\n", " ")
    return text[:24] + ("…" if len(text) > 24 else "")
