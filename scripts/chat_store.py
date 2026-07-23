"""聊天会话的本地持久化：每个会话一个 JSON 文件，供 app.py 的多会话历史功能使用。
纯本地文件，不涉及网络——和其他心理咨询衍生数据一样，不进 git（见 .gitignore）。
"""
import json
import uuid
from datetime import datetime, timezone

from config import CHAT_SESSIONS_DIR


def new_session_id() -> str:
    return uuid.uuid4().hex[:12]


def _session_path(session_id: str):
    return CHAT_SESSIONS_DIR / f"{session_id}.json"


def list_sessions() -> list[dict]:
    """返回所有会话的元信息（id/title/updated_at），按更新时间倒序，不含 messages 正文（省内存）。"""
    sessions = []
    if not CHAT_SESSIONS_DIR.exists():
        return sessions
    for f in CHAT_SESSIONS_DIR.glob("*.json"):
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


def load_session(session_id: str) -> dict:
    path = _session_path(session_id)
    if not path.exists():
        return {"id": session_id, "title": "新对话", "messages": []}
    return json.loads(path.read_text(encoding="utf-8"))


def save_session(session_id: str, title: str, messages: list[dict], created_at: str | None = None) -> None:
    CHAT_SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat()
    data = {
        "id": session_id,
        "title": title,
        "created_at": created_at or now,
        "updated_at": now,
        "messages": messages,
    }
    _session_path(session_id).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def delete_session(session_id: str) -> None:
    path = _session_path(session_id)
    if path.exists():
        path.unlink()


def make_title(first_message: str) -> str:
    text = first_message.strip().replace("\n", " ")
    return text[:24] + ("…" if len(text) > 24 else "")
