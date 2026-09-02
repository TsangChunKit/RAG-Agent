# API Reference

## 目的

**为 AI 开发者提供明确的 API 规范，避免类型错误和调用错误。**

每个模块列出：
- 导出的函数/类
- 参数类型
- 返回类型
- 是否可选
- 典型用法

---

## config.py

### 路径配置（⚠️ 全部是函数，需要调用）

```python
# ❌ 错误用法
if CHAT_MEMORY_PATH.exists():  # AttributeError!

# ✅ 正确用法
if CHAT_MEMORY_PATH(workspace_id).exists():
```

#### 路径函数列表

| 函数名 | 参数 | 返回 | 说明 |
|--------|------|------|------|
| `RAW_DIR(workspace_id)` | `Optional[str]` | `Path` | 原始文档目录 |
| `PROCESSED_DIR(workspace_id)` | `Optional[str]` | `Path` | 处理后文档目录 |
| `SUMMARIES_DIR(workspace_id)` | `Optional[str]` | `Path` | 摘要目录 |
| `GRAPH_FRAGMENTS_DIR(workspace_id)` | `Optional[str]` | `Path` | 图谱片段目录 |
| `CHAT_SESSIONS_DIR(workspace_id)` | `Optional[str]` | `Path` | 对话会话目录 |
| `DB_DIR(workspace_id)` | `Optional[str]` | `Path` | LanceDB 目录 |
| `GRAPH_JSON_PATH(workspace_id)` | `Optional[str]` | `Path` | 主图谱文件 |
| `CHAT_GRAPH_JSON_PATH(workspace_id)` | `Optional[str]` | `Path` | 对话图谱文件 |
| `LONG_TERM_MEMORY_PATH(workspace_id)` | `Optional[str]` | `Path` | 长期记忆文件 |
| `CHAT_MEMORY_PATH(workspace_id)` | `Optional[str]` | `Path` | 对话记忆文件 |
| `GRAPH_NODE_EMBEDDINGS_PATH(workspace_id)` | `Optional[str]` | `Path` | 图谱锚点节点 embedding 持久化缓存（`.npz`，见下方 `find_relevant_graph_nodes()`）|

**重要**：所有路径都是函数，必须先调用才能使用 Path 方法。

### 常量

| 名称 | 类型 | 值 |
|------|------|-----|
| `LANCEDB_TABLE_NAME` | `str` | `"sessions"` |
| `BASE_DIR` | `Path` | 项目根目录 |
| `PRIVATE_DIR` | `Path` | `private.nosync/` |

---

## scripts/ask.py

### 主要函数

#### `answer()`
```python
def answer(
    question: str,
    history: Optional[list[dict]] = None,
    k: Optional[int] = None,
    use_full_history: bool = False,
    max_context: int = 450_000,
    max_turns: int = 60
) -> dict:
    """
    问答主入口。
    
    Args:
        question: 用户问题
        history: 对话历史 [{"role": "user"/"assistant", "content": str}]
        k: 检索片段数量（None = 使用配置）
        use_full_history: 是否使用完整历史（不压缩）
        max_context: 最大上下文长度
        max_turns: 历史保留最大轮数
    
    Returns:
        {
            "answer": str,                    # LLM 回答
            "sources": list[dict],            # 检索到的来源片段
            "input_tokens": int,              # 输入 token 数（向后兼容旧字段名）
            "token_usage": dict,              # {input, output, thinking, cached, total}
            "matched_graph_nodes": list[dict],# 匹配的图谱节点 [{"type", "label"}]
            "api_content": str,               # 完整内容（供显示/调试）
            "history_content": str,           # 精简版（检索片段+问题，供历史回放）
            "compression_info": Optional[dict], # 压缩信息（如果触发了压缩）
        }
    """
```

#### `retrieve()`
```python
def retrieve(
    query: str,
    k: Optional[int] = None,
    workspace_id: Optional[str] = None
) -> list[dict]:
    """
    混合检索（稠密语义 + FTS 关键词，RRF 融合）→ 可选 cross-encoder 精排
    → 相关性阈值过滤 → 父块/窗口扩展。

    注意：query 进来后先过 text_norm.to_simplified()（繁→简），embed / FTS / reranker
          三条腿共用归一化后的文本。索引侧的 Chunk.text 也是简体，两边对齐。

    Args:
        query: 查询文本
        k: 返回结果数量（None = 使用「⚙️ 索引设置」当前值）
        workspace_id: workspace ID（None = 当前 workspace）。底层的 LanceDB 连接/
            全量 chunk DataFrame 不做进程级缓存、每次都按这个参数重新读取，避免
            process 存活期间切换 workspace 后沿用旧 workspace 的连接（2026-07-30
            修复：曾经是不分 workspace 的全域缓存，切换 workspace 后检索会沿用
            上一个 workspace 的向量库）。
    
    Returns:
        [
            {
                "source_file": str,          # 来源文件
                "session_date": str,         # 日期 YYYY-MM-DD
                "start_ts": str,             # 窗口起始时间戳
                "end_ts": str,               # 窗口结束时间戳
                "chunk_index_range": tuple,  # (起始 chunk_index, 结束 chunk_index)
                "text": str,                 # 片段文本（父块/窗口扩展后）
                "rank": int,                 # 排序，**从 0 起算**（越小越相关）
                "score": Optional[float],    # 窗口内最高的精排分数；关了 reranker 时为 None
                "below_threshold": bool,     # 有分数且低于 min_score（= 保底片段）
            }
        ]
    """
```

**相关性阈值（`min_score` / `min_keep`）**：精排之后再过一道 `_filter_by_score()`，
分数低于 `min_score` 的候选不进上下文；**只有"一条都没过线"时**才退回分数最高的
`min_keep` 条，并由 `_format_retrieved()` 在 prompt 最前面加一句"相关性都很低、不要硬套"。
两个参数都在「⚙️ 索引设置」→ Reranker 里热调，`min_score = 0` 即关掉整个机制
（行为回到改动前）。分数量级见下方 `scripts/reranker.py` 一节。

> `score is None` 表示"没有分数可比"（关了 reranker，或走的是心智地图证据日那条内存检索
> 通路），**不等于"不相关"**——下游不要把 None 当成低分处理。

> ⚠️ **空向量库**（该 workspace 还没入过库、`sessions` 表不存在）时 `retrieve()` 抛
> `ValueError: Table 'sessions' was not found`，**不是**返回 `[]`。这是有意的：静默返回空会把
> "库是空的"伪装成"检索不到"。UI 侧的防线在 `app.py`——先看 `list_indexed_records()`，
> 空库直接提示"向量库还是空的"，压根不会走到这里。

#### `_filter_by_score()`（内部）
```python
def _filter_by_score(hits: pd.DataFrame, min_score: float, min_keep: int) -> pd.DataFrame:
    """
    丢掉 rerank_score < min_score 的候选（hits 已按分数降序）。
    一条都没过线时退回 hits.head(min_keep)。
    min_score <= 0、没有 rerank_score 列、或空表 → 原样返回（= 关掉这个机制）。
    """
```

#### `load_system_instruction()`
```python
def load_system_instruction(workspace_id: Optional[str] = None) -> str:
    """
    加载 system instruction。
    
    优先级：
    1. Workspace 专用文件
    2. 全局文件
    3. 默认值
    
    Args:
        workspace_id: Workspace ID（None = 当前）
    
    Returns:
        System instruction 文本
    """
```

#### `save_system_instruction()`
```python
def save_system_instruction(text: str, workspace_id: Optional[str] = None) -> None:
    """
    保存 system instruction（workspace 感知）。

    内容相对当前版本有实际变化时，自动追加一条版本历史记录（见下方
    `list_system_instruction_history()`），摘要由 LLM 生成（profile="summary"）；
    LLM 调用失败会降级为占位摘要（不阻塞保存本身）。内容未变（原样点保存）
    不产生新记录，也不调用 LLM。

    Args:
        text: 新的 system instruction 内容
        workspace_id: Workspace ID（None = 当前）
    """
```

#### `reset_system_instruction()`
```python
def reset_system_instruction(workspace_id: Optional[str] = None) -> str:
    """
    重置 system instruction 为默认值（workspace 感知），内部走 save_system_instruction()，
    因此同样会追加一条版本历史记录。

    Returns:
        重置后的默认文本（DEFAULT_SYSTEM_INSTRUCTION）
    """
```

#### `list_system_instruction_history()`
```python
def list_system_instruction_history(limit: int = 50, workspace_id: Optional[str] = None) -> list[dict]:
    """
    读取 system instruction 版本历史（workspace 感知），最新的在前。

    Args:
        limit: 最多返回几条
        workspace_id: Workspace ID（None = 当前）

    Returns:
        [{"ts": str, "content": str, "summary": str}, ...]
        ts 为 ISO 8601 时间戳（含时区），content 为该版本的完整内容，
        summary 为 LLM 生成的变更摘要（保存触发）或固定文案（恢复触发）。
    """
```

#### `get_system_instruction_version()`
```python
def get_system_instruction_version(ts: str, workspace_id: Optional[str] = None) -> Optional[dict]:
    """
    按时间戳取某条历史版本的完整记录（workspace 感知）。

    Returns:
        找到则返回 {"ts", "content", "summary"}，找不到返回 None。
    """
```

#### `restore_system_instruction_version()`
```python
def restore_system_instruction_version(ts: str, workspace_id: Optional[str] = None) -> str:
    """
    把 system instruction 恢复到 ts 对应的历史版本（workspace 感知）。

    恢复动作本身也会追加一条历史记录（摘要固定为「已恢复至 {ts} 版本」，不调用 LLM）。

    Args:
        ts: 目标版本的时间戳（来自 list_system_instruction_history() 的 "ts" 字段）
        workspace_id: Workspace ID（None = 当前）

    Returns:
        恢复后的内容

    Raises:
        ValueError: 找不到对应时间戳的历史版本
    """
```

版本历史存储在 `SYSTEM_INSTRUCTION_HISTORY_PATH(workspace_id)`（`data/system_instruction_history.jsonl`，
append-only、永久保留，不做清理）。Streamlit「⚙️ System Instruction 设置」弹窗内的
「🕓 版本历史」区块基于这四个函数实现：列表展示 + 展开查看全文 + 恢复（恢复前需二次确认）。

### 辅助函数

| 函数 | 作用 | 返回类型 |
|------|------|---------|
| `sanitize(q: str)` | 清理查询字符串 | `str` |
| `extract_mentioned_dates(question: str)` | 提取提到的日期（`2026年7月4日` / `2026-07-04` / `2026/7/4` / 中文数字 / 英文月份缩写或全名如 `2026-Aug-16`，大小写不敏感；不支持无年份的相对日期，如「今天」见 `session_resolver.resolve()`） | `list[str]` |
| `find_relevant_graph_nodes(question, graph, top_k, workspace_id)` | GraphRAG 节点匹配 | `list[dict]` |

**`find_relevant_graph_nodes()` 的节点向量持久化（2026-07-30）**：GRAPH_ANCHOR_TYPES
（schema/belief/mode/coping）节点的 embedding 按 workspace 持久化在
`GRAPH_NODE_EMBEDDINGS_PATH(workspace_id)`（`.npz`），按节点逐一存内容哈希（sha256）+
向量。每次调用时逐节点比对哈希：命中就直接复用磁盘里的向量，只有新增节点/描述变动的节点
才会重新调用 embedding 模型——图谱重新生成后通常只有少数节点变动，不必整批重算，也不需要
进程内存快取（磁盘本身就是跨 workspace、跨 process 都正确的持久层）。

---

## scripts/mcp_rag_search.py

Hermes / 外部 Agent 用的 **MCP 檢索 server**（stdio）。薄包裝 `ask.retrieve()`，
預設 workspace `counseling`。完整接線與測試見 [HERMES_MCP.md](HERMES_MCP.md)。

依賴：uv group `mcp`（`fastmcp`）。主應用不需要。

```bash
uv sync --group mcp
uv run --group mcp python -m scripts.mcp_rag_search
```

#### `serialize_window()`
```python
def serialize_window(window: dict[str, Any]) -> dict[str, Any]:
    """把 retrieve() 窗口轉成 JSON-safe dict（tuple → list 等）。"""
```

#### `get_index_settings()`
```python
def get_index_settings() -> dict[str, Any]:
    """
    唯讀：與 Streamlit「⚙️ 索引設置」當前一致的查詢期參數
    （private.nosync/index_settings.json）。MCP 無獨立配置、不寫檔。
    """
```

#### `search_sessions()`
```python
def search_sessions(
    query: str,
    k: Optional[int] = None,
    workspace_id: str = "counseling",
) -> dict[str, Any]:
    """
    混合檢索（呼叫 ask.retrieve）。索引參數與 UI 共用 index_settings.json；
    每次呼叫重新讀檔。k=None（建議）= 完全跟隨 UI。

    Returns:
        成功: {"ok", "query", "workspace_id", "count", "results", "k_override", "settings_used"}
        失敗: {"ok": False, "error", "workspace_id", "settings_used"?}
    """
```

#### `list_workspaces_info()`
```python
def list_workspaces_info() -> dict[str, Any]:
    """列出本機 workspace。成功: {"ok", "count", "workspaces": [{name, display_name, created_at}]}。"""
```

#### `create_mcp()` / `main()`
```python
def create_mcp():
    """建立 FastMCP 實例，註冊 tools: search_sessions, get_index_settings, list_workspaces。"""

def main() -> None:
    """stdio MCP 入口：chdir 到 repo root 後 mcp.run()。"""
```

**MCP tools（Hermes 端名稱會加 `mcp_<server>_` 前綴）**：

| Tool | 參數 | 說明 |
|------|------|------|
| `search_sessions` | `query`, `k=None`, `workspace_id="counseling"` | 只搜尋；`settings_used` 跟 UI |
| `get_index_settings` | （無） | 唯讀 UI 查詢期參數 |
| `list_workspaces` | （無） | 列出可用 workspace |

---

## scripts/streamlit_wake_server.py

轻量的 Streamlit 生命周期服务，只依赖 Python 标准库。固定入口监听 `0.0.0.0:8501`；
实际 Streamlit 监听 `127.0.0.1:8502`（Streamlit 本身仍接受外部接口连接）。访问 8501 时，
若 8502 尚未启动就调用 `counseling_agent_ctl.sh web-start`，就绪后浏览器自动跳转。

### `streamlit_is_healthy()`

```python
def streamlit_is_healthy(
    host: str = STREAMLIT_HOST,
    port: int = STREAMLIT_PORT,
    timeout: float = 1.0,
) -> bool:
    """探测 /_stcore/health；响应为 ok 才返回 True，连接失败返回 False。"""
```

### `run_control()`

```python
def run_control(action: str, *, runner: Runner = subprocess.run) -> None:
    """
    调用共享服务控制脚本。action 只允许 web-start / web-stop。

    Raises:
        ValueError: action 不受支持
        RuntimeError: 控制脚本返回非零状态
    """
```

### `has_active_clients()`

```python
def has_active_clients(
    port: int = STREAMLIT_PORT,
    *,
    runner: Runner = subprocess.run,
) -> bool:
    """
    用 /usr/sbin/lsof 判断 8502 是否有 ESTABLISHED TCP 连接。

    lsof 无匹配（exit 1 + 空输出）返回 False；探测本身失败会抛 RuntimeError，
    supervisor 遇到该错误会保留 Streamlit，避免把不可观测误判为空闲。
    """
```

### `IdleTracker` / `IdleSupervisor`

```python
IdleTracker(timeout_seconds: float)
IdleTracker.observe(
    *,
    streamlit_running: bool,
    has_clients: bool,
    now: float,
) -> bool

IdleSupervisor(
    *,
    timeout_seconds: float = 1800,
    health_probe=streamlit_is_healthy,
    client_probe=has_active_clients,
    stop_streamlit=lambda: run_control("web-stop"),
    clock=time.monotonic,
)
IdleSupervisor.check_once() -> None
IdleSupervisor.note_wake_activity() -> None
IdleSupervisor.run_forever(stop_event, interval_seconds=30) -> None
```

`IdleTracker.observe()` 只有在 Streamlit 持续运行、且持续没有客户端达到 timeout 时返回 True；
服务停止或重新出现客户端都会清空计时。默认 30 分钟。浏览器保持 Streamlit 分页时，
WebSocket 属于 established connection，不会误关。客户端探测失败也会清空当前 idle 观察，
必须重新累积一段完整且可观测的 30 分钟；根页面唤醒请求会通过共享 lifecycle lock 重置观察，
不会和 supervisor 的 stop 决策竞态。

`web-stop` 发送 SIGTERM 后最多等待 15 秒，直到 launchd job 不再是 `state = running` 才返回成功。
因此 lifecycle lock 覆盖的是完整停止，而不只是信号发送；后续根请求不会误把正在退出的旧进程
判断为健康而跳过重启。

### HTTP 与入口

```python
create_server(
    host: str = "0.0.0.0",
    port: int = 8501,
    *,
    wake_activity=lambda: None,
    lifecycle_lock=None,
) -> WakeHTTPServer
main() -> None
```

| 路径 | 行为 |
|------|------|
| `/` | 确保 Streamlit 启动，返回轮询页；就绪后跳到同一 hostname 的 8502 |
| `/status` | `{"ready": bool}`，只探测、不触发启动 |
| `/health`, `/_stcore/health` | gateway 自身健康检查，返回 `ok` |
| 其他路径 | 返回 404、不触发启动；避免旧 Streamlit 分页的 `/_stcore/*` 轮询误唤醒 |

launchd 环境变量可覆盖 `RAG_WAKE_HOST`、`RAG_WAKE_PORT`、`RAG_STREAMLIT_HOST`、
`RAG_STREAMLIT_PORT`、`RAG_STREAMLIT_IDLE_SECONDS` 与 `RAG_STREAMLIT_CHECK_SECONDS`。

---

## scripts/workspace_manager.py

### 核心函数

#### `get_current_workspace()`
```python
def get_current_workspace() -> str:
    """
    获取当前 workspace ID。
    
    优先级：
    1. Streamlit session_state
    2. 环境变量 CURRENT_WORKSPACE
    3. 旧路径数据检测（存在则回退 "_legacy"）
    4. WORKSPACES_ROOT 下按字母序的第一个 workspace
    5. 兜底 "_legacy"
    
    Returns:
        Workspace ID 字符串
    """
```

#### `create_workspace()`
```python
def create_workspace(
    name: str,
    display_name: str,
    domain: str,
    graph_schema_mode: str = "generic",
    schema_file: Optional[str] = None
) -> Path:
    """
    创建新 workspace。
    
    Args:
        name: Workspace ID（slug 格式，如 "my-notes"；不能为 "_legacy"）
        display_name: 显示名称（如 "我的笔记"）
        domain: 领域（"counseling", "generic", "sutras", "solution_arch" 等）
        graph_schema_mode: Schema 模式（"predefined", "generic", "custom"，默认 "generic"）
        schema_file: Schema 文件名（mode="predefined" 时必需）
    
    Returns:
        Path: 创建的 workspace 根目录
    
    Raises:
        ValueError: Workspace 已存在或参数无效
    """
```

#### `list_workspaces()`
```python
def list_workspaces() -> list[dict]:
    """
    列出所有 workspaces。
    
    Returns:
        [
            {
                "name": str,          # Workspace ID
                "display_name": str,  # 显示名称
                "created_at": str,    # 创建时间 ISO 8601（_legacy 为 None）
            }
        ]

    注意：返回项不含 domain 字段；如需 domain 请用 load_workspace_config(name)。
    列表包含 _legacy（若旧路径数据存在），并按 name 排序。
    """
```

#### `get_workspace_dir()`
```python
def get_workspace_dir(workspace_id: Optional[str] = None) -> Path:
    """
    获取 workspace 根目录（None = 当前 workspace）。

    降级顺序：workspace 存在 → 返回它；WORKSPACES_ROOT 整个不存在 → 返回 PRIVATE_DIR
    （旧结构兼容）；否则 raise ValueError("Workspace not found: ...")。
    workspace_id == "_legacy" 时直接返回 PRIVATE_DIR。
    """
```

#### `load_workspace_config()`
```python
def load_workspace_config(workspace_id: Optional[str] = None) -> dict:
    """
    加载 workspace 配置，缺失字段用 DEFAULT_WORKSPACE_CONFIG 顶上。

    ⚠️ 配置文件是坏 JSON 时**不抛异常**：打印一行警告后整体降级到
    DEFAULT_WORKSPACE_CONFIG。刻意如此——配置被编辑器写坏时整个 app 不该打不开，
    用户至少还能进 UI 把它改回来。
    """
```

#### `set_current_workspace()`
```python
def set_current_workspace(workspace_id: str) -> None:
    """
    切换当前 workspace：写 st.session_state（非 Streamlit 环境退回环境变量
    CURRENT_WORKSPACE）。

    ⚠️ st.session_state 是**进程级**的，测试里必须清（见 tests/conftest.py），
    也不要 patch 成普通 dict——`{}` 不支持属性赋值，这行会 AttributeError。
    """
```

---

## scripts/index_settings.py

六组索引参数的 JSON 读写层（`private.nosync/index_settings.json`）。**只有分组读函数 + 全量
写函数**，没有 `load()`、没有 `update(key=value)`：分组是刻意的，混成一个扁平 dict 就看不出
哪些改动要重建索引、哪些下一次问答就生效。

```python
retrieval_params()      -> {"top_k", "window_expand"}                     # 查询期，立即生效
chunking_params()       -> {"chunk_size", "chunk_overlap"}                # 只影响之后新入库的
embedding_params()      -> {"model", "device", "batch_size", "use_fp16"}   # model/device 改动需重启
fts_params()            -> {"base_tokenizer", "ngram_min", "ngram_max"}    # 改完需重建索引
reranker_params()       -> {"use_reranker", "rerank_top_k", "final_top_k",
                            "min_score", "min_keep", "model", "device", "use_fp16"}
graph_evidence_params() -> {"max_dates", "fragments_per_date", "window_expand", "include_summary"}

load_for_ui() -> dict   # 上面六组打包成 {"retrieval": ..., "chunking": ..., ...}
save(retrieval, chunking, embedding, fts, reranker, graph_evidence) -> None  # 六组全给
reset() -> None         # 删掉设置文件 = 全部回退 config.py 常量
```

缺失/为 `None` 的字段一律用 `config.py` 常量顶上（`False` 会保留，不当作缺失）。

> ⚠️ **这一层刻意不做取值校验**：写 `chunk_size = -1` 会原样存下来。边界靠唯一的写入来源
> ——「⚙️ 索引设置」表单的 `st.number_input("分块大小 chunk_size（字符）", 100, 2000, ...)`。
> 改动那个 widget 的上下界时，记得 `tests/integration/test_edge_cases.py::test_negative_chunk_size`
> 会跟着炸（它断言了那行代码），这是提醒而不是噪音。

---

## scripts/graph_utils.py

#### `resolve_graph()`
```python
def resolve_graph(fragments: list[dict], threshold: float = MERGE_SIM_THRESHOLD,
                  schema: Optional[dict] = None) -> dict:
    """
    reduce 步：把逐份咨询抽出的子图归并成一张全局图（纯 Python + 一次本地 BGE 批量向量化，
    不调 LLM）。

    ⚠️ 只吃**一个** fragments 列表，第二个位置参数是相似度阈值——不是 edges。

    Args:
        fragments: [{"nodes", "edges", "session_date", ...}, ...]

    Returns:
        {"nodes": [...], "edges": [...]}；同类型内语义相似度 ≥ threshold 的节点合并成一个
        规范节点（id 形如 "schema:0"），description 取最长的那份、related_dates 取并集；
        边重映射到规范 id 后按 (源, 目标, 关系类型) 去重并合并 evidence_dates。
        空列表 / 节点为空的 fragment → {"nodes": [], "edges": []}。
    """
```

#### `merge_graphs()`
```python
def merge_graphs(therapy_graph: Optional[dict], chat_graph: Optional[dict]) -> Optional[dict]:
    """
    合并真实咨询图谱（source=therapy）和 AI 对话记忆图谱（source=chat）。

    ⚠️ 两个**固定角色**的位置参数，不是 merge_graphs([g1, g2])——正因为角色固定，
    source 标签才能自动打上（UI 靠它区分颜色）。
    任一边为 None 时原样返回另一边，两边都为 None 返回 None（优雅降级）。
    合并后会重算中心性（跨图的边会改变连接度，半张图上算的值不代表全局重要程度）。
    """
```

#### `compute_centrality()`
```python
def compute_centrality(graph: dict) -> None:
    """原地给每个节点写 degree_centrality / betweenness_centrality（保留 4 位小数）。"""
```

---

## scripts/index_records.py

#### `list_indexed_records()`
```python
def list_indexed_records(workspace_id: Optional[str] = None) -> list[dict]:
    """
    读 chunks.jsonl 按 source_file 聚合成已索引清单（真相源是分块产物，不查 LanceDB）。

    Returns:
        [{"source_file", "session_date", "n_chunks", "has_summary"}]，
        按 (session_date, source_file) **倒序**（新的在前）。
        文件不存在或为空 → []（app.py 用它判断"向量库还是空的"）。
    """
```

#### `load_change_log()`
```python
def load_change_log(limit: int = 50, workspace_id: Optional[str] = None) -> list[dict]:
    """读取最近 limit 条索引变更记录，最新的在前；坏行跳过（不让审计日志的半行毁掉整页）。"""
```

---

## scripts/chat_store.py

```python
new_session_id() -> str                       # 12 位十六进制
list_sessions(workspace_id=None) -> list[dict]
load_session(session_id, workspace_id=None) -> dict
save_session(session_id, title, messages, created_at=None, workspace_id=None) -> None
delete_session(session_id, workspace_id=None) -> None
make_title(first_message: str) -> str         # 换行压成空格，截 24 字 + "…"（单字符省略号）
```

---

## scripts/chunk.py

### 核心类型

#### `Chunk` (dataclass)
```python
@dataclass
class Chunk:
    id: str                # 唯一 ID
    session_date: str      # 会话日期 YYYY-MM-DD
    source_file: str       # 来源文件名
    chunk_index: int       # 在文件中的索引（从 0 开始）
    speakers: str          # 发言人（逗号分隔，去重）
    start_ts: str          # 起始时间戳
    end_ts: str            # 结束时间戳
    raw_text: str          # 原始拼接文本（不含上下文前缀），字形保持原样 → 展示/喂 LLM 用
    text: str              # 上下文前缀 + raw_text，再过繁→简归一化，供 embedding + FTS
    prev_chunk_id: Optional[str] = None  # 前一个 chunk 的 id
    next_chunk_id: Optional[str] = None  # 后一个 chunk 的 id
```

> ⚠️ 字段顺序即为 dataclass 定义顺序，位置构造请以此为准。

### 核心函数

#### `chunk_session()`
```python
def chunk_session(
    session: ParsedSession,
    workspace_id: Optional[str] = None
) -> list[Chunk]:
    """
    分块函数（滑动窗口 + 父块扩展）。
    
    Args:
        session: ParsedSession 对象
        workspace_id: Workspace ID
    
    Returns:
        Chunk 对象列表
    """
```

---

## scripts/text_norm.py

检索层的繁→简归一化。**系统不变量：所有进入检索/语义匹配的文本一律先过 `to_simplified()`，
展示用的原文（`Chunk.raw_text`、prompt 里的使用者问题）不动。**

### `to_simplified()`
```python
def to_simplified(text: str) -> str:
    """
    繁体 → 简体。已是简体则原样返回（幂等），非中文字符不受影响。

    Args:
        text: 任意文本（query / chunk 文本 / 图谱节点文本）

    Returns:
        简体化后的文本；zhconv 未安装或转换抛异常时返回原文（降级，不抛错）
    """
```

**调用点（只有三处）**：

| 位置 | 归一化对象 | 为什么 |
|-----|-----------|-------|
| `chunk.py::chunk_session()` | `Chunk.text`（索引字段） | 让索引侧统一简体；对现有简体语料是 no-op，**无需重建索引** |
| `ask.py::retrieve()` | query | 覆盖 embed + FTS + reranker 三条腿 |
| `ask.py::find_relevant_graph_nodes()` | question | 图谱节点标签/描述是简体 |

**为什么必须做**：FTS（jieba 或 ngram）与 dense 对繁简变体都不可靠，「水煙」和「水烟」易漏。实测繁体
query「我喜歡抽水煙」检索不到语料里唯一提到「水烟」的两个块，归一化后两个都能命中。

依赖 `zhconv`（纯 Python，无编译依赖）。缺失时降级为原样返回。

---

## scripts/reranker.py

### `rerank_candidates()`
```python
def rerank_candidates(query: str, hits: pd.DataFrame, top_k: int) -> pd.DataFrame:
    """
    对 hybrid 候选做 cross-encoder（bge-reranker-v2-m3）精排。

    Returns:
        列结构和输入一致，额外多一列 rerank_score（0–1，越大越相关）；
        按 rerank_score 降序、截断到 top_k。任何一步失败 → 回退为输入顺序的前 top_k 条。
    """
```

> ⚠️ **只归一化一次**：sentence-transformers 5.x 的 `CrossEncoder` 会按模型 config 自带的
> `activation_fn` 归一化（bge-reranker-v2-m3 配的是 `Sigmoid`），`predict()` 返回的已经是 0–1。
> 所以 `rerank_candidates()` 先检查 `model.activation_fn`，只补上"缺失的那一次" sigmoid。
> 曾经的 bug：多 sigmoid 一次 → 无关片段（logit ≈ -11 → 1.6e-5）被挤到 0.500004、彼此只差
> 1e-6，fp16 下直接相等 → 排序全是 tie，精排退化成 hybrid 原序。

**分数量级参考**（2026-07-25 实测本项目语料，chunk 是长段落对话，绝对分数偏低）：

| query | top1 rerank_score | 说明 |
|------|------------------|------|
| 「我和妈妈的关系」 | 0.87 | 切题且语料覆盖厚 |
| 「工作压力」 | 0.79 | 同上 |
| 「我對伴侶的依戀模式」 | 0.42 | 切题（繁体，归一化后命中）|
| 「我喜歡抽水煙」 | 0.18 | 切题但语料里只提过两次 |
| 「怎么修理汽车引擎」 | 0.10 | ⚠️ 无关却不低——重叠区 |
| 「最近的焦虑和睡眠」 | 0.035 | ⚠️ 切题却不高——语料覆盖少 |
| 「今天天氣不錯我想吃拉麵」 | 0.0035 | 完全无关 |
| 「巴黎奥运会金牌榜」 | 0.0003 | 完全无关 |

> **绝对值都远低于 0.5**，相关性阈值必须按 0.01 量级定标，不要照搬通用的 0.5。
> 而且相关/无关**存在重叠区**（0.03–0.10），所以阈值只用来砍掉 0.001 量级的噪音尾巴，
> 模糊区交给 `min_keep` 保底 + prompt 里的低相关警告，见 `retrieve()` 与 `config.py`
> 的 `RERANKER_MIN_SCORE`。

---

## scripts/parse.py

### 核心类型

#### `Utterance` (dataclass)
```python
@dataclass
class Utterance:
    speaker: str      # 发言人
    timestamp: str    # 时间戳 HH:MM:SS（伪发言人小节沿用上一条真实时间戳）
    text: str         # 发言内容
    line_no: int      # 行号
```

> ⚠️ 首两字段顺序为 `speaker, timestamp`（与直觉相反），位置构造请注意。

#### `ParsedSession` (dataclass)
```python
@dataclass
class ParsedSession:
    source_file: str        # 文件名
    session_date: str       # 日期 YYYY-MM-DD，来自文件名
    file_datetime: str      # 文件名原始 14 位数字（生成时间，仅记录）
    utterances: list[Utterance] = field(default_factory=list)  # 发言列表
```

### 核心函数

#### `parse_transcript()`
```python
def parse_transcript(path: Path) -> ParsedSession:
    """
    解析逐字稿文件。
    
    Args:
        path: 文件路径 Path 对象（文件名必须包含日期；内部调用 path.name / path.read_text）
    
    Returns:
        ParsedSession 对象
    
    Raises:
        ValueError: 文件名格式不正确
    """
```

---

## scripts/ingest_new.py

增量入库：把 `raw/` 里的新逐字稿一条龙做完（parse → chunk → 向量化 → LanceDB append →
摘要 → 更新长期记忆 → 记变更记录）。看门狗、UI 的「⚡ 立即入库」按钮、命令行三条通路都走这里。

### 核心函数

#### `ingest_new_file()`
```python
def ingest_new_file(path: Path, force: bool = False, workspace_id: Optional[str] = None) -> dict:
    """
    增量摄取单个文件（幂等；已入库/已有摘要的步骤会跳过，除非 force=True）。

    Args:
        path: 逐字稿路径（不在 raw/ 下会先 copy 进去）
        force: 强制重新入库 + 重新生成摘要
        workspace_id: workspace 名称，None = 当前 workspace

    Returns:
        该场的结构化摘要 dict

    Raises:
        ValueError: 文件名没有 14 位日期前缀（parse 阶段）
        Exception: 向量化 / LanceDB / LLM 任一步失败都会原样抛出
    """
```

⚠️ **写入顺序不变量**：内部必须先 `ingest()` 成功、再把 chunk 追加进 `chunks.jsonl`。
`chunks.jsonl` 是「已入库」的真相源，顺序反了会让失败的文件被当成已入库、永不重试
（详见 `docs/ARCHITECTURE.md` §1b）。

#### `pending_raw_files()`
```python
def pending_raw_files(
    workspace_id: Optional[str] = None,
    stable_seconds: int = 0,
    skip: Optional[Set[str]] = None,
) -> List[Path]:
    """
    raw/ 里还没入库的 .txt（按文件名排序）。「没入库」= 文件名不在 chunks.jsonl 的
    source_file 集合里。这是待入库判定的**单一真相源**：看门狗和 UI 按钮都调它。

    Args:
        workspace_id: workspace 名称，None = 当前 workspace
        stable_seconds: >0 时只返回 mtime 已稳定这么多秒的文件（看门狗传 30，避开还在
            复制途中的半个文件）；UI/命令行传 0
        skip: 额外跳过的文件名集合（看门狗用它跳过本进程内已知永久失败的文件）

    Returns:
        待入库文件路径列表（raw 目录不存在时为空列表）
    """
```

用法：
```python
from scripts.ingest_new import pending_raw_files

# UI：列出待入库文件（不等稳定窗口）
pending = pending_raw_files(workspace_id="counseling")

# 看门狗：只要写完 30 秒以上的，且跳过已知坏文件
pending = pending_raw_files("counseling", stable_seconds=30, skip=_failed)
```

#### `ingest_pending()`
```python
def ingest_pending(
    workspace_id: Optional[str] = None,
    force: bool = False,
    stable_seconds: int = 0,
) -> Dict[str, list]:
    """
    把 raw/ 里所有待入库的逐字稿逐份入库（UI「⚡ 立即入库」按钮的后端）。
    一份失败不影响其余，失败信息原样返回给调用方渲染（不打印、不抛出）。

    Returns:
        {"ingested": [文件名, ...], "failed": [{"file": 文件名, "error": 错误信息}, ...]}
    """
```

用法（app.py 里就是这么用的）：
```python
result = ingest_pending()                      # 当前 workspace
st.success("已入库：" + "、".join(result["ingested"]))
for f in result["failed"]:
    st.error(f"❌ {f['file']}：{f['error']}")
```

#### `missing_summary_files()`
```python
def missing_summary_files(workspace_id: Optional[str] = None) -> List[str]:
    """
    已入库但摘要 JSON 还没生成的 source_file 列表（workspace 感知）。

    直接复用 index_records.list_indexed_records() 的 has_summary 判定，不重新发明一套
    判断逻辑。典型成因：chunks/向量化都成功了，但摘要那步 LLM 调用失败（比如 API key/
    OAuth 过期）——这类文件不会出现在 pending_raw_files() 里（chunks.jsonl 已经有它），
    看门狗也不会重试，只能靠这个函数找回来。

    Args:
        workspace_id: workspace 名称，None = 当前 workspace

    Returns:
        缺摘要的 source_file 文件名列表
    """
```

#### `regenerate_missing_summaries()`
```python
def regenerate_missing_summaries(workspace_id: Optional[str] = None) -> Dict[str, list]:
    """
    把已入库但缺摘要的文件逐份补生成摘要（UI「🔁 补生成摘要」按钮的后端）。
    只重跑摘要这一步，不碰 chunks/LanceDB。一份失败不影响其余，失败信息原样返回给
    调用方渲染（不打印、不抛出）。至少成功一份才会刷新长期记忆。

    Returns:
        {"generated": [文件名, ...], "failed": [{"file": 文件名, "error": 错误信息}, ...]}
    """
```

用法（app.py「📚 已索引的咨询记录」弹窗里就是这么用的）：
```python
missing = missing_summary_files()
if missing:
    result = regenerate_missing_summaries()
    st.success("已补生成摘要：" + "、".join(result["generated"]))
    for f in result["failed"]:
        st.error(f"❌ {f['file']}：{f['error']}")
```

---

## scripts/settings.py

运行期可调参数 + LLM 后端选择的读写口。每次调用都重读 `GEMINI_SETTINGS_PATH`，改完下一次调用即生效。

### Provider 常量

```python
VALID_PROVIDERS = ("grok", "hermes", "copilot_cli")  # UI 选项直接读取
DEFAULT_PROVIDER = "hermes"            # 非法/缺失/已停用时的回退值，必须 ∈ VALID_PROVIDERS
DISABLED_PROVIDERS = {"gemini": "……停用原因（UI 会显示）"}  # 与 VALID_PROVIDERS 不相交
```

`copilot_cli` 通过本机已登录的 GitHub Copilot CLI 非交互运行：所有工具、内置 MCP、项目 instructions
和 remote export 均关闭，单次超时 300 秒。它返回相同的 `_Response` 形状，但 token usage 字段为 0；
`response_schema` 会写入 prompt，返回后至少执行 JSON 语法验证。CLI 不支持等价的 temperature 和
max-output-tokens 参数。

> `gemini` 当前停用（产品选择）。原 Python 3.9 / google-genai 1.x 的 `thinking_level` 技术
> blocker 已在 3.12 + google-genai 2.x 下解除。恢复步骤见 README §七「gemini 为什么停用」。

### `provider()`

```python
def provider() -> str:
    """
    当前 LLM 后端。

    Returns:
        VALID_PROVIDERS 之一；设置文件里的值非法、缺失或已停用（在 DISABLED_PROVIDERS 里）
        时返回 DEFAULT_PROVIDER。
    """
```

### `load_for_ui()`

```python
def load_for_ui() -> dict:
    """
    给设置 UI 用的当前生效值（不回传 key 明文）。

    Returns:
        {
          "provider": str,                 # 当前后端
          "disabled_providers": dict,      # {provider 名: 停用原因}，UI 用来显示"为什么没这个选项"
          "dialogue": dict,                # {model, thinking_level, temperature, max_output_tokens}
          "summary": dict,                 # {model, thinking_level, temperature}
          "summary_max_tokens": dict,      # {text, chat_graph, therapy_graph}
          "api_key_set": bool,
          "xai_api_key_set": bool,
          "hermes_base_url": str,
        }
    """
```

### `save()`

```python
def save(dialogue: dict, summary: dict, summary_max: dict,
         api_key: Optional[str] = None, provider: Optional[str] = None,
         xai_api_key: Optional[str] = None, hermes_api_key: Optional[str] = None,
         hermes_base_url: Optional[str] = None) -> None:
    """
    写回设置。各 key 传空/None = 保留原值，传非空 = 覆盖。
    provider 只在 ∈ VALID_PROVIDERS 时写入——非法值和已停用值都会被忽略（不报错）。
    """
```

---

## 类型注解规范（Python 3.12）

项目钉 `requires-python = "==3.12.*"`。PEP 604（`X | Y`）与 `typing.Optional` / `Union` 都合法，
不强制某一种；存量代码大量使用 `Optional[...]`，新代码两种皆可。

```python
from typing import Optional, Union

def func_a() -> dict | None:       # ✅ PEP 604
def func_b() -> Optional[dict]:    # ✅ typing.Optional
def func_c() -> str | int:         # ✅
def func_d() -> Union[str, int]:   # ✅
```

---

## 常见错误速查

### 1. AttributeError: 'function' object has no attribute 'exists'

```python
# ❌ 错误
if CHAT_MEMORY_PATH.exists():

# ✅ 正确
if CHAT_MEMORY_PATH(workspace_id).exists():
```

### 2. NameError: name 'Optional' is not defined

```python
# ❌ 错误：导入在 docstring 内
"""Module docstring.
from typing import Optional
"""

# ✅ 正确：导入在 docstring 外
"""Module docstring."""
from typing import Optional
```

### 3. 缺少 workspace_id 参数

```python
# ⚠️ 警告：在 UI 代码中应该传递
build_graph()  # 使用默认 workspace

# ✅ 推荐：明确传递
build_graph(workspace_id=current_workspace)
```

### 4. ThinkingConfig: thinking_level Extra inputs are not permitted

```
1 validation error for ThinkingConfig
thinking_level  Extra inputs are not permitted [type=extra_forbidden, input_value='high']
```

**原因（历史）**：Gemini 3.x 用 `thinking_level` 取代了 `thinking_budget`；旧环境 Python 3.9
只能装到 `google-genai` 1.47.0，其 `ThinkingConfig` 不认 `thinking_level`。

**现状**：venv 已是 Python 3.12 + `google-genai` 2.x（`ThinkingConfig` 含 `thinking_level`），
该错误不应再出现。`gemini` provider 仍停用（产品选择，默认 hermes）；恢复步骤见 README §七。

**排查用**：

```python
from google.genai import types
print(list(types.ThinkingConfig.model_fields))  # 装的 SDK 到底支持哪些字段
```

---

## 快速参考

### 导入检查清单

开发新功能前，确认：
- [ ] 所有 `from config import` 的路径都是函数
- [ ] 类型注解合法（`Optional[X]` 或 `X | None` 皆可；Python 3.12）
- [ ] `from typing import` 在模块顶部，不在 docstring 内
- [ ] Workspace 相关函数传递 `workspace_id` 参数
- [ ] Mock 了所有外部依赖（LLM, Embeddings, DB）

### 测试检查清单

提交前确认：
- [ ] `pytest tests/unit/test_imports.py` 通过
- [ ] `python scripts/check_code_patterns.py` 通过
- [ ] 新增代码覆盖率 ≥ 80%
- [ ] 所有测试通过

---

## 扩展阅读

- [DEVELOPMENT_GUIDE.md](./DEVELOPMENT_GUIDE.md) - 开发指南
- [ARCHITECTURE.md](./ARCHITECTURE.md) - 系统架构
- [TESTING_STRATEGY.md](./TESTING_STRATEGY.md) - 测试策略
