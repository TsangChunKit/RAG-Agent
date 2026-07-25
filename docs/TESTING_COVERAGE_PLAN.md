# 测试覆盖率提升计划

> ⚠️ 本文档为最初的提升计划。数据已更新到最新实测值（2026-07-25 第二次，
> `pytest tests/unit/ --cov=scripts --cov=app`）。最新逐文件覆盖率以 `COVERAGE_BOOST_ROUND4.md` 为准。

## 当前状态

### 整体覆盖率：73% 🟢

```
目标：70% → 85%
当前：72.72%（scripts + app.py，734 个单元测试）
状态：已越过最低目标 70%，距推荐值 85% 还差 12%
```

> ✅ pre-commit hook 的第 4 项检查（`--cov-fail-under=70`）**已能通过**（2026-07-25 起）。
> 清掉它靠的正是下面 P2/P3 那批 0% 的批处理脚本（update_memory / ingest_new / index_records /
> 两个 watcher / check_code_patterns），不是继续加核心模块的测试——见 `COVERAGE_BOOST_ROUND4.md`。
> 剩下的 0%（`auto_fix.py`、`migrate_from_old_project.py`）是一次性工具脚本，优先级最低。

### 文件级别覆盖率

| 文件 | 行数 | 当前覆盖率 | 目标 | 优先级 | 状态 |
|------|------|-----------|------|--------|------|
| **核心业务逻辑** ||||
| ask.py | 607 | 79% | 80% | P0 | 🟢 接近 |
| graph_utils.py | 99 | 100% | 80% | P0 | ✅ 达标 |
| session_graph.py | 76 | 78% | 80% | P0 | 🟢 接近 |
| chunk.py | 134 | 90% | 80% | P0 | ✅ 达标 |
| ingest.py | 39 | 85% | 80% | P0 | ✅ 达标 |
| reranker.py | 34 | 100% | 80% | P0 | ✅ 达标 |
| build_graph.py | 24 | 54% | 80% | P0 | 🟡 需要 |
| **配置/工具类** ||||
| text_norm.py | 12 | 83% | 80% | P1 | ✅ 达标（未覆盖的 2 行是 zhconv 缺失分支，装了包就跑不到）|
| graph_schema_loader.py | 73 | 71% | 80% | P1 | 🟢 接近 |
| parse.py | 80 | 88% | 80% | P1 | ✅ 达标 |
| index_settings.py | 46 | 100% | 80% | P1 | ✅ 达标 |
| index_records.py | 49 | 100% | 60% | P1 | ✅ 达标 |
| workspace_manager.py | 142 | 80% | 80% | P1 | ✅ 达标 |
| settings.py | 67 | 100% | 60% | P1 | ✅ 达标 |
| **批处理脚本** ||||
| update_memory.py | 35 | 89% | 40% | P2 | ✅ 达标（未覆盖的只有 `__main__`）|
| update_chat_memory.py | 51 | 92% | 40% | P2 | ✅ 达标（同上）|
| ingest_new.py | 69 | 83% | 40% | P2 | ✅ 达标（同上）|
| chat_memory_watcher.py | 62 | 82% | 40% | P2 | ✅ 达标（未覆盖的是常驻 while 循环）|
| raw_ingest_watcher.py | 59 | 80% | 40% | P2 | ✅ 达标（同上）|
| **工具脚本（低优先级）** ||||
| check_code_patterns.py | 90 | 99% | - | P3 | ✅ 达标（它是 hook 第一道闸门，自己必须可信）|
| auto_fix.py | 79 | 0% | - | P3 | ⚪ 可选（一次性修复脚本）|
| migrate_from_old_project.py | 120 | 0% | - | P3 | ⚪ 可选（一次性迁移脚本，跑过就不再用）|

> **`__main__` 块不测**：批处理脚本/看门狗的 `if __name__ == "__main__":`（含 `while True` +
> `time.sleep`）是进程入口，测它等于测 launchd。所以这几个文件的实际上限就在 80–92%，
> 逻辑部分已全覆盖。

---

## 为什么覆盖率这么低？

### 根本原因

1. **历史债务**
   - 项目初期没有测试文化
   - "先实现功能，后补测试"（实际从未补）
   - 累积了 2265 行代码，只有 509 行被测试

2. **复杂度高**
   - ask.py 532 行（检索+LLM+压缩+GraphRAG）
   - 事后补测试难度极大
   - 需要大量 mock 和 fixture

3. **缺乏强制机制**
   - 之前没有 pre-commit hook
   - 没有 CI 覆盖率检查
   - 开发者可以跳过测试

### 影响

```
未测试代码 = 1756 行 = 78%

这意味着：
- 78% 的代码可能有 bug
- 78% 的代码改动风险极高
- 78% 的功能没有回归保护
```

---

## 提升计划

### Phase 1: 紧急修复（1 周）

**目标：核心业务逻辑达到 60%**

#### 任务 1: ask.py (13% → 60%)

**需要新增测试**：
- `test_retrieve_basic()` - 基本检索
- `test_retrieve_with_graph()` - GraphRAG 引导检索
- `test_retrieve_with_reranker()` - 重排序
- `test_answer_basic()` - 基本问答
- `test_answer_with_history()` - 带历史的问答
- `test_answer_context_compression()` - 上下文压缩
- `test_answer_empty_db()` - 空数据库处理
- `test_answer_llm_failure()` - LLM 失败处理

**预计工作量**：2-3 天

#### 任务 2: graph_utils.py (9% → 60%)

**需要新增测试**：
- `test_resolve_graph_basic()` - 基本归并
- `test_resolve_graph_dedup_nodes()` - 节点去重
- `test_resolve_graph_merge_edges()` - 边合并
- `test_resolve_graph_calculate_centrality()` - 中心性计算
- `test_merge_graphs()` - 多图合并

**预计工作量**：1 天

#### 任务 3: chunk.py (35% → 60%)

**需要新增测试**：
- `test_chunk_session_sliding_window()` - 滑动窗口
- `test_chunk_session_parent_expansion()` - 父块扩展
- `test_contextual_prefix()` - 上下文前缀生成
- `test_chunk_large_session()` - 大文件分块

**预计工作量**：1 天

### Phase 2: 全面覆盖（2 周）

**目标：整体覆盖率达到 70%**

#### 任务 4-6: 其他核心文件

- ingest.py (32% → 80%)
- build_graph.py (33% → 80%)
- session_graph.py (21% → 80%)

**预计工作量**：3-4 天

#### 任务 7-10: 配置/工具类

- parse.py, index_settings.py, workspace_manager.py, settings.py

**预计工作量**：2-3 天

#### 任务 11-13: 批处理脚本 ✅ 已完成（2026-07-25）

- update_memory.py (0% → 89%)、update_chat_memory.py (43% → 92%)、ingest_new.py (0% → 83%)
- 附带完成：index_records.py (20% → 100%)、chat_memory_watcher.py (0% → 82%)、
  raw_ingest_watcher.py (0% → 80%)、check_code_patterns.py (0% → 99%)
- 结果：整体 61% → 73%，pre-commit hook 的覆盖率闸门首次通过。详见 `COVERAGE_BOOST_ROUND4.md`

**实际工作量**：约 0.5 天（7 个测试文件，126 个新测试：597 → 723）

#### 任务 14: 修复集成测试腐化（2026-07-25 新增，优先级 P0）

`pytest tests/integration/ --integration` 当前 **33 个失败（2026-07-25 实测 33 failed / 39 passed；数量随上一轮残留的 workspace 浮动），
与被测代码无关**，两类原因：

1. **测试写死了已改名/已改签名的 API** —— 例如 `index_settings.load()`（不存在）、
   `parse_transcript(str)`（实参必须是 `Path`）。属于测试没跟上重构。
2. **测试污染真实数据目录** —— `test_full_workflow.py` 等直接调 `create_workspace("ws1", ...)`，
   建在真实的 `private.nosync/workspaces/` 下。上一轮跑完不清理，下一轮就撞
   `ValueError: Workspace already exists: ws1`；`test-workspace/` 甚至已被误提交进 git。
   修法：fixture 里 monkeypatch `WORKSPACES_ROOT` 到 `tmp_path`，一次性切断。

**做完之前，不要把整体覆盖率的锅算在集成测试头上**——它们现在跑不完，覆盖率数据只反映单元测试。

**预计工作量**：0.5-1 天

### Phase 3: 卓越品质（持续）

**目标：整体覆盖率达到 85%**

- 提升所有 P0 文件到 90%
- 补充集成测试
- 添加性能测试
- 完善边缘情况测试

---

## 测试策略

### 1. Mock 策略

```python
# Mock LLM 调用
@patch('scripts.llm.ask_llm')
def test_answer(mock_llm):
    mock_llm.return_value = "Mocked response"
    result = answer("test question")
    assert result["answer"] == "Mocked response"

# Mock Embeddings
@patch('scripts.embedder.embed_one')
def test_retrieve(mock_embed):
    mock_embed.return_value = [0.1] * 768
    results = retrieve("test query")
    assert len(results) > 0
```

### 2. Fixture 策略

```python
# 共享测试数据
@pytest.fixture
def sample_session():
    return ParsedSession(
        source_file="test.txt",
        session_date="2026-01-01",
        turns=[...]
    )

# 隔离环境
@pytest.fixture
def isolated_workspace(tmp_path):
    workspace = tmp_path / "test_workspace"
    workspace.mkdir()
    yield workspace
    # pytest 自动清理
```

### 3. 参数化测试

```python
@pytest.mark.parametrize("input,expected", [
    ("normal input", "normal output"),
    ("edge case", "edge output"),
    ("error input", None),
])
def test_function(input, expected):
    result = function(input)
    assert result == expected
```

---

## 强制执行机制

### Pre-commit Hook

**已安装**：`.git/hooks/pre-commit`

**检查项目**：
1. 静态代码检查
2. 导入测试
3. 单元测试
4. 覆盖率检查（≥ 70%）

**失败 → commit 被拒绝**

### CI/CD（待实施）

```yaml
# .github/workflows/test.yml
name: Tests
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - name: Run tests
        run: |
          pytest tests/ --cov=scripts --cov-fail-under=70
      - name: Upload coverage
        uses: codecov/codecov-action@v3
```

---

## 监控指标

### 每周目标

| 周 | 目标覆盖率 | P0 文件平均 | 关键里程碑 |
|----|-----------|------------|-----------|
| Week 1 | 35% | 40% | ask.py 基础测试完成 |
| Week 2 | 50% | 60% | 所有 P0 文件 ≥ 60% |
| Week 3 | 65% | 75% | P1 文件开始 |
| Week 4 | 70% | 80% | Phase 2 完成 |

### 质量门槛

**绝不允许**：
- ❌ 新增代码没有测试
- ❌ 覆盖率下降
- ❌ 跳过 pre-commit hook（除非紧急）

**强制要求**：
- ✅ 新功能必须 ≥ 80% 覆盖率
- ✅ Bug 修复必须有回归测试
- ✅ 重构必须保持或提升覆盖率

---

## 成功案例

### 已完成的高覆盖率文件

1. **graph_schema_loader.py: 71%** ✅
   - 完整的 schema 加载测试
   - 降级行为测试
   - 错误处理测试

2. **parse.py: 60%** ✅
   - 基本解析测试
   - 边缘情况测试
   - 文件格式验证

### 经验教训

**什么有效**：
- ✅ 小步迭代（一次一个函数）
- ✅ Mock 外部依赖（LLM, Embeddings）
- ✅ 参数化测试（减少重复代码）
- ✅ Fixture 共享（提高效率）

**什么无效**：
- ❌ 试图一次测试整个模块
- ❌ 跳过边缘情况
- ❌ 忽略错误处理
- ❌ 测试实现细节而非行为

---

## 快速参考

### 查看覆盖率

```bash
# 整体覆盖率
pytest --cov=scripts --cov-report=term-missing

# HTML 报告
pytest --cov=scripts --cov-report=html
open htmlcov/index.html

# 特定文件
pytest tests/unit/test_ask.py --cov=scripts.ask --cov-report=term-missing
```

### 运行测试

```bash
# 所有测试
pytest tests/

# 单元测试
pytest tests/unit/

# 集成测试
pytest tests/integration/ --integration

# 特定测试
pytest tests/unit/test_ask.py::test_answer_basic -v
```

### 修复失败测试

```bash
# 详细错误信息
pytest tests/unit/test_ask.py -vv

# 停在第一个失败
pytest tests/unit/test_ask.py -x

# 查看 print 输出
pytest tests/unit/test_ask.py -s
```

---

## 联系与支持

**问题**：覆盖率提升遇到困难？

**资源**：
- 查看 `docs/TESTING_STRATEGY.md` - 完整测试策略
- 查看 `CLAUDE.md` - 开发规则
- 查看现有测试作为参考（`tests/unit/test_graph_schema_loader.py`）

**原则**：
> 测试不是负担，而是投资。
> 前期多花 20 分钟写测试，后期节省无数小时调试。
