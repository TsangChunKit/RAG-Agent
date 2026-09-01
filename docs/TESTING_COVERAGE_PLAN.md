# 测试覆盖率提升计划

> ⚠️ 本文档为最初的提升计划。总体数据已更新到最新实测值（2026-09-01，
> `pytest tests/unit/ --cov=scripts --cov=app`）。最新逐文件覆盖率以 `COVERAGE_BOOST_ROUND4.md` 为准。

## 当前状态

### 整体覆盖率：71% 🟢

```
目标：70% → 85%
当前：71.60%（scripts + app.py，794 个单元测试通过，1 个跳过）
状态：已越过最低目标 70%，距推荐值 85% 还差 13.40%
```

> 📉 **为什么从 72.7% 掉到 71.5%（2026-07-26）**：这一轮给 `tests/conftest.py` 加了三道 autouse
> 硬隔离闸门（钉住 `WORKSPACES_ROOT` / 堵死真实 LLM 调用 / 清模块级缓存）。隔离生效后
> `import app` 走的是"干净 tmp workspace"那条路，`app.py` 的一部分模块级代码不再被顺带执行，
> 于是 app.py 从 31% 掉到 25%。这是**用 1.2 个覆盖率点换掉「测试会污染真实 private.nosync/、
> 会打真实 API」这个更严重的问题**——覆盖率是代理指标，测试可信度是真指标，冲突时选后者。

> ✅ pre-commit hook 的覆盖率检查（`--cov-fail-under=70`）**已能通过**（2026-07-25 起）。
> （2026-07-30 起该检查与单元测试合并为 pre-commit hook 的第 2 项，不再是独立的第 4 项——
> hook 从 4 步简化为 2 步，命令本身没变）
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
| workspace_manager.py | 142 | 75% | 80% | P1 | 🟢 接近 |
| settings.py | 67 | 100% | 60% | P1 | ✅ 达标 |
| **批处理脚本** ||||
| update_memory.py | 35 | 89% | 40% | P2 | ✅ 达标（未覆盖的只有 `__main__`）|
| update_chat_memory.py | 51 | 92% | 40% | P2 | ✅ 达标（同上）|
| ingest_new.py | 114 | 89% | 40% | P2 | ✅ 达标（2026-08-08 新增 `missing_summary_files()` / `regenerate_missing_summaries()`，供「🔁 补生成摘要」按钮用，未覆盖的仍只有 `__main__`）|
| chat_memory_watcher.py | 62 | 82% | 40% | P2 | ✅ 达标（未覆盖的是常驻 while 循环）|
| raw_ingest_watcher.py | 36 | 67% | 40% | P2 | ✅ 达标（未覆盖的 12 行全是 `__main__` 常驻循环；文件从 59 行缩到 36 行，是因为待入库判定挪进了 ingest_new.py）|
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

#### 任务 14: 修复集成测试腐化（2026-07-25 新增，优先级 P0）—— ✅ 已完成（2026-07-26）

`pytest tests/integration/ --integration` 原本 **33 个失败，与被测代码无关**，两类原因：

1. **测试写死了已改名/已改签名的 API** —— 例如 `index_settings.load()`（不存在）、
   `parse_transcript(str)`（实参必须是 `Path`）、`ingest(chunks=list[Chunk], rebuild=True)`
   （实际吃 `list[dict]` + `mode=`）。属于测试没跟上重构。
2. **测试污染真实数据目录** —— `test_full_workflow.py` 等直接调 `create_workspace("ws1", ...)`，
   建在真实的 `private.nosync/workspaces/` 下。上一轮跑完不清理，下一轮就撞
   `ValueError: Workspace already exists: ws1`；`test-workspace/` 甚至已被误提交进 git。

**最终结果：73 passed / 0 failed**（历程 33 failed → 18 failed → 全绿；全套
`pytest tests/ --integration` = 821 passed）。**一个测试都没删** —— 每个原有测试名和它的意图
都保留，只把断言重写成对着真实 API。

第 2 类的根治：`tests/conftest.py` 加了三道 **autouse** 闸门，任何测试（不只是集成测试）都不可能再污染真实数据或出网：

| 闸门 | 钉住什么 | 为什么必须在 conftest 做 |
|-----|---------|----------------------|
| `isolate_data_root` | `workspace_manager.WORKSPACES_ROOT` / `PRIVATE_DIR`、`INDEX_SETTINGS_PATH`、`GEMINI_SETTINGS_PATH`、`ENV_PATH`、`CURRENT_WORKSPACE` 环境变量、`st.session_state` | `WORKSPACES_ROOT` 是 **import-time 求值的模块常量**，测试里 patch `PRIVATE_DIR` 对它完全无效——这正是 13 个"以为自己隔离了"的测试实际在写真实目录的原因 |
| `block_real_llm_calls` | `genai.Client` / `openai.OpenAI` 构造函数 + `llm` 的模块级 client 缓存 | 测试 patch 的是 `scripts.llm.ask_llm`，但调用方全是 `from scripts.llm import ask_llm`（名字已绑进各自 namespace），patch 源模块拦不住任何东西 |
| `reset_module_caches`（2026-07-30 已移除，见下） | `ask._table` / `ask._all_chunks_cache` | 模块级单例会把上一个测试的 tmp_path 表泄漏给下一个 |

> 2026-07-30 更新：这道闸门在修复「跨 workspace 快取污染」的生产 bug 时被移除——根治办法是
> 直接删掉 `ask._table` / `ask._all_chunks_cache` 这两个模块级单例本身（`_get_table()` /
> `_load_all_chunks()` 改成每次都按传入的 `workspace_id` 重新读取），而不是继续「测试记得清、
> 生产环境自求多福」。没有全局状态就没有跨测试泄漏，这道闸门因此变得不再必要，`tests/conftest.py`
> 现在只剩两道闸门。详见 [ARCHITECTURE.md](./ARCHITECTURE.md) 的「缓存策略」与
> [TESTING_STRATEGY.md](./TESTING_STRATEGY.md) 的「测试隔离的两道闸门」。

外加 `tests/integration/conftest.py` 提供三个替身：`deterministic_embed`（crc32 当种子：同文本
同向量、不同文本近正交，"该合并的节点必然合并"于是是可断言的事实而不是概率，也不用加载 2GB 的
BGE-M3）、`fake_resp`（LLM 响应工厂）、`no_reranker`（走 `index_settings.save()` 正式通路关掉精排）。
详见 [TESTING_STRATEGY.md](./TESTING_STRATEGY.md#集成测试专用-fixtures-testsintegrationconftestpy)。

第 1 类（API 签名过时）的修法是把 `test_full_pipeline.py` / `test_full_workflow.py` /
`test_edge_cases.py` / `test_ui_features.py` **逐个测试重写**，对着代码而不是对着记忆写断言。
过程中发现的一批"测试幻想出来的 API"，同时也是给文档的校正清单：

| 测试以为的 | 实际的 |
|-----------|-------|
| `index_settings.load()` / `.update(chunk_size=...)` | 只有六个分组读函数 + `load_for_ui()` / `save(六组全给)` / `reset()`；**刻意不做校验**，边界靠 UI 的 `st.number_input(..., 100, 2000, ...)` 兜 |
| `merge_graphs([g1, g2])` | `merge_graphs(therapy_graph, chat_graph)`——合并的是两个固定角色，所以能自动打 `source` 标签；任一边为 `None` 时原样返回另一边 |
| `resolve_graph(nodes, edges)` | `resolve_graph(fragments, threshold=...)`——第二个位置参数是阈值，旧测试传 `[]` 只是"恰好没报错" |
| `create_workspace(...) -> workspace_id` | 返回 `Path`（workspace 根目录），id 是 `.name` |
| `parse_transcript(str)` + `result.turns[i].user_text` | 吃 `Path`，返回 `ParsedSession.utterances`（发言列表，不是 user/assistant 轮次对）；逐字稿格式是 `发言人(HH:MM:SS): 文本`，文件名前 14 位必须是 `YYYYMMDDHHMMSS` |
| `ingest(list[Chunk], rebuild=True)` | `ingest(list[dict], mode="overwrite"/"append")` |
| `make_title()` 截 50 字 + `"..."` | 截 **24** 字 + 单字符 `"…"` |
| `load_workspace_config()` 遇坏 JSON 抛 `JSONDecodeError` | 捕获后**降级**到 `DEFAULT_WORKSPACE_CONFIG`（配置写坏时 app 仍打得开） |
| `retrieve()` 空库返回 `[]` | 抛 `ValueError: Table 'sessions' was not found`（有意的失败可见性；UI 侧由 `list_indexed_records()` 提前挡掉） |
| `list_indexed_records()` 按日期正序 | 按 `(session_date, source_file)` **倒序**（新的在前） |
| `answer()` 吞掉 LLM 异常 | 直接向上抛，让 app.py 显示真实错误而不是伪装成"没有相关记录" |

**实际工作量**：约 0.5 天（4 个集成测试文件重写，无一删除）

### Phase 3: 卓越品质（持续）

**目标：整体覆盖率达到 85%**

- 提升所有 P0 文件到 90%
- 补充集成测试
- 添加性能测试
- 完善边缘情况测试

---

## 测试策略

### 1. Mock 策略

**patch 调用方 namespace，不是定义处**（任务 14 里 13 个测试栽在这一点上）：

```python
# ✅ ask.py 写的是 `from scripts.llm import ask_llm`，名字绑在 scripts.ask 里
def test_answer(monkeypatch, fake_resp):
    from scripts import ask
    monkeypatch.setattr(ask, "ask_llm", lambda contents, **kw: fake_resp("Mocked response"))
    result = ask.answer("test question")
    assert result["answer"] == "Mocked response"

# ❌ patch("scripts.llm.ask_llm") 拦不到 ask.py 里已绑定的名字——
#    测试照样打真实 API（现已被 conftest 的 block_real_llm_calls 兜住）

# Embeddings 用 tests/integration/conftest.py 的 deterministic_embed，
# 它一次性 patch 掉 embedder / ingest / ask 三处绑定；维度是 1024（EMBEDDING_DIM），不是 768
```

### 2. Fixture 策略

```python
# 共享测试数据（真实 dataclass：ParsedSession 是 utterances，不是 turns）
@pytest.fixture
def sample_session():
    from scripts.parse import ParsedSession, Utterance
    return ParsedSession(
        source_file="20260101000000_测试.txt",
        session_date="2026-01-01",
        file_datetime="20260101000000",
        utterances=[Utterance(speaker="来访者", timestamp="00:00:01", text="你好", line_no=1)],
    )

# 隔离环境：不用自己写。tests/conftest.py 的 autouse isolate_data_root
# 已经把 WORKSPACES_ROOT / INDEX_SETTINGS_PATH / GEMINI_SETTINGS_PATH / ENV_PATH
# 全钉进 tmp_path，并预置了 DEFAULT_TEST_WORKSPACE。
# 需要"连 workspace 都不存在"的场景，用 empty_workspaces_root fixture。
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
