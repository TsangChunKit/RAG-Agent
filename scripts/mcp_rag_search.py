"""Hermes / 外部 Agent 用的 MCP 檢索 server。

薄包裝：把既有 `scripts.ask.retrieve()` 外露成 MCP tools，不重寫向量檢索。
預設 workspace = counseling。只做索引搜尋，不呼叫 `answer()` / LLM。

**索引參數與 UI 共用同一真相源**：`private.nosync/index_settings.json`
（Streamlit「⚙️ 索引設置」）。每次 search 都重新讀檔，無 MCP 獨立配置、
不寫回設定檔。查詢期參數（top_k / final_top_k / min_score 等）改 UI 後
下一次 MCP 搜尋即生效。

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


# ── 可注入的依賴點（測試 monkeypatch，不碰真 LanceDB / 真設定檔）──────────


def _retrieve(query: str, k: Optional[int] = None, workspace_id: Optional[str] = None):
    """延遲 import ask.retrieve，避免 import 本模組時載入 embedding 模型。"""
    from scripts.ask import retrieve

    return retrieve(query, k=k, workspace_id=workspace_id)


def _list_workspaces():
    from scripts.workspace_manager import list_workspaces

    return list_workspaces()


def _load_query_time_settings() -> dict[str, Any]:
    """從 index_settings 讀「當前與 UI 一致」的查詢期參數（每次呼叫重新讀檔）。"""
    from scripts import index_settings

    retrieval = index_settings.retrieval_params()
    reranker = index_settings.reranker_params()
    # 只暴露查詢期、會影響 MCP 搜尋結果的字段（不含 model/device 等需重啟的）
    return {
        "source": "private.nosync/index_settings.json (Streamlit ⚙️ 索引設置)",
        "retrieval": {
            "top_k": retrieval["top_k"],
            "window_expand": retrieval["window_expand"],
        },
        "reranker": {
            "use_reranker": reranker["use_reranker"],
            "rerank_top_k": reranker["rerank_top_k"],
            "final_top_k": reranker["final_top_k"],
            "min_score": reranker["min_score"],
            "min_keep": reranker["min_keep"],
        },
        "notes": {
            "k_override": (
                "search_sessions 的 k 僅在 use_reranker=false 時作為 hybrid limit；"
                "開了 reranker 時最終條數由 final_top_k + min_score/min_keep 決定（與 UI 相同）。"
            ),
            "tracks_ui": True,
        },
    }


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


def get_index_settings() -> dict[str, Any]:
    """唯讀：回傳與 Streamlit「⚙️ 索引設置」當前一致的查詢期參數。

    不寫檔、不改設定。MCP 沒有獨立索引配置。
    """
    try:
        settings = _load_query_time_settings()
    except Exception as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "settings": settings}


def search_sessions(
    query: str,
    k: Optional[int] = None,
    workspace_id: str = DEFAULT_WORKSPACE,
) -> dict[str, Any]:
    """混合檢索 counseling（或其他）workspace 的諮詢片段。

    索引參數一律跟 UI（index_settings.json）；本函式不維護獨立配置。
    k=None（建議預設）→ 完全使用 UI 的 top_k / final_top_k / min_score。
    k 有值 → 僅在關 reranker 時覆寫 hybrid limit；開 reranker 時仍以 UI 為準。

    Returns:
        成功: ok, query, workspace_id, count, results, settings_used
        失敗: ok=False, error, workspace_id, settings_used?（盡量附上）
    """
    q = (query or "").strip()
    settings_used: dict[str, Any] | None
    try:
        settings_used = _load_query_time_settings()
    except Exception:
        settings_used = None

    if not q:
        out: dict[str, Any] = {
            "ok": False,
            "error": "query 不能為空",
            "workspace_id": workspace_id,
        }
        if settings_used is not None:
            out["settings_used"] = settings_used
        return out
    try:
        # k=None → retrieve 內部讀 UI top_k / final_top_k 等
        windows = _retrieve(q, k=k, workspace_id=workspace_id)
    except Exception as e:  # 空庫 ValueError 等：失敗可見，不靜默
        out = {
            "ok": False,
            "error": str(e),
            "workspace_id": workspace_id,
        }
        if settings_used is not None:
            out["settings_used"] = settings_used
        return out
    results = [serialize_window(w) for w in windows]
    out = {
        "ok": True,
        "query": q,
        "workspace_id": workspace_id,
        "count": len(results),
        "results": results,
        "k_override": k,
    }
    if settings_used is not None:
        out["settings_used"] = settings_used
    return out


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
            "Retrieval knobs (top_k, final_top_k, min_score, etc.) always follow Streamlit "
            "「⚙️ 索引設置」/ private.nosync/index_settings.json — there is no separate MCP config. "
            "Use get_index_settings to inspect current values; search_sessions returns settings_used. "
            "Results are private counseling notes — quote dates/sources; do not invent."
        ),
    )

    @mcp.tool(name="search_sessions")
    def _search_sessions(
        query: str,
        k: Optional[int] = None,
        workspace_id: str = DEFAULT_WORKSPACE,
    ) -> dict[str, Any]:
        """Hybrid-search counseling session transcripts (local LanceDB).

        Index/retrieval settings always track the Streamlit UI (index_settings.json):
        top_k, window_expand, use_reranker, rerank_top_k, final_top_k, min_score, min_keep.
        Leave k=null to fully follow UI. If use_reranker is on, final count is final_top_k
        then min_score filter (k is ignored for count). Returns settings_used for visibility.
        Does NOT call the LLM — search only.
        """
        return search_sessions(query=query, k=k, workspace_id=workspace_id)

    @mcp.tool(name="get_index_settings")
    def _get_index_settings() -> dict[str, Any]:
        """Read-only: current query-time index settings from Streamlit UI (shared with MCP).

        Same file as ⚙️ 索引設置. Use before/after search to verify top_k / min_score / etc.
        Does not write or change settings.
        """
        return get_index_settings()

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
