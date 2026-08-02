# 系统架构

## 目的

**为 AI 开发者提供系统全貌，理解模块间关系，避免破坏依赖。**

---

## 核心数据流

### 1. 入库流程（Ingestion）

```
原始文档 (raw/*.txt)
    ↓
[parse.py] 解析逐字稿 → ParsedSession
    ↓
[chunk.py] 分块 → Chunk[]
    ↓
[embedder.py] 向量化 → Chunk[] + embeddings
    ↓
[ingest.py] 写入 LanceDB
    ↓
向量数据库 (db/sessions.lance)
```

**关键模块依赖**：
- `parse.py` → 独立（只依赖标准库）
- `chunk.py` → `parse.py`, `text_norm.py`, `workspace_manager.py`
- `embedder.py` → BGE-M3 模型
- `ingest.py` → `chunk.py`, `embedder.py`, LanceDB

#### 1b. 增量入库的三条触发通路（同一个 `ingest_new.py`）

```
                      ┌─ raw 入库看门狗（每 2 分钟，launchd 常驻）
raw/ 新增 .txt  ───────┼─ UI「📚 已索引的咨询记录」→「⚡ 立即入库」按钮
                      └─ 命令行 python -m scripts.ingest_new <文件>
                                    ↓
                    [ingest_new.pending_raw_files()]   ← 「哪些还没入库」的单一真相源
                                    ↓
                    [ingest_new.ingest_new_file()]  parse → chunk → ingest(append)
                                    → chunks.jsonl → summarize → update_memory → changelog
```

三条通路共用 `pending_raw_files()` 判定「没入库」（文件名不在 `chunks.jsonl` 的 `source_file`
集合里），差别只有：看门狗传 `stable_seconds=30`（避开还在复制的半个文件）并用 `skip=_failed`
记住永久失败的文件；UI/命令行传 0（用户的点击本身就是「已放好」的信号）。

**⚠️ 写入顺序是个不变量**：`ingest_new_file()` 必须先 `ingest()` 进 LanceDB、成功后才把 chunk
追加进 `chunks.jsonl`。因为 `chunks.jsonl` 是「已入库」的真相源，先写它等于提前宣布成功——
一旦 LanceDB / FTS 那步失败，那份逐字稿就既不在库里、又不在待入库清单里，摘要 / 长期记忆 /
变更记录全都没跑，而看门狗永远不会重试它（2026-07-26 真实事故，见 `tests/unit/test_ingest_new.py`
的 `TestFailureLeavesNoPhantomIndex`）。反过来最坏只是下次重试一遍，是可恢复的。

### 2. 摘要生成流程

```
ParsedSession
    ↓
[summarize.py] 调用 LLM → 结构化摘要 JSON
    ↓
[update_memory.py] 汇总所有摘要 → LONG_TERM_MEMORY.md
```

**关键模块依赖**：
- `summarize.py` → `llm.py`, `parse.py`
- `update_memory.py` → `llm.py`, `settings.py`, `config`（直接读 SUMMARIES_DIR，不依赖 summarize.py）

### 3. 图谱构建流程

```
ParsedSession
    ↓
[session_graph.py] 抽取单个会话子图 → Fragment JSON
    ↓
[graph_utils.py] 归并所有子图 → 完整图谱
    ↓
[build_graph.py] 协调流程 → graph.json
```

**关键模块依赖**：
- `session_graph.py` → `llm.py`, `parse.py`, `graph_schema_loader.py`
- `graph_utils.py` → `embedder.py`
- `build_graph.py` → `session_graph.py`, `graph_utils.py`

### 4. 问答流程（RAG）

```
用户问题
    ↓
[text_norm.py:to_simplified()] 繁→简归一化（只影响检索，原问题原样进 prompt）
    ↓
[ask.py:retrieve()] 混合检索
    ├─ 向量检索 (LanceDB)
    ├─ FTS 检索（默认 jieba/default 中文分词；繁简仍须先对齐，见 text_norm）
    ├─ 重排序 (reranker.py，输出 0–1 相关性分数)
    ├─ 相关性阈值 (_filter_by_score：< min_score 的丢掉；全不过线才留 min_keep 条保底)
    └─ 窗口扩展（父块，文本取 raw_text = 原文字形）
    ↓
检索片段 + 长期记忆 + 图谱
    ↓
[ask.py:answer()] 组装上下文
    ├─ 加载记忆
    ├─ GraphRAG（图谱引导检索，question 同样先归一化）
    ├─ 上下文压缩
    └─ 调用 LLM
    ↓
答案
```

**关键模块依赖**：
- `ask.py` → `embedder.py`, `llm.py`, `graph_utils.py`, `reranker.py`, `text_norm.py`, `workspace_manager.py`

### 4b. 外部 Agent 檢索（Hermes MCP）

```
Hermes Agent（~/.hermes）
    ↓ MCP stdio
[mcp_rag_search.py] FastMCP tools: search_sessions / get_index_settings / list_workspaces
    ↓
[ask.py:retrieve()]  （與 UI 共用 index_settings.json 真相源）
    ↓
LanceDB workspace（預設 counseling）
```

- **只外露檢索**，不呼叫 `answer()` / LLM。
- **索引參數一直追蹤 UI**：無 MCP 獨立配置；每次 search 重讀 `private.nosync/index_settings.json`，回傳 `settings_used`。
- 依賴 `uv` dependency group `mcp`（`fastmcp`）；主應用不裝也可運行。
- 接線、測試、timeout 見 [HERMES_MCP.md](HERMES_MCP.md)。

#### 简繁归一化（系统不变量）

语料库全是简体（转写工具输出），使用者常打繁体。规则：

| 用途 | 字段 / 数据 | 字形 |
|-----|------------|-----|
| 索引 / 匹配 | `Chunk.text`、retrieve() 的 query、图谱锚点匹配的 question | **一律简体** |
| 展示 / 喂 LLM | `Chunk.raw_text`、prompt 里的使用者问题、逐字稿全文 | **保留原文** |

落点只有三处（见 `docs/API_REFERENCE.md` 的 `to_simplified()`）。ingest 侧那一处对现有简体语料是
幂等 no-op，所以这条不变量是**追加**上去的，不需要重建索引或迁移数据。

#### 相关性分数在数据流里的传递

精排分数不只用于排序，还一路带到 UI，让"这条到底有多相关"全程可见：

```
rerank_score（reranker.py）
    → _filter_by_score() 过滤（min_score / min_keep）
    → hit_score{(file, chunk_index): score}
    → _merge_windows() 取窗口内最高分 → window["score"]
    → retrieve() 打上 window["below_threshold"]
    → _format_retrieved() 写进片段头部「｜相关性 0.183」+ 全低时加一句警告
    → answer() 的 sources[i]["score"] / ["below_threshold"]
    → app.py「引用来源」显示「相关性 0.183（低相关·保底）」
```

`score is None` = **没有分数可比**（关了 reranker，或走心智地图证据日那条内存检索通路），
和"分数低"是两件事，下游一律按"不显示、不判低相关"处理。这条区分是刻意的：让失败/降级
可见，而不是用 0 或 -1 之类的哨兵值混进真实分数里。

---

## 模块依赖图

### 层级结构

```
Layer 0: 基础设施
├─ config.py          # 配置和路径（位于项目根目录，导入用 `from config import ...`）
├─ workspace_manager.py  # Workspace 管理
└─ graph_schema_loader.py  # Schema 加载

Layer 1: 外部服务
├─ embedder.py        # BGE-M3 embeddings
├─ llm.py             # LLM 调用（Gemini/Grok/Hermes）
└─ reranker.py        # BGE 重排序

Layer 2: 数据处理
├─ parse.py           # 逐字稿解析
├─ text_norm.py       # 繁→简归一化（检索层不变量，无项目内依赖）
├─ chunk.py           # 分块
└─ session_resolver.py  # 会话解析

Layer 3: 存储
├─ ingest.py          # 向量库入库
├─ index_records.py   # 索引记录管理
└─ index_settings.py  # 索引配置

Layer 4: 知识提取
├─ summarize.py       # 摘要生成
├─ session_graph.py   # 单会话图谱
├─ graph_utils.py     # 图谱工具
└─ build_graph.py     # 图谱构建

Layer 5: 应用
├─ ask.py             # 问答核心
├─ update_memory.py   # 记忆更新
├─ ingest_new.py      # 增量入库编排（parse→chunk→ingest→summarize→update_memory）
│                     #   + pending_raw_files() / ingest_pending()：待入库判定的单一真相源
├─ context_cache.py   # 缓存管理
└─ mcp_rag_search.py  # Hermes MCP：薄包裝 retrieve()（可選依賴 group mcp / fastmcp）

Layer 5.5: 常驻看门狗（launchd，非 Streamlit 进程）
├─ raw_ingest_watcher.py    # 每 2 分钟扫 raw/，调 ingest_new.pending_raw_files() + ingest_new_file()
└─ chat_memory_watcher.py   # 闲置 30 分钟更新 AI 对话记忆 + 其图谱
   ⚠️ 这两个拿不到 Streamlit 的 session_state，必须由 plist 显式传 --workspace
      （见 scripts/launchd/），否则退到「workspaces/ 下字母序第一个」这种隐式行为

Layer 6: UI
├─ app.py             # Streamlit 主应用（→ ingest_new.pending_raw_files / ingest_pending）
└─ pages/             # Streamlit 页面
```

### 依赖规则

1. **只能向下依赖**：高层模块可以依赖低层，反之不行
2. **Layer 1 独立**：外部服务模块互不依赖
3. **Layer 2 独立**：数据处理模块互不依赖
4. **不允许循环依赖**

---

## 关键接口

### Workspace 隔离机制

所有涉及数据的函数都应接受 `workspace_id: Optional[str]` 参数：

```python
# 数据读写
def some_function(..., workspace_id: Optional[str] = None):
    # 获取 workspace 目录
    ws_dir = get_workspace_dir(workspace_id)
    
    # 使用 workspace 特定路径
    data_dir = RAW_DIR(workspace_id)
    
    # 读写操作...
```

**必须传递 workspace_id 的场景**：
- 文件读写（raw/, db/, summaries/, graph.json 等）
- 向量库操作
- 配置读取（如果有 workspace 级配置）

**可以不传的场景**：
- 纯计算函数（如 `sanitize()`, `_to_int()`）
- 全局配置（如 LLM settings）

### LLM 调用接口

```python
from scripts.llm import ask_llm

# 基本调用（contents 为位置参数，其余均为 keyword-only）
response = ask_llm(
    "Question",                    # contents: str 或多轮 [{"role", "parts"}]
    profile="dialogue",            # 或 "summary"
    system_instruction="You are...",
    cached_content=None,           # Explicit Cache 资源名（可选，仅 Gemini）
)

# response 是 _Response(text, usage_metadata)，有 .text 属性
answer = response.text
```

完整签名：

```python
def ask_llm(
    contents,                              # 位置参数：str 或 [{"role": "user"/"model", "parts": [...]}]
    *,                                     # 以下均为 keyword-only
    profile: str = "dialogue",
    system_instruction: Optional[str] = None,
    response_schema: Optional[dict] = None,  # 传入则强制 JSON 结构化输出
    max_output_tokens: Optional[int] = None,
    model: Optional[str] = None,
    temperature: Optional[float] = None,
    thinking_level: Optional[str] = None,
    cached_content: Optional[str] = None,
): ...
```

**注意**：
- `profile="dialogue"` 使用对话配置（model, temperature 等）
- `profile="summary"` 使用摘要配置（更便宜的模型）
- `cached_content` 用于 Explicit Cache（仅 Gemini；grok 恒为 None）

### 向量检索接口

```python
from scripts.ask import retrieve

results = retrieve(
    query="用户问题",
    k=10,  # 返回 10 个片段
    workspace_id=None,  # None = 当前 workspace；底层连接/数据不做进程级缓存，每次都按此参数重新读取
)

# results: list[dict]
for r in results:
    print(r["text"])         # 片段文本
    print(r["source_file"])  # 来源文件
    print(r["session_date"]) # 日期
    print(r["rank"])         # 排序
```

---

## 数据存储结构

### Workspace 目录结构

```
private.nosync/
├── .env                      # API keys (全局)
├── gemini_settings.json      # LLM 配置 (全局)
├── index_settings.json       # 索引配置 (全局)
└── workspaces/
    └── {workspace_id}/
        ├── .workspace_config.json  # Workspace 配置
        ├── system_instruction.md   # Persona (可选)
        ├── LONG_TERM_MEMORY.md     # 长期记忆总结
        ├── CHAT_MEMORY.md          # 对话记忆总结
        ├── data/
        │   ├── raw/                # 原始逐字稿
        │   ├── processed/          # 处理产物
        │   │   └── chunks.jsonl    # 所有 chunks（入库/统计用）
        │   ├── summaries/          # 摘要 JSON
        │   ├── graph_fragments/    # 图谱片段
        │   ├── graph.json          # 合并后的主图谱
        │   ├── chat_graph.json     # AI 对话图谱
        │   ├── graph_node_embeddings.npz  # 图谱锚点节点 embedding 持久化缓存（按节点哈希增量更新）
        │   ├── index_changelog.jsonl  # 索引变更记录
        │   ├── system_instruction_history.jsonl  # System Instruction 版本历史（见下）
        │   └── chat_sessions/      # 对话会话
        │       └── {session_id}.json
        └── db/
            └── sessions.lance/     # LanceDB 向量库
```

### System Instruction 版本历史

`scripts/ask.py` 的 `save_system_instruction()` 每次保存（内容有变化时）都会向
`system_instruction_history.jsonl`（append-only，workspace 独立）追加一条
`{ts, content, summary}` 记录：`content` 是变更后的完整文本，`summary` 是调用 LLM
（`profile="summary"`）生成的一句话摘要——LLM 调用失败会降级为占位摘要，不阻塞保存
本身。`restore_system_instruction_version()` 恢复到某条历史版本时，同样会追加一条记录
（摘要固定为「已恢复至 {ts} 版本」，不调用 LLM，因为恢复前的内容已经在上一次保存时
进了历史）。所有版本永久保留，不做清理（文本很小、编辑频率低）。

Explicit Cache（`context_cache.py`）按内容做 fingerprint，恢复/保存后内容一变就自动
失效重建，版本历史功能不需要额外处理缓存失效。

### 向量库 Schema

LanceDB 表：`sessions`

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | `str` | chunk 唯一 ID |
| `vector` | `FixedSizeList(1024, float32)` | BGE-M3 dense 向量 |
| `text` | `str` | 片段文本（含上下文前缀，供 FTS/embedding）|
| `raw_text` | `str` | 原始拼接文本（不含前缀）|
| `source_file` | `str` | 来源文件名 |
| `session_date` | `str` | 日期 YYYY-MM-DD |
| `chunk_index` | `int` | 在文件中的索引 |
| `speaker` | `str` | 发言人（逗号分隔，⚠️ 列名单数，来自 Chunk.speakers）|
| `start_ts` | `str` | 起始时间戳 |
| `end_ts` | `str` | 结束时间戳 |
| `prev_chunk_id` | `str` | 前一个 chunk 的 id（无则空串）|
| `next_chunk_id` | `str` | 后一个 chunk 的 id（无则空串）|

**索引**：
- FTS 索引：`text` 字段（默认 jieba/default，可改 ngram；见 index_settings / config.FTS_BASE_TOKENIZER）
- 向量：不建 ANN 索引（数据规模小，直接暴力搜索即可）

### 图谱 Schema

`graph.json` / `chat_graph.json`

```json
{
  "nodes": [
    {
      "id": "unique_id",
      "label": "节点名称",
      "type": "节点类型",  // 来自 graph schema
      "description": "描述",
      "centrality": 0.85,   // 中心性（0-1）
      "related_dates": ["2026-01-01"],  // 相关日期
      "domain": "图式领域"  // 可选
    }
  ],
  "edges": [
    {
      "source": "node_id_1",
      "target": "node_id_2",
      "relation": "关系类型",  // 来自 graph schema
      "evidence": "证据文本",
      "evidence_dates": ["2026-01-01"]  // 证据日期
    }
  ]
}
```

---

## 配置系统

### 三层配置

1. **全局配置**（所有 workspaces 共享）
   - `private.nosync/.env` - API keys
   - `private.nosync/gemini_settings.json` - LLM 参数
   - `private.nosync/index_settings.json` - 索引参数

2. **Workspace 配置**
   - `workspaces/{id}/.workspace_config.json`
   - 包含：name, domain, graph_schema, persona

3. **代码默认值**
   - 各模块中的常量（如 `GRAPH_NODE_MATCH_THRESHOLD`）

### 配置优先级

```
Workspace 配置 > 全局配置 > 代码默认值
```

---

## 并发和状态管理

### Streamlit Session State

UI 状态存储在 `st.session_state`：

| Key | 类型 | 说明 |
|-----|------|------|
| `messages` | `list[dict]` | 当前对话历史 |
| `active_session_id` | `str` | 当前会话 ID |
| `current_workspace` | `str` | 当前 workspace |

### 文件系统并发

- **LanceDB**：支持多读，单写
- **JSON 文件**：无锁，最后写入获胜
- **JSONL 文件**：追加安全

**注意**：
- 不要并发写同一个 JSON 文件
- 使用 `index_changelog.jsonl` 追加日志（并发安全）

---

## 扩展点

### 添加新 Workspace

1. 调用 `create_workspace()`
2. 定义或选择 Graph Schema
3. 上传文档到 `raw/`
4. 运行入库流程

### 添加新 Graph Schema

1. 创建 `scripts/graph_schemas/{name}.json`
2. 定义 `node_types`, `relation_types`
3. 编写 `system_instruction_template`
4. 在 workspace config 中引用

### 添加新 LLM Provider

1. 在 `scripts/llm.py` 添加新 provider 逻辑（OpenAI 兼容网关只需在 `_OPENAI_PROVIDERS` 注册表加一行）
2. 更新 `scripts/settings.py` 的 `VALID_PROVIDERS` 支持新 provider 配置
3. UI 无需改动：`gemini_settings_dialog()` 的 provider 选项直接读 `settings.VALID_PROVIDERS`

### 停用 / 恢复某个 Provider

provider 的可选集合只有一个真相来源：`scripts/settings.py`。

- `VALID_PROVIDERS` = 可选后端；`DISABLED_PROVIDERS` = {已停用 provider: 原因}（两者不相交）。
- `settings.provider()` 读到停用值（或非法值）时回退 `DEFAULT_PROVIDER`，`settings.save()` 拒绝把
  停用值写进设置文件 → 停用的 provider 在真实运行路径上进不去，对应的调用代码可以原样保留。
- `load_for_ui()` 把 `disabled_providers` 一并返回，UI 在 provider 选区显示停用原因（避免选项凭空消失）。
- 停用某个后端时，`config.py` 里的默认模型名要一起换成仍启用的后端吃得下的值——否则"删掉设置文件 =
  恢复默认"会把不可用的模型名塞回去。

**当前状态**：`gemini` 仍停用（产品选择；原 Python 3.9 / google-genai 1.x 的 `thinking_level`
blocker 已在 3.12 + google-genai 2.x 下解除；恢复步骤见 README §七「gemini 为什么停用」）。
`scripts/llm.py` 的 `_ask_gemini()` 与 `scripts/context_cache.py` 的 Explicit Caching 因此处于
休眠状态（代码保留、单元测试仍覆盖）。

---

## 性能考虑

### 瓶颈点

1. **Embedding 调用**
   - BGE-M3 模型加载：~2GB 内存
   - 单次 embedding：~50-100ms
   - 优化：批量调用

2. **LLM 调用**
   - 500ms - 5s（取决于 thinking_level / reasoning_effort）
   - 优化：Explicit Cache（仅 gemini；当前停用，改由 OpenAI 兼容后端的隐式 prompt cache 承担）

3. **向量检索**
   - LanceDB 查询：10-100ms（取决于数据量）
   - 优化：调整 `retrieve_top_k` 和 `final_top_k`

4. **图谱归并**
   - `resolve_graph()`：O(n²) 节点相似度计算
   - 优化：分批处理，限制节点数

### 缓存策略

- **Explicit Cache**（Gemini）：长期记忆、System Instruction
- **Session State**（Streamlit）：对话历史、当前 workspace
- **磁盘持久化（按 workspace，按内容哈希增量失效）**：图谱锚点节点 embedding
  （`GRAPH_NODE_EMBEDDINGS_PATH`，见 `find_relevant_graph_nodes()`）

**2026-07-30 移除的反模式**：`scripts/ask.py` 曾经用 module-level 全局变量缓存
LanceDB table 连接、全量 chunk DataFrame、合并后图谱——这三者都不区分 workspace，
Streamlit 是单一常驻 process，切换 workspace 不会清空它们，导致"process 里查过一次
A workspace 后，即便 UI 切到 B workspace，检索/图谱一直沿用 A 的数据"（真实事故：
在「八字紫微」workspace 问问题却撈到「心理咨询」workspace 的 chunks）。修复方式是
直接砍掉这三处缓存——本地连接/小 JSON 的重新读取是 ms 级成本，不值得为了这点性能
换来"必须记得在切换 workspace 时清缓存"这种隐性耦合。唯一保留的是上面这条磁盘持久化
（要过 embedding 模型，重算成本是秒级、真的值得缓存），且天然按 workspace 分文件，
不需要额外的失效钩子。

---

## 错误处理

### 分层错误处理

1. **底层（embedder, llm）**：抛出异常
2. **中层（retrieve, summarize）**：捕获并降级
3. **顶层（answer, UI）**：友好错误信息

### 常见错误

| 错误 | 原因 | 处理 |
|------|------|------|
| `FileNotFoundError` | 文件缺失 | 返回默认值或空字符串 |
| `JSONDecodeError` | JSON 损坏 | 返回 None，记录警告 |
| `LLM API Error` | API 调用失败 | 重试或返回错误信息 |
| `Embedding Error` | 模型加载失败 | 致命错误，需要修复环境 |

---

## 测试策略

### 测试金字塔

```
     /\
    /E2E\         集成测试（慢，少量）
   /------\       - 完整流程测试
  /Unit    \      单元测试（快，大量）
 /----------\     - 函数级别测试
/Static Check\    静态检查（最快，自动）
--------------    - check_code_patterns.py
```

### Mock 策略

| 依赖 | Mock 方式 |
|------|----------|
| LLM 调用 | `@patch('scripts.llm.ask_llm')` |
| Embeddings | `@patch('scripts.embedder.embed_one')` |
| LanceDB | `@patch('scripts.ask._get_table')` |
| 文件系统 | `tmp_path` fixture |
| Streamlit | `@patch('streamlit.xxx')` |

---

## 迁移和升级

### Workspace 迁移

从旧项目（`心理咨詢agent`）迁移到新项目的 workspace：

```bash
python scripts/migrate_from_old_project.py
```

**注意**：脚本不接受 CLI 参数——源路径、目标路径、workspace 名称
（`counseling`）均在 `migrate_from_old_project.py` 顶部常量中硬编码，
如需改动请直接编辑该文件。

### 配置升级

检查配置版本：
```python
config = load_workspace_config(workspace_id)
if "version" not in config:
    # 升级到新版本
    config["version"] = "2.0"
```

---

## 调试技巧

### 查看数据流

```python
# 1. 查看 chunks
from scripts.chunk import CHUNKS_JSONL_PATH
chunks = [json.loads(line) for line in open(CHUNKS_JSONL_PATH()).readlines()]
print(f"Total chunks: {len(chunks)}")

# 2. 查看向量库
import lancedb
db = lancedb.connect(str(DB_DIR()))
table = db.open_table("sessions")
print(f"Total records: {table.count_rows()}")

# 3. 查看图谱
graph = json.loads(GRAPH_JSON_PATH().read_text())
print(f"Nodes: {len(graph['nodes'])}, Edges: {len(graph['edges'])}")
```

### 日志级别

```python
import logging
logging.basicConfig(level=logging.DEBUG)  # 查看详细日志
```

---

## 未来架构改进

### 计划中

1. **向量化任务队列**
   - 后台批量 embedding
   - 避免阻塞 UI

2. **增量图谱更新**
   - 不重新构建整个图谱
   - 只更新新增节点

3. **分布式向量库**
   - 支持更大数据量
   - 多机部署

4. **实时监听**
   - 文件变更自动入库
   - 热重载配置

### 不计划做

- ❌ 多用户（单人本地使用）
- ❌ 云部署（隐私优先）
- ❌ 移动端（Web 端够用）

---

## 参考

- [API_REFERENCE.md](./API_REFERENCE.md) - API 规范
- [DEVELOPMENT_GUIDE.md](./DEVELOPMENT_GUIDE.md) - 开发指南
- [TESTING_STRATEGY.md](./TESTING_STRATEGY.md) - 测试策略
