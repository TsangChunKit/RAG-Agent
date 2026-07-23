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
            "answer": str,              # LLM 回答
            "retrieved_count": int,     # 检索到的片段数
            "graph_nodes_matched": int, # 匹配的图谱节点数
            "thinking_content": str,    # 思考过程（如果有）
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
    混合检索（向量 + FTS + 重排序）。
    
    Args:
        query: 查询文本
        k: 返回结果数量
        workspace_id: Workspace ID
    
    Returns:
        [
            {
                "text": str,           # 片段文本
                "source_file": str,    # 来源文件
                "session_date": str,   # 日期 YYYY-MM-DD
                "rank": int,           # 排序（越小越相关）
                "score": float,        # 相关性分数
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
    3. "_legacy" (兼容模式)
    
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
    graph_schema_mode: str,
    schema_file: Optional[str] = None
) -> str:
    """
    创建新 workspace。
    
    Args:
        name: Workspace ID（slug 格式，如 "my-notes"）
        display_name: 显示名称（如 "我的笔记"）
        domain: 领域（"counseling", "generic", "sutras" 等）
        graph_schema_mode: Schema 模式（"predefined", "generic", "custom"）
        schema_file: Schema 文件名（mode="predefined" 时必需）
    
    Returns:
        创建的 workspace ID
    
    Raises:
        ValueError: Workspace 已存在
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
                "domain": str,        # 领域
                "created_at": str,    # 创建时间 ISO 8601
            }
        ]
    """
```

---

## scripts/chunk.py

### 核心类型

#### `Chunk` (dataclass)
```python
@dataclass
class Chunk:
    text: str              # 片段文本
    chunk_index: int       # 在文件中的索引（从 0 开始）
    source_file: str       # 来源文件名
    session_date: str      # 会话日期 YYYY-MM-DD
    speakers: str          # 发言人（逗号分隔）
    start_ts: str         # 起始时间戳
    end_ts: str           # 结束时间戳
```

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

## scripts/parse.py

### 核心类型

#### `Utterance` (dataclass)
```python
@dataclass
class Utterance:
    timestamp: str    # 时间戳（如 "00:15:30"）
    speaker: str      # 发言人
    text: str         # 发言内容
    line_no: int      # 行号
```

#### `ParsedSession` (dataclass)
```python
@dataclass
class ParsedSession:
    source_file: str        # 文件名
    session_date: str       # 日期 YYYY-MM-DD
    file_datetime: str      # 文件名中的时间戳
    utterances: list[Utterance]  # 发言列表
```

### 核心函数

#### `parse_transcript()`
```python
def parse_transcript(file_path: str) -> ParsedSession:
    """
    解析逐字稿文件。
    
    Args:
        file_path: 文件路径（文件名必须包含日期）
    
    Returns:
        ParsedSession 对象
    
    Raises:
        ValueError: 文件名格式不正确
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
