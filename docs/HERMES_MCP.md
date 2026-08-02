# Hermes Agent × counseling 向量庫（MCP 檢索）

讓 **Nous Hermes Agent**（`~/.hermes`、`hermes` CLI）透過 **MCP** 對本專案的
LanceDB 做**混合索引搜尋**。檢索邏輯與 Streamlit 共用 `scripts.ask.retrieve()`，
不另建索引。

> 這和 RAG-Agent UI 裡的 **LLM provider = hermes**（本地 OpenAI 相容代理
> `http://127.0.0.1:8645/v1`）是兩件事。本文件只講 **Hermes Agent 的 MCP 工具**。

---

## 架構

```text
Hermes Agent
  │  MCP stdio（本機，敏感資料不走 HTTP）
  ▼
scripts/mcp_rag_search.py   # FastMCP server
  │  search_sessions / get_index_settings / list_workspaces
  ▼
scripts.ask.retrieve(query, workspace_id="counseling")
  │
  ▼
private.nosync/workspaces/counseling/db/sessions.lance
```

| Tool | 作用 |
|------|------|
| `search_sessions` | hybrid 檢索（dense + FTS + RRF → rerank → 父塊窗口）。預設 `workspace_id=counseling`。回傳含 `settings_used` |
| `get_index_settings` | **唯讀**當前查詢期索引參數（與 Streamlit「⚙️ 索引設置」同一檔） |
| `list_workspaces` | 列出本機可用 workspace id |

### 索引參數一直追蹤 UI（單一真相源）

MCP **沒有**獨立的索引配置。查詢期參數與 Streamlit 共用：

```text
private.nosync/index_settings.json
        ↑ 讀
Streamlit「⚙️ 索引設置」  ──寫──→  同一檔  ←──讀──  MCP / ask.retrieve()
```

| 參數 | 誰改 | MCP 是否跟隨 |
|------|------|--------------|
| `top_k` / `window_expand` | UI | ✅ 每次 search 重新讀檔 |
| `use_reranker` / `rerank_top_k` / `final_top_k` | UI | ✅ |
| `min_score` / `min_keep` | UI | ✅ |
| `search_sessions(k=…)` | 可選覆寫 | ⚠️ 僅在 **關** reranker 時當 hybrid limit；**開** reranker 時最終條數仍由 UI 的 `final_top_k` + `min_score` 決定 |

建議：MCP 呼叫時 **不要傳 k**（保持 null），完全跟 UI。  
改 UI 存檔後，**下一次** MCP 搜尋即生效（無需重啟 Hermes；若 MCP 子進程已載入 BGE 模型仍可繼續用，只是設定從檔重讀）。

v1 **不做**：`answer()`、GraphRAG 完整問答、寫庫、遠端 HTTP MCP、在 MCP 裡改寫 index_settings。

---

## 用 uv 管理 Python 環境

本專案用 **uv** 管依賴與虛擬環境（見 `pyproject.toml` / `uv.lock`）。

MCP server 依賴 **fastmcp**，放在獨立 dependency group `mcp`，**主應用
（Streamlit / ingest / ask）不需要**。

```bash
cd /path/to/RAG-Agent

# 日常開發（測試 + 主依賴）
uv sync --group dev

# 接 Hermes MCP 時再加上 mcp 組
uv sync --group dev --group mcp

# 只補裝 fastmcp（已有 .venv 時）
uv sync --group mcp

# 臨時跑一條命令（會解析 group）
uv run --group mcp python -m scripts.mcp_rag_search
uv run --group mcp fastmcp list scripts/mcp_rag_search.py --json
```

Hermes 配置裡的 `command` 請指向 **本 repo 的 venv Python**（由 uv 建立）：

```text
/path/to/RAG-Agent/.venv/bin/python
```

不要用系統 `python3`，否則缺 LanceDB / BGE / fastmcp。

---

## 安裝與接線 Hermes

### 1. 安裝 MCP 依賴

```bash
cd ~/Documents/Project/RAG-Agent   # 改成你的路徑
uv sync --group mcp
```

### 2. 註冊 MCP server

```bash
# 把 REPO 換成實際路徑
REPO="$HOME/Documents/Project/RAG-Agent"

hermes mcp add counseling-rag \
  --command "$REPO/.venv/bin/python" \
  --args -m scripts.mcp_rag_search
```

或在 `~/.hermes/config.yaml` 手動寫：

```yaml
mcp_servers:
  counseling-rag:
    command: "/Users/YOU/Documents/Project/RAG-Agent/.venv/bin/python"
    args: ["-m", "scripts.mcp_rag_search"]
    # 首次 retrieve 會載入 BGE-M3，可能 >30s
    timeout: 180
```

### 3. 驗證連線

```bash
hermes mcp list
hermes mcp test counseling-rag
```

通過後，Hermes 會把工具註冊成類似：

- `mcp_counseling_rag_search_sessions`
- `mcp_counseling_rag_get_index_settings`
- `mcp_counseling_rag_list_workspaces`

（前綴 = `mcp_<server名>_<tool名>`）

### 4. 對話試用

```bash
hermes chat -q "用 counseling 知識庫搜尋：我哪次談到買遊戲電腦的糾結？"
```

或在 Hermes CLI / gateway 裡自然問過去諮詢內容；agent 應呼叫 `search_sessions`。

---

## 測試方式

### A. 單元測試（mock，不連真庫、不載模型）

```bash
cd /path/to/RAG-Agent
uv sync --group dev
uv run pytest tests/unit/test_mcp_rag_search.py -v
uv run pytest tests/unit/test_imports.py -k mcp -v
```

覆蓋：窗口序列化、預設 workspace、空 query、retrieve 例外、list_workspaces、
`create_mcp` 在有/無 fastmcp 時的行為。

### B. FastMCP CLI 煙霧（需 `--group mcp`）

```bash
uv sync --group mcp

# 列出 tools / schema
uv run --group mcp fastmcp list scripts/mcp_rag_search.py --json

# 直接呼叫（會走真 retrieve + BGE；首次較慢）
uv run --group mcp fastmcp call scripts/mcp_rag_search.py \
  search_sessions query="買遊戲電腦" --json

uv run --group mcp fastmcp call scripts/mcp_rag_search.py \
  get_index_settings --json

uv run --group mcp fastmcp call scripts/mcp_rag_search.py \
  list_workspaces --json
```

### C. 純 Python 呼叫（不經 MCP 協議）

```bash
uv run --group mcp python -c "
from scripts.mcp_rag_search import search_sessions, list_workspaces_info, get_index_settings
print(get_index_settings())
print(list_workspaces_info())
print(search_sessions('買遊戲電腦'))  # k=None → 完全跟 UI
"
```

### D. Hermes 端到端

```bash
hermes mcp test counseling-rag
hermes chat -q "列出 counseling 知識庫可用的 workspace，並搜尋「图式」相關片段"
```

### E. 手動 stdio 起 server（除錯）

```bash
uv run --group mcp python -m scripts.mcp_rag_search
# 進程會掛在 stdio 等 MCP client；Ctrl+C 結束
```

---

## 回傳格式（`search_sessions`）

成功：

```json
{
  "ok": true,
  "query": "買遊戲電腦",
  "workspace_id": "counseling",
  "count": 2,
  "k_override": null,
  "settings_used": {
    "source": "private.nosync/index_settings.json (Streamlit ⚙️ 索引設置)",
    "retrieval": { "top_k": 8, "window_expand": 1 },
    "reranker": {
      "use_reranker": true,
      "rerank_top_k": 30,
      "final_top_k": 4,
      "min_score": 0.2,
      "min_keep": 2
    },
    "notes": { "tracks_ui": true, "k_override": "..." }
  },
  "results": [
    {
      "source_file": "...txt",
      "session_date": "2026-07-04",
      "start_ts": "...",
      "end_ts": "...",
      "chunk_index_range": [3, 5],
      "text": "...",
      "rank": 0,
      "score": 0.42,
      "below_threshold": false
    }
  ]
}
```

失敗（空 query、空庫、其他例外）——**不靜默回空列表**（盡量仍附 `settings_used`）：

```json
{
  "ok": false,
  "error": "Table 'sessions' was not found",
  "workspace_id": "counseling",
  "settings_used": { "...": "與 UI 當前值相同" }
}
```

---

## 隱私與限制

- 諮詢逐字稿敏感：**只走本機 stdio**，不要改成公網 HTTP。
- 檢索本身不出網（BGE / LanceDB 本地）；無需給此 MCP 傳 LLM API key。
- 首次 `search_sessions` 可能因載入 embedding 而慢；Hermes 側 `timeout: 180`。
- 同一 Hermes session 內 MCP 子進程常駐，後續呼叫會快很多。

---

## 卸載 / 停用

```bash
hermes mcp remove counseling-rag
# 或在 config.yaml 設 enabled: false
```

刪除 MCP 條目不影響 Streamlit 問答與入庫。

---

## 相關程式與文檔

| 路徑 | 說明 |
|------|------|
| `scripts/mcp_rag_search.py` | MCP server + 可測純函式 |
| `scripts/ask.py` → `retrieve()` | 檢索真相源 |
| `tests/unit/test_mcp_rag_search.py` | 單元測試 |
| `docs/API_REFERENCE.md` | 函式簽名 |
| `docs/ARCHITECTURE.md` | 資料流 |
| `pyproject.toml` → `[dependency-groups].mcp` | uv 依賴組 |
