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
- `chunk.py` → `parse.py`, `workspace_manager.py`
- `embedder.py` → BGE-M3 模型
- `ingest.py` → `chunk.py`, `embedder.py`, LanceDB

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
[ask.py:retrieve()] 混合检索
    ├─ 向量检索 (LanceDB)
    ├─ FTS 检索
    ├─ 重排序 (reranker.py)
    └─ 窗口扩展（父块）
    ↓
检索片段 + 长期记忆 + 图谱
    ↓
[ask.py:answer()] 组装上下文
    ├─ 加载记忆
    ├─ GraphRAG（图谱引导检索）
    ├─ 上下文压缩
    └─ 调用 LLM
    ↓
答案
```

**关键模块依赖**：
- `ask.py` → `embedder.py`, `llm.py`, `graph_utils.py`, `reranker.py`, `workspace_manager.py`

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
└─ context_cache.py   # 缓存管理

Layer 6: UI
├─ app.py             # Streamlit 主应用
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
    k=10,  # 返回 10 个片段（无 workspace_id 参数，使用当前 workspace）
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
        │   ├── index_changelog.jsonl  # 索引变更记录
        │   └── chat_sessions/      # 对话会话
        │       └── {session_id}.json
        └── db/
            └── sessions.lance/     # LanceDB 向量库
```

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
- FTS 索引：`text` 字段（ngram tokenizer，见 index_settings）
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

1. 在 `scripts/llm.py` 添加新 provider 逻辑
2. 更新 `scripts/settings.py` 支持新 provider 配置
3. 在 UI `gemini_settings_dialog()` 添加配置选项

---

## 性能考虑

### 瓶颈点

1. **Embedding 调用**
   - BGE-M3 模型加载：~2GB 内存
   - 单次 embedding：~50-100ms
   - 优化：批量调用

2. **LLM 调用**
   - Gemini API：500ms - 5s（取决于 thinking_level）
   - 优化：使用 Explicit Cache

3. **向量检索**
   - LanceDB 查询：10-100ms（取决于数据量）
   - 优化：调整 `retrieve_top_k` 和 `final_top_k`

4. **图谱归并**
   - `resolve_graph()`：O(n²) 节点相似度计算
   - 优化：分批处理，限制节点数

### 缓存策略

- **Explicit Cache**（Gemini）：长期记忆、System Instruction
- **Session State**（Streamlit）：对话历史、当前 workspace
- **全局变量**：LanceDB table 连接、已加载的 embedder

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
