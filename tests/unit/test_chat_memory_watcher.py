"""测试对话记忆看门狗（scripts/chat_memory_watcher.py）。

check_and_update() 的三道闸门（任何一道不过就不触发昂贵的 LLM 调用）：
  1. 有没有会话；
  2. 这批新对话是不是已经处理过了（marker >= 最新会话时间）；
  3. 空闲时间够不够 IDLE_MINUTES（还在聊就别打断）。

测试全部 mock update_chat_memory / build_chat_graph，只验证「什么时候该跑、什么时候不该跑」。
"""
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from scripts import chat_memory_watcher as w
from scripts.chat_memory_watcher import (
    CHECK_INTERVAL_SECONDS,
    IDLE_MINUTES,
    check_and_update,
)


# ── Fixtures ──────────────────────────────────────────────────────────────


def _iso(minutes_ago):
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()


@pytest.fixture
def env(tmp_path, monkeypatch):
    """隔离路径 + mock 两个昂贵的下游动作。"""
    sessions_dir = tmp_path / "chat_sessions"
    graph_path = tmp_path / "chat_graph.json"

    mock_update = MagicMock()
    mock_build = MagicMock(return_value={"nodes": [{"id": "n1"}, {"id": "n2"}], "edges": []})
    mock_list = MagicMock(return_value=[])

    monkeypatch.setattr(w, "CHAT_SESSIONS_DIR", lambda ws=None: sessions_dir)
    monkeypatch.setattr(w, "CHAT_GRAPH_JSON_PATH", lambda ws=None: graph_path)
    monkeypatch.setattr(w, "update_chat_memory", mock_update)
    monkeypatch.setattr(w, "build_chat_graph", mock_build)
    monkeypatch.setattr(w, "list_sessions", mock_list)

    return {
        "sessions_dir": sessions_dir,
        "graph_path": graph_path,
        "update": mock_update,
        "build": mock_build,
        "list": mock_list,
    }


# ── marker 文件 ────────────────────────────────────────────────────────────


class TestMarker:
    """.last_memory_run（记录「上次处理到哪个时间点」）。"""

    def test_marker_path_under_sessions_dir(self, env):
        assert w._marker_path() == env["sessions_dir"] / ".last_memory_run"

    def test_last_run_none_when_missing(self, env):
        assert w._last_memory_run() is None

    def test_last_run_none_when_unparsable(self, env):
        """marker 被写坏 → 当作没跑过（宁可多跑一次，也不要崩）。"""
        env["sessions_dir"].mkdir(parents=True)
        (env["sessions_dir"] / ".last_memory_run").write_text("not-a-timestamp")

        assert w._last_memory_run() is None

    def test_mark_then_read_roundtrip(self, env):
        ts = datetime(2026, 7, 25, 12, 0, 0, tzinfo=timezone.utc)

        w._mark_memory_run(ts)

        assert w._last_memory_run() == ts

    def test_mark_creates_parent_dir(self, env):
        assert not env["sessions_dir"].exists()

        w._mark_memory_run(datetime.now(timezone.utc))

        assert (env["sessions_dir"] / ".last_memory_run").exists()


# ── _latest_session_update ────────────────────────────────────────────────


class TestLatestSessionUpdate:
    def test_none_when_no_sessions(self, env):
        assert w._latest_session_update() is None

    def test_takes_max_updated_at(self, env):
        env["list"].return_value = [
            {"updated_at": "2026-07-01T10:00:00+00:00"},
            {"updated_at": "2026-07-20T10:00:00+00:00"},
        ]

        assert w._latest_session_update() == datetime(2026, 7, 20, 10, 0, tzinfo=timezone.utc)


# ── check_and_update ──────────────────────────────────────────────────────


class TestCheckAndUpdate:
    """三道闸门 + 触发后的副作用。"""

    def test_no_sessions_does_nothing(self, env):
        assert check_and_update() is False
        env["update"].assert_not_called()
        env["build"].assert_not_called()

    def test_still_chatting_does_nothing(self, env):
        """刚聊完（空闲不足 IDLE_MINUTES）→ 不打断。"""
        env["list"].return_value = [{"updated_at": _iso(minutes_ago=1)}]

        assert check_and_update() is False
        env["update"].assert_not_called()

    def test_already_processed_does_nothing(self, env):
        """marker 比最新会话新 → 这批已经汇总过了，别重复烧 token。"""
        env["list"].return_value = [{"updated_at": _iso(minutes_ago=IDLE_MINUTES + 10)}]
        w._mark_memory_run(datetime.now(timezone.utc))

        assert check_and_update() is False
        env["update"].assert_not_called()

    def test_idle_long_enough_triggers_update(self, env):
        env["list"].return_value = [{"updated_at": _iso(minutes_ago=IDLE_MINUTES + 1)}]

        assert check_and_update() is True
        env["update"].assert_called_once_with(workspace_id=None)
        env["build"].assert_called_once_with(workspace_id=None)

    def test_writes_chat_graph_json(self, env):
        env["list"].return_value = [{"updated_at": _iso(minutes_ago=IDLE_MINUTES + 1)}]

        check_and_update()

        graph = json.loads(env["graph_path"].read_text(encoding="utf-8"))
        assert len(graph["nodes"]) == 2

    def test_marks_run_so_next_check_is_a_noop(self, env):
        """跑完写 marker → 同一批对话不会被反复处理。"""
        env["list"].return_value = [{"updated_at": _iso(minutes_ago=IDLE_MINUTES + 1)}]

        assert check_and_update() is True
        assert check_and_update() is False
        env["update"].assert_called_once()

    def test_passes_workspace_id_through(self, env):
        env["list"].return_value = [{"updated_at": _iso(minutes_ago=IDLE_MINUTES + 1)}]

        assert check_and_update("counseling") is True

        env["list"].assert_called_with("counseling")
        env["update"].assert_called_once_with(workspace_id="counseling")
        env["build"].assert_called_once_with(workspace_id="counseling")


class TestConstants:
    def test_interval_smaller_than_idle_window(self):
        """扫描间隔必须远小于空闲阈值，否则会漏过触发窗口。"""
        assert CHECK_INTERVAL_SECONDS < IDLE_MINUTES * 60
