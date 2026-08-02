"""Hermes / 外部 Agent 用的 MCP 檢索 server。

薄包裝：把既有 `scripts.ask.retrieve()` 外露成 MCP tools，不重寫向量檢索。
預設 workspace = counseling。只做索引搜尋，不呼叫 `answer()` / LLM。

啟動（stdio，給 Hermes 用）：

    uv run --group mcp python -m scripts.mcp_rag_search

見 docs/HERMES_MCP.md。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Optional

# 不依賴 Hermes / 呼叫端的 cwd：固定把 repo root 放進 sys.path。
# 注意：不在 import 時 os.chdir（會污染 pytest 工作目錄）。
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

DEFAULT_WORKSPACE = "counseling"


# ── 可注入的依賴點（測試 monkeypatch 這兩個名字，不碰真 LanceDB / 真 workspace 目錄）──


def _retrieve(query: str, k: Optional[int] = None, workspace_id: Optional[str] = None):
    """延遲 import ask.retrieve，避免 import 本模組時載入 embedding 模型。"""
    from scripts.ask import retrieve

    return retrieve(query, k=k, workspace_id=workspace_id)


def _list_workspaces():
    from scripts.workspace_manager import list_workspaces

    return list_workspaces()


# ── 純函式（單元測試主目標）──────────────────────────────────────────────


def serialize_window(window: dict[str, Any]) -> dict[str, Any]:
    """把 retrieve() 回傳的窗口轉成 JSON-safe dict。"""
    cir = window.get("chunk_index_range")
    if isinstance(cir, tuple):
        cir = list(cir)
    return {
        "source_file": window.get("source_file"),
        "session_date": window.get("session_date"),
        "start_ts": window.get("start_ts"),
        "end_ts": window.get("end_ts"),
        "chunk_index_range": cir,
        "text": window.get("text"),
        "rank": window.get("rank"),
        "score": window.get("score"),
        "below_threshold": bool(window.get("below_threshold", False)),
    }


def search_sessions(
    query: str,
    k: Optional[int] = None,
    workspace_id: str = DEFAULT_WORKSPACE,
) -> dict[str, Any]:
    """混合檢索 counseling（或其他）workspace 的諮詢片段。

    Returns:
        {"ok": True, "query", "workspace_id", "count", "results": [...]}
        或 {"ok": False, "error": str, "workspace_id": str}
    """
    q = (query or "").strip()
    if not q:
        return {
            "ok": False,
            "error": "query 不能為空",
            "workspace_id": workspace_id,
        }
    try:
        windows = _retrieve(q, k=k, workspace_id=workspace_id)
    except Exception as e:  # 空庫 ValueError 等：失敗可見，不靜默
        return {
            "ok": False,
            "error": str(e),
            "workspace_id": workspace_id,
        }
    results = [serialize_window(w) for w in windows]
    return {
        "ok": True,
        "query": q,
        "workspace_id": workspace_id,
        "count": len(results),
        "results": results,
    }


def list_workspaces_info() -> dict[str, Any]:
    """列出本機可用的 workspace（給 agent 選 workspace_id）。"""
    try:
        items = _list_workspaces()
    except Exception as e:
        return {"ok": False, "error": str(e)}
    workspaces = [
        {
            "name": w.get("name"),
            "display_name": w.get("display_name"),
            "created_at": w.get("created_at"),
        }
        for w in items
    ]
    return {"ok": True, "count": len(workspaces), "workspaces": workspaces}


# ── FastMCP server ──────────────────────────────────────────────────────


def create_mcp():
    """建立 FastMCP 實例並註冊 tools。缺 fastmcp 時丟清楚的 ImportError。"""
    try:
        from fastmcp import FastMCP
    except ImportError as e:
        raise ImportError(
            "缺少 fastmcp。請用 uv 安裝 MCP 依賴組："
            "  uv sync --group mcp"
            "  或  uv add --group mcp fastmcp"
            "詳見 docs/HERMES_MCP.md。"
        ) from e

    mcp = FastMCP(
        name="rag-agent-search",
        instructions=(
            "Search the local RAG-Agent session vector DB (default workspace: counseling). "
            "Use search_sessions for hybrid retrieval over therapy transcripts. "
            "Results are private personal counseling notes — quote dates/sources; do not invent."
        ),
    )

    @mcp.tool(name="search_sessions")
    def _search_sessions(
        query: str,
        k: Optional[int] = None,
        workspace_id: str = DEFAULT_WORKSPACE,
    ) -> dict[str, Any]:
        """Hybrid-search counseling (or other) session transcripts in the local LanceDB index.

        Use when the user asks about past counseling sessions, themes, dates, or quotes from
        therapy notes. Default workspace_id is "counseling". Returns ranked text windows with
        session_date / source_file / score. Does NOT call the LLM — search only.
        """
        return search_sessions(query=query, k=k, workspace_id=workspace_id)

    @mcp.tool(name="list_workspaces")
    def _list_workspaces_tool() -> dict[str, Any]:
        """List available RAG-Agent workspace ids (name + display_name)."""
        return list_workspaces_info()

    return mcp


# 供 `fastmcp list scripts/mcp_rag_search.py` / inspect 發現；無 fastmcp 時為 None。
try:
    mcp = create_mcp()
except ImportError:
    mcp = None


def main() -> None:
    """stdio MCP 入口。固定 cwd 到 repo root 後 run。"""
    os.chdir(_ROOT)
    if str(_ROOT) not in sys.path:
        sys.path.insert(0, str(_ROOT))
    server = mcp if mcp is not None else create_mcp()
    server.run()


if __name__ == "__main__":
    main()
