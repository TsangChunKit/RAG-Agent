"""测试 AI 对话记忆生成（scripts/update_chat_memory.py）。

刻意与 LONG_TERM_MEMORY 分开：素材是「和 AI 聊天」的记录，不是与真人咨询师的咨询。
测试要守住这条边界（标题文案里明确写了「非真实咨询记录」）。

重点测试：
1. load_chat_sessions() - 加载、排序、坏 JSON 跳过
2. _format_sessions() - user/assistant 前缀、AI 回复截断 300 字
3. update_chat_memory() - 空会话的占位内容 / 正常内容 + 头部计数
"""
import datetime
import json
from unittest.mock import MagicMock

import pytest

from scripts import update_chat_memory as ucm
from scripts.update_chat_memory import (
    SYSTEM_INSTRUCTION,
    generate_chat_memory_body,
    load_chat_sessions,
    update_chat_memory,
)


# ── Fixtures ──────────────────────────────────────────────────────────────


def _session(session_id, updated_at, title="聊聊工作", messages=None):
    return {
        "id": session_id,
        "title": title,
        "updated_at": updated_at,
        "messages": messages if messages is not None else [
            {"role": "user", "content": "我最近很焦虑"},
            {"role": "assistant", "content": "听起来压力不小"},
        ],
    }


@pytest.fixture
def paths(tmp_path, monkeypatch):
    """会话目录和 CHAT_MEMORY.md 都指到 tmp_path（默认不建目录，便于测缺失分支）。"""
    sessions_dir = tmp_path / "chat_sessions"
    memory_path = tmp_path / "CHAT_MEMORY.md"

    monkeypatch.setattr(ucm, "CHAT_SESSIONS_DIR", lambda ws=None: sessions_dir)
    monkeypatch.setattr(ucm, "CHAT_MEMORY_PATH", lambda ws=None: memory_path)

    return {"sessions": sessions_dir, "memory": memory_path}


@pytest.fixture
def mock_llm(monkeypatch):
    resp = MagicMock()
    resp.text = "\n## 反复讨论的议题\n- 工作焦虑\n"
    mock = MagicMock(return_value=resp)

    monkeypatch.setattr(ucm, "ask_llm", mock)
    monkeypatch.setattr(ucm, "summary_max_tokens", lambda kind: 8000)

    return mock


def _write_session(sessions_dir, name, data):
    sessions_dir.mkdir(parents=True, exist_ok=True)
    (sessions_dir / name).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


# ── load_chat_sessions ────────────────────────────────────────────────────


class TestLoadChatSessions:
    """会话加载。"""

    def test_missing_dir_returns_empty(self, paths):
        assert load_chat_sessions() == []

    def test_sorted_by_updated_at(self, paths):
        _write_session(paths["sessions"], "b.json", _session("b", "2026-07-01T10:00:00"))
        _write_session(paths["sessions"], "a.json", _session("a", "2026-06-01T10:00:00"))

        sessions = load_chat_sessions()

        assert [s["id"] for s in sessions] == ["a", "b"]

    def test_skips_corrupted_json(self, paths):
        """一份坏文件不能让整份记忆更新失败。"""
        _write_session(paths["sessions"], "ok.json", _session("ok", "2026-07-01T10:00:00"))
        (paths["sessions"] / "bad.json").write_text("{截断的 json", encoding="utf-8")

        sessions = load_chat_sessions()

        assert len(sessions) == 1
        assert sessions[0]["id"] == "ok"

    def test_missing_updated_at_sorts_first(self, paths):
        """老数据没有 updated_at → 用空串兜底，不抛 KeyError。"""
        _write_session(paths["sessions"], "a.json", {"id": "no_ts", "messages": []})
        _write_session(paths["sessions"], "b.json", _session("b", "2026-07-01T10:00:00"))

        sessions = load_chat_sessions()

        assert [s["id"] for s in sessions] == ["no_ts", "b"]


# ── _format_sessions ──────────────────────────────────────────────────────


class TestFormatSessions:
    """喂给 LLM 的聊天文本。"""

    def test_role_prefixes(self):
        text = ucm._format_sessions([_session("a", "2026-07-01T10:00:00")])

        assert "使用者问：我最近很焦虑" in text
        assert "AI答（节选）：听起来压力不小" in text

    def test_header_has_date_and_title(self):
        text = ucm._format_sessions([_session("a", "2026-07-01T10:00:00", title="聊聊消费")])

        assert "[对话 2026-07-01｜聊聊消费]" in text

    def test_assistant_reply_truncated_to_300_chars(self):
        """AI 回复通常很长，只取节选，避免把 token 花在自己说过的话上。"""
        long_reply = "啊" * 500
        text = ucm._format_sessions([
            _session("a", "2026-07-01T10:00:00", messages=[{"role": "assistant", "content": long_reply}])
        ])

        assert text.count("啊") == 300

    def test_newlines_flattened_in_reply(self):
        text = ucm._format_sessions([
            _session("a", "2026-07-01T10:00:00", messages=[{"role": "assistant", "content": "第一段\n第二段"}])
        ])

        assert "第一段 第二段" in text

    def test_session_without_messages(self):
        text = ucm._format_sessions([_session("a", "2026-07-01T10:00:00", messages=[])])

        assert "[对话 2026-07-01｜聊聊工作]" in text


# ── generate_chat_memory_body ──────────────────────────────────────────────


class TestGenerateChatMemoryBody:
    def test_uses_summary_profile(self, mock_llm):
        """便宜的摘要模型就够（和 update_memory 的长文任务不同）。"""
        body = generate_chat_memory_body([_session("a", "2026-07-01T10:00:00")])

        _, kwargs = mock_llm.call_args
        assert kwargs["profile"] == "summary"
        assert kwargs["system_instruction"] == SYSTEM_INSTRUCTION
        assert kwargs["max_output_tokens"] == 8000
        assert "model" not in kwargs  # 不覆盖 profile 的默认模型
        assert body.startswith("## 反复讨论的议题")


# ── update_chat_memory ────────────────────────────────────────────────────


class TestUpdateChatMemory:
    """整份 CHAT_MEMORY.md。"""

    def test_no_sessions_writes_placeholder_without_calling_llm(self, paths, mock_llm):
        content = update_chat_memory([])

        assert "（还没有聊天记录。）" in content
        assert "已纳入对话：0 次" in content
        assert paths["memory"].read_text(encoding="utf-8") == content
        mock_llm.assert_not_called()

    def test_header_marks_not_real_counseling(self, paths, mock_llm):
        """标题必须写明非真实咨询记录——这是两份记忆不互相污染的关键。"""
        content = update_chat_memory([_session("a", "2026-07-01T10:00:00")])

        assert "非真实咨询记录" in content
        assert f"更新时间：{datetime.date.today().isoformat()}" in content
        assert "已纳入对话：1 次" in content

    def test_body_appended(self, paths, mock_llm):
        content = update_chat_memory([
            _session("a", "2026-07-01T10:00:00"),
            _session("b", "2026-07-02T10:00:00"),
        ])

        assert "## 反复讨论的议题" in content
        assert "已纳入对话：2 次" in content
        assert content.endswith("\n")

    def test_loads_from_disk_when_not_given(self, paths, mock_llm):
        _write_session(paths["sessions"], "a.json", _session("a", "2026-07-01T10:00:00"))

        content = update_chat_memory()

        assert "已纳入对话：1 次" in content

    def test_creates_parent_dir(self, tmp_path, monkeypatch, mock_llm):
        """workspace 刚建好、目录还不存在时也要能写。"""
        memory_path = tmp_path / "nested" / "deep" / "CHAT_MEMORY.md"
        monkeypatch.setattr(ucm, "CHAT_MEMORY_PATH", lambda ws=None: memory_path)
        monkeypatch.setattr(ucm, "CHAT_SESSIONS_DIR", lambda ws=None: tmp_path / "nope")

        update_chat_memory()

        assert memory_path.exists()
