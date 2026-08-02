"""MCP RAG search 單元測試。

只測薄包裝邏輯（序列化 / 預設 workspace / 錯誤路徑 / 設定追蹤），一律 mock，
不載 BGE、不連真 LanceDB。
"""
from __future__ import annotations

import pytest


_FAKE_SETTINGS = {
    "source": "private.nosync/index_settings.json (Streamlit ⚙️ 索引設置)",
    "retrieval": {"top_k": 8, "window_expand": 1},
    "reranker": {
        "use_reranker": True,
        "rerank_top_k": 30,
        "final_top_k": 4,
        "min_score": 0.2,
        "min_keep": 2,
    },
    "notes": {"k_override": "...", "tracks_ui": True},
}


# ── serialize_window ────────────────────────────────────────────────────


class TestSerializeWindow:
    def test_happy_path_tuple_to_list(self):
        from scripts.mcp_rag_search import serialize_window

        raw = {
            "source_file": "a.txt",
            "session_date": "2026-07-04",
            "start_ts": "00:01:00",
            "end_ts": "00:05:00",
            "chunk_index_range": (3, 5),
            "text": "片段內容",
            "rank": 0,
            "score": 0.42,
            "below_threshold": False,
        }
        out = serialize_window(raw)
        assert out["chunk_index_range"] == [3, 5]
        assert out["session_date"] == "2026-07-04"
        assert out["score"] == 0.42
        assert out["below_threshold"] is False
        assert out["text"] == "片段內容"

    def test_missing_optional_fields(self):
        from scripts.mcp_rag_search import serialize_window

        out = serialize_window({"text": "only text"})
        assert out["text"] == "only text"
        assert out["score"] is None
        assert out["below_threshold"] is False
        assert out["chunk_index_range"] is None


# ── get_index_settings / tracks UI ──────────────────────────────────────


class TestGetIndexSettings:
    def test_returns_live_ui_settings(self, monkeypatch):
        from scripts import mcp_rag_search as m

        monkeypatch.setattr(m, "_load_query_time_settings", lambda: dict(_FAKE_SETTINGS))
        result = m.get_index_settings()
        assert result["ok"] is True
        assert result["settings"]["retrieval"]["top_k"] == 8
        assert result["settings"]["reranker"]["min_score"] == 0.2
        assert result["settings"]["notes"]["tracks_ui"] is True

    def test_load_failure_visible(self, monkeypatch):
        from scripts import mcp_rag_search as m

        def boom():
            raise RuntimeError("settings broken")

        monkeypatch.setattr(m, "_load_query_time_settings", boom)
        result = m.get_index_settings()
        assert result["ok"] is False
        assert "broken" in result["error"]


# ── search_sessions ─────────────────────────────────────────────────────


class TestSearchSessions:
    def test_happy_path_includes_settings_used(self, monkeypatch):
        from scripts import mcp_rag_search as m

        calls = []

        def fake_retrieve(query, k=None, workspace_id=None):
            calls.append((query, k, workspace_id))
            return [
                {
                    "source_file": "s.txt",
                    "session_date": "2026-01-01",
                    "start_ts": "0",
                    "end_ts": "1",
                    "chunk_index_range": (0, 1),
                    "text": "命中",
                    "rank": 0,
                    "score": 0.9,
                    "below_threshold": False,
                }
            ]

        monkeypatch.setattr(m, "_retrieve", fake_retrieve)
        monkeypatch.setattr(m, "_load_query_time_settings", lambda: dict(_FAKE_SETTINGS))
        result = m.search_sessions("買遊戲電腦")
        assert result["ok"] is True
        assert result["workspace_id"] == "counseling"
        assert result["count"] == 1
        assert result["results"][0]["text"] == "命中"
        assert result["k_override"] is None
        assert result["settings_used"]["reranker"]["final_top_k"] == 4
        assert result["settings_used"]["notes"]["tracks_ui"] is True
        # 預設 k=None → 完全交給 UI / retrieve
        assert calls == [("買遊戲電腦", None, "counseling")]

    def test_explicit_k_and_workspace(self, monkeypatch):
        from scripts import mcp_rag_search as m

        captured = {}

        def fake_retrieve(query, k=None, workspace_id=None):
            captured["query"] = query
            captured["k"] = k
            captured["workspace_id"] = workspace_id
            return []

        monkeypatch.setattr(m, "_retrieve", fake_retrieve)
        monkeypatch.setattr(m, "_load_query_time_settings", lambda: dict(_FAKE_SETTINGS))
        result = m.search_sessions("q", k=3, workspace_id="bazhai-ziwu")
        assert result["ok"] is True
        assert result["count"] == 0
        assert result["k_override"] == 3
        assert "settings_used" in result
        assert captured == {"query": "q", "k": 3, "workspace_id": "bazhai-ziwu"}

    def test_empty_query_rejected_still_attaches_settings(self, monkeypatch):
        from scripts import mcp_rag_search as m

        monkeypatch.setattr(m, "_load_query_time_settings", lambda: dict(_FAKE_SETTINGS))
        result = m.search_sessions("   ")
        assert result["ok"] is False
        assert "query" in result["error"].lower() or "空" in result["error"]
        assert result["settings_used"]["retrieval"]["top_k"] == 8

    def test_retrieve_value_error_visible(self, monkeypatch):
        from scripts import mcp_rag_search as m

        def boom(query, k=None, workspace_id=None):
            raise ValueError("Table 'sessions' was not found")

        monkeypatch.setattr(m, "_retrieve", boom)
        monkeypatch.setattr(m, "_load_query_time_settings", lambda: dict(_FAKE_SETTINGS))
        result = m.search_sessions("anything")
        assert result["ok"] is False
        assert "sessions" in result["error"]
        assert result["workspace_id"] == "counseling"
        assert result["settings_used"]["notes"]["tracks_ui"] is True


# ── list_workspaces_info ────────────────────────────────────────────────


class TestListWorkspacesInfo:
    def test_returns_names(self, monkeypatch):
        from scripts import mcp_rag_search as m

        monkeypatch.setattr(
            m,
            "_list_workspaces",
            lambda: [
                {"name": "counseling", "display_name": "心理咨询", "created_at": "2026-01-01"},
                {"name": "bazhai-ziwu", "display_name": "八宅", "created_at": None},
            ],
        )
        result = m.list_workspaces_info()
        assert result["ok"] is True
        assert result["count"] == 2
        assert result["workspaces"][0]["name"] == "counseling"
        assert result["workspaces"][0]["display_name"] == "心理咨询"


# ── create_mcp / import smoke ───────────────────────────────────────────


class TestMcpServerFactory:
    def test_create_mcp_requires_fastmcp_or_works(self):
        """有 fastmcp 時 create_mcp 成功；沒有時回清楚 ImportError。"""
        from scripts import mcp_rag_search as m

        try:
            import fastmcp  # noqa: F401
        except ImportError:
            with pytest.raises(ImportError) as ei:
                m.create_mcp()
            assert "fastmcp" in str(ei.value).lower()
            return

        mcp = m.create_mcp()
        assert mcp is not None
