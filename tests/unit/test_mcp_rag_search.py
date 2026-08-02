"""MCP RAG search 單元測試。

只測薄包裝邏輯（序列化 / 預設 workspace / 錯誤路徑），一律 mock `ask.retrieve`，
不載 BGE、不連真 LanceDB。
"""
from __future__ import annotations

import pytest


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


# ── search_sessions ─────────────────────────────────────────────────────


class TestSearchSessions:
    def test_happy_path_default_workspace(self, monkeypatch):
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
        result = m.search_sessions("買遊戲電腦")
        assert result["ok"] is True
        assert result["workspace_id"] == "counseling"
        assert result["count"] == 1
        assert result["results"][0]["text"] == "命中"
        assert result["results"][0]["chunk_index_range"] == [0, 1]
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
        result = m.search_sessions("q", k=3, workspace_id="bazhai-ziwu")
        assert result["ok"] is True
        assert result["count"] == 0
        assert captured == {"query": "q", "k": 3, "workspace_id": "bazhai-ziwu"}

    def test_empty_query_rejected(self):
        from scripts.mcp_rag_search import search_sessions

        result = search_sessions("   ")
        assert result["ok"] is False
        assert "query" in result["error"].lower() or "空" in result["error"]

    def test_retrieve_value_error_visible(self, monkeypatch):
        from scripts import mcp_rag_search as m

        def boom(query, k=None, workspace_id=None):
            raise ValueError("Table 'sessions' was not found")

        monkeypatch.setattr(m, "_retrieve", boom)
        result = m.search_sessions("anything")
        assert result["ok"] is False
        assert "sessions" in result["error"]
        assert result["workspace_id"] == "counseling"


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
