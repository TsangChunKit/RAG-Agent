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
    k: Optional[int] = None
) -> list[dict]:
    """
    混合检索（稠密语义 + ngram 关键词，RRF 融合）→ 可选 cross-encoder 精排 → 父块/窗口扩展。

    注意：本函数没有 workspace_id 参数，使用当前 workspace（由 index_settings 决定）。
    注意：query 进来后先过 text_norm.to_simplified()（繁→简），embed / FTS / reranker
          三条腿共用归一化后的文本。索引侧的 Chunk.text 也是简体，两边对齐。
    
    Args:
        query: 查询文本
        k: 返回结果数量（None = 使用「⚙️ 索引设置」当前值）
    
    Returns:
        [
            {
                "source_file": str,          # 来源文件
                "session_date": str,         # 日期 YYYY-MM-DD
                "start_ts": str,             # 窗口起始时间戳
                "end_ts": str,               # 窗口结束时间戳
                "chunk_index_range": tuple,  # (起始 chunk_index, 结束 chunk_index)
                "text": str,                 # 片段文本（父块/窗口扩展后）
                "rank": int,                 # 排序（越小越相关）
            }
        ]
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
| `extract_mentioned_dates(question: str)` | 提取提到的日期 | `list[str]` |
| `find_relevant_graph_nodes(question, graph, top_k)` | GraphRAG 节点匹配 | `list[dict]` |

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

**为什么必须做**：ngram FTS 靠字符重叠，「水煙」和「水烟」零重叠；dense 也会漏。实测繁体
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

**分数量级参考**（实测本项目语料，chunk 是长段落对话，绝对分数偏低）：

| query | top1 rerank_score |
|------|------------------|
| 「我喜歡抽水煙」（相关） | 0.18 |
| 「抽水烟排解焦虑」（相关） | 0.047 |
| 「今天天氣不錯我想吃拉麵」（完全无关） | 0.0032 |

> 相关/无关差约两个数量级，但**绝对值都远低于 0.5**——将来加相关性阈值要按 0.01 量级定标，
> 不要照搬通用的 0.5。

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

## scripts/settings.py

运行期可调参数 + LLM 后端选择的读写口。每次调用都重读 `GEMINI_SETTINGS_PATH`，改完下一次调用即生效。

### Provider 常量

```python
VALID_PROVIDERS = ("grok", "hermes")   # 可选后端（UI 选项直接读这个，不要在 app.py 里写死）
DEFAULT_PROVIDER = "hermes"            # 非法/缺失/已停用时的回退值，必须 ∈ VALID_PROVIDERS
DISABLED_PROVIDERS = {"gemini": "……停用原因（UI 会显示）"}  # 与 VALID_PROVIDERS 不相交
```

> `gemini` 当前停用：Python 3.9 能装的最高版 `google-genai`（1.47.0）的 `ThinkingConfig` 不支持
> `thinking_level`。原因与恢复步骤见 README §七「gemini 为什么停用」。

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

## 类型注解规范（Python 3.9 兼容）

### ❌ 禁止使用

```python
# PEP 604 语法（Python 3.10+）
def func() -> dict | None:  # ❌ 不兼容 Python 3.9
def func() -> str | int:    # ❌ 不兼容 Python 3.9
```

### ✅ 必须使用

```python
from typing import Optional, Union

def func() -> Optional[dict]:  # ✅ 兼容 Python 3.9
def func() -> Union[str, int]: # ✅ 兼容 Python 3.9
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

### 2. TypeError: unsupported operand type(s) for |

```python
# ❌ 错误
def func() -> dict | None:

# ✅ 正确
def func() -> Optional[dict]:
```

### 3. NameError: name 'Optional' is not defined

```python
# ❌ 错误：导入在 docstring 内
"""Module docstring.
from typing import Optional
"""

# ✅ 正确：导入在 docstring 外
"""Module docstring."""
from typing import Optional
```

### 4. 缺少 workspace_id 参数

```python
# ⚠️ 警告：在 UI 代码中应该传递
build_graph()  # 使用默认 workspace

# ✅ 推荐：明确传递
build_graph(workspace_id=current_workspace)
```

### 5. ThinkingConfig: thinking_level Extra inputs are not permitted

```
1 validation error for ThinkingConfig
thinking_level  Extra inputs are not permitted [type=extra_forbidden, input_value='high']
```

**原因**：Gemini 3.x 用 `thinking_level` 取代了 `thinking_budget`，但 Python 3.9 能装的最高版
`google-genai`（1.47.0）的 `ThinkingConfig` 只有 `include_thoughts` / `thinking_budget` 两个字段；
支持 `thinking_level` 的 `google-genai` 2.x 要求 Python ≥ 3.10，所以 `pip install -U` 也拿不到。

**现状**：`gemini` provider 已停用（`settings.DISABLED_PROVIDERS`），默认走 hermes，
不会再触发这个错误。要用回 Gemini 得先升 Python ≥ 3.10 + google-genai 2.x，
恢复步骤见 README §七「gemini 为什么停用」。

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
- [ ] 使用 `Optional[X]` 而非 `X | None`
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
