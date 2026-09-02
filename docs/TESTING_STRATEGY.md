# 测试策略：如何避免"修复完下次还有错误"

## 问题分析

**症状**：不断发现新的运行时错误（AttributeError, NameError），修复后又发现新的。

**根本原因**（本文档记录的是项目初期状况，当时覆盖率仅 2%）：
1. ❌ **测试覆盖率不足**（初期 2%，现已提升到 71%，见下方目标表）
2. ❌ **测试层次不完整**（只有单元测试，没有集成测试）
3. ❌ **没有静态代码检查**（依赖手动发现错误）
4. ❌ **缺少端到端测试**（没有模拟实际运行环境）

---

## 解决方案：四层防护体系

### 第一层：静态代码检查（最快）⚡️

**工具**: `scripts/check_code_patterns.py`

**检查内容**：
- ✅ 路径函数被当作 Path 对象使用（如 `CHAT_MEMORY_PATH.exists()`）
- ⚪ Optional 导入在文档字符串内（误报太多，已停用，恒返回空）
- ⚪ 类型注解兼容性（曾拦截 PEP 604；项目现钉 3.12，已停用，恒返回空）
- ⚠️ 缺少 workspace_id 参数（只对 `app.py` / `pages/` 发**警告**，不挡提交）

**扫描范围**：递归扫当前目录，排除 `.venv/`、`__pycache__/`、`tests/`。
排除 `tests/` 是必须的——测试文件里会故意放坏模式当 fixture 数据（例如
`tests/unit/test_check_code_patterns.py` 断言 `RAW_DIR.exists` 会被检出），
扫它们只会产生假阳性、把正常提交挡在门外。显式点名文件时不受此排除影响。

**使用**：
```bash
# 检查所有文件（不含 tests/）
python scripts/check_code_patterns.py

# 检查特定文件（显式指定则不排除，可用于检查测试文件）
python scripts/check_code_patterns.py app.py scripts/ask.py

# 在 commit 前自动运行（Git hook）
echo "python scripts/check_code_patterns.py" > .git/hooks/pre-commit
chmod +x .git/hooks/pre-commit
```

**优点**：
- 秒级反馈，不需要运行代码
- 捕获 90% 的常见错误
- 可以集成到 IDE、pre-commit hook、CI

---

### 第二层：导入测试（基础）✅

**文件**: `tests/unit/test_imports.py`

**检查内容**：
- ✅ 所有模块能成功导入
- ✅ Import-time 错误（NameError, ImportError）
- ✅ 模块级别的类型注解错误

**使用**：
```bash
# 运行导入测试（快速）
pytest tests/unit/test_imports.py -v

# 必须在每次 commit 前通过
```

**覆盖范围**：
- ✅ 捕获 100% 的导入错误
- ❌ 不捕获运行时逻辑错误

---

### 第三层：单元测试（详细）🔍

**目录**: `tests/unit/`

**检查内容**：
- ✅ 核心函数的输入输出
- ✅ 边缘情况和错误处理
- ✅ 业务逻辑正确性

**使用**：
```bash
# 运行所有单元测试
pytest tests/unit/ -v --cov=scripts --cov-report=term-missing

# 目标：覆盖率 ≥ 80%
```

**当前状态**（2026-09-03 实测 `pytest tests/ --integration --cov=scripts --cov=app`）：
- 覆盖率：73.70%（已过 hook 的 70% 闸门，目标 ≥ 80%）
- 单元测试文件：31 个（tests/unit/）
- 测试结果：905 通过 / 0 失败

> `app.py` 仍只有 24%，原因是 autouse 硬隔离（见下方「测试隔离的两道闸门」）令 `import app`
> 不再顺带执行模块级 UI 代码；这是刻意用覆盖率换取「测试不会污染真实数据、不会打真实 API」。

> ✅ 集成测试（`pytest tests/integration/ --integration`）**73 passed / 0 failed**
> （2026-07-26 修完，历程：33 failed → 18 failed → 全绿）。全套 `pytest tests/ --integration`
> 现为 **905 passed**。修法与三类根因见 [TESTING_COVERAGE_PLAN.md](./TESTING_COVERAGE_PLAN.md) 任务 14。

### 服务生命周期回归测试

`tests/unit/test_counseling_agent_ctl.py` 用替身 `launchctl` 执行真实 shell 脚本，确认：
- `start` / `stop` 管理 wake gateway、Streamlit 与两个看门狗
- `web-start` / `web-stop` 只管理 Streamlit，不会误停负责唤醒的 gateway
- Streamlit plist 是 8502、`RunAtLoad=false`、`KeepAlive=false`
- wake gateway plist 是 8501、默认 idle timeout 为 1800 秒
- `status` 不调用或报告 Tailscale
- repo 不提供自动连接 Tailscale 的 launchd job

`tests/unit/test_streamlit_wake_server.py` 覆盖健康探测、控制失败可见性、`lsof` 客户端判断、
30 分钟边界与重置、探测失败时不误关，以及真实本机 HTTP server 的 wake/status 响应。
这两组测试共同保证空闲回收不会牺牲固定入口，也不依赖 Tailscale；远端私网只在用户手动执行
`tailscale up` 时启用。

### 测试隔离的两道闸门（`tests/conftest.py`，autouse）

写在 conftest 顶层且 autouse，因为"让每个测试自己记得隔离"已被证明不可靠：

| 闸门 | 作用 |
|-----|-----|
| `isolate_data_root` | 把 `workspace_manager.WORKSPACES_ROOT` / `PRIVATE_DIR`、`INDEX_SETTINGS_PATH`、`GEMINI_SETTINGS_PATH`、`ENV_PATH` 钉进 `tmp_path`，清 `CURRENT_WORKSPACE` 与 `st.session_state`，并预置一个 `default-ws`（要"零个 workspace"的测试用 `empty_workspaces_root`）|
| `block_real_llm_calls` | 在 `genai.Client` / `openai.OpenAI` 构造函数及 `llm.run_subprocess`（Copilot CLI）上设断路器，漏 mock 时**大声报错**而不是静默出网 |

> 2026-07-30 移除的第三道闸门 `reset_module_caches`（曾清 `ask._table` / `ask._all_chunks_cache`）：
> 这两个模块级单例本身已经被删掉（`scripts/ask.py` 的 `_get_table()` / `_load_all_chunks()`
> 改成每次都按传入的 `workspace_id` 重新读取，不再缓存），既修掉了"跨 workspace 泄漏"的
> 生产 bug，也顺带让这个"必须记得清"的测试闸门变得不再必要——没有全局状态就没有跨测试
> 泄漏可言。详见 [ARCHITECTURE.md](./ARCHITECTURE.md) 的「缓存策略」一节。

⚠️ 三个反复踩的坑，写测试时记牢：
1. `WORKSPACES_ROOT` 是 **import-time 求值的常量**，patch `PRIVATE_DIR` 对它无效。
2. Mock LLM 要 patch **调用方 namespace 里的名字**（`scripts.ask.ask_llm` / `scripts.summarize.ask_llm`），
   patch `scripts.llm.ask_llm` 拦不住 `from scripts.llm import ask_llm` 的绑定。
   同理 `ingest.py` 的 `embed`、`index_records.py` 的 `SUMMARIES_DIR` 都得按调用方模块打。
3. **不要 patch `streamlit.session_state`**（曾有 autouse fixture 做 `patch("streamlit.session_state", {})`）：
   `{}` 没有属性赋值语义，`set_current_workspace()` 里的 `st.session_state.current_workspace = x`
   会直接 `AttributeError`，workspace 切换测了个假。清理已由 `isolate_data_root` 统一负责。

`tests/unit/test_llm.py::TestCopilotCLIProvider` 额外锁定本机 CLI 通路：无工具参数、单/多轮 prompt
序列化、JSON 验证，以及未安装、超时、非零退出和空响应的错误可见性。测试只 mock
`scripts.llm.run_subprocess`，绝不真的消耗 Copilot 请求。

### 集成测试专用 fixtures（`tests/integration/conftest.py`）

集成测试要真的把 parse → chunk → ingest → retrieve 串起来跑，只有两样东西不能用真的：

| fixture | 替掉什么 | 为什么 |
|---------|---------|-------|
| `deterministic_embed` | `embedder.embed` / `embed_one`（含 `ingest.py`、`ask.py` 里的 module-level 绑定）| 真 BGE-M3 要 2GB + 每次加载数秒。用 `crc32(text)` 当种子：同文本同向量（余弦 1.0）、不同文本近正交，于是 `resolve_graph()` 的"该合并的必然合并"是可断言的事实而不是概率 |
| `fake_resp` | LLM 响应对象工厂（`.text` + `.usage_metadata.*_token_count`）| 调用方只用这两处；token 数必须是真整数，MagicMock 会让 `ask.py` 的加法拿到 MagicMock |
| `no_reranker` | 通过 `index_settings.save()` 关掉 reranker + `min_score` | reranker 是本地真模型，而且假向量在它眼里毫无意义、分数会被阈值滤掉。走**正式配置通路**而不是 patch 内部，测的就是"用户在 UI 里关掉精排"的真实行为 |

---

### 第四层：集成/端到端测试（完整）🎯

**文件**: `tests/integration/test_streamlit_app.py`

**检查内容**：
- ✅ Streamlit 应用能成功启动
- ✅ 所有 UI 路径能执行
- ✅ 路径函数被正确调用
- ✅ Workspace 参数传递正确

**使用**：
```bash
# 运行集成测试（需要 --integration flag）
pytest tests/integration/test_streamlit_app.py -v --integration

# 或者实际启动 UI 测试
streamlit run app.py --server.headless true
```

**关键测试**：
```python
def test_config_path_functions_not_used_as_paths():
    """确保路径函数被调用，不被当作 Path 对象"""
    # 如果有 CHAT_MEMORY_PATH.exists() 这种代码，测试会失败
```

---

## 完整的测试工作流

### 开发阶段（本地）

```bash
# 1. 写代码前：运行导入测试（确保没破坏基础）
pytest tests/unit/test_imports.py

# 2. 写代码时：运行静态检查（实时反馈）
python scripts/check_code_patterns.py <changed_files>

# 3. 写完代码：运行单元测试（验证逻辑）
pytest tests/unit/ -v

# 4. 提交前：运行完整测试
pytest tests/ -v --cov=scripts

# 5. 最后验证：启动 UI 手动测试
streamlit run app.py
```

### CI/CD 阶段（自动）

**GitHub Actions** (`.github/workflows/test.yml`):
1. 静态代码检查（check_code_patterns.py）
2. 导入测试（test_imports.py）
3. 单元测试（tests/unit/）
4. 集成测试（tests/integration/）
5. 覆盖率检查（≥ 70%）

---

## 具体修复流程

当发现新错误时，按以下顺序修复：

### 步骤 1：修复当前错误
```bash
# 找到错误位置
python scripts/check_code_patterns.py

# 修复错误
# ...

# 验证修复
python scripts/check_code_patterns.py
```

### 步骤 2：添加测试防止复发
```python
# tests/integration/test_streamlit_app.py
def test_specific_error_pattern():
    """测试这次发现的具体错误模式"""
    # 如果代码中仍有这个模式，测试会失败
```

### 步骤 3：更新静态检查规则
```python
# scripts/check_code_patterns.py
def check_new_pattern():
    """添加新的检查规则"""
    # 捕获这类错误的通用模式
```

### 步骤 4：运行完整测试套件
```bash
pytest tests/ -v
python scripts/check_code_patterns.py
```

---

## 测试覆盖率目标

> 数据更新于 2026-07-25 第二次（实测 `pytest tests/unit/ --cov=scripts --cov=app`，
> 即 pre-commit hook 覆盖率检查用的同一条命令）。2026-07-30 起 pre-commit hook 已从
> 4 项检查合并为 2 项（静态检查 + 单元测试/覆盖率合一），详见 `CLAUDE.md` 的
> Pre-commit Hook 章节；命令本身不变，只是不再分开跑三次 pytest。

| 层级 | 当前 | 目标 | 优先级 |
|------|------|------|--------|
| 静态检查 | ✅ 100% | ✅ 100% | P0 |
| 导入测试 | ✅ 100% | ✅ 100% | P0 |
| 单元测试 | 🟡 71%（scripts + app） | ✅ 80% | P1 |
| 集成测试 | ✅ 73 passed / 0 failed（2026-07-26 修完腐化，见 TESTING_COVERAGE_PLAN 任务 14） | ✅ 全绿 + 60% | P1 |
| **整体** | **🟢 71%（≥ hook 的 70% 阈值）** | **✅ 80%** | **P0** |

---

## 常见错误模式 Checklist

在每次 commit 前检查：

- [ ] 所有路径常量都用 `CONSTANT()` 调用（不是 `CONSTANT.method`）
- [ ] 所有 `from typing import` 在文档字符串外面
- [ ] Workspace 相关函数都传递了 `workspace_id` 参数
- [ ] 运行 `python scripts/check_code_patterns.py` 通过
- [ ] 运行 `pytest tests/unit/test_imports.py` 通过
- [ ] 实际启动 UI 验证（`streamlit run app.py`）

---

## 关键指标

**定义"完成"的标准**：

1. ✅ 静态检查通过（0 errors）
2. ✅ 导入测试通过（12/12）
3. ✅ 单元测试覆盖率 ≥ 80%
4. ✅ 集成测试通过
5. ✅ UI 实际启动成功
6. ✅ GitHub Actions CI 全绿

**如果满足以上所有条件，才能认为"修复完成"。**

---

## 总结

### 为什么会"修复完下次还有错误"？

❌ **只修复错误，不修复流程**
- 修复代码 ✅
- 添加测试 ❌ ← 关键缺失
- 更新检查工具 ❌ ← 关键缺失

✅ **正确的流程**：
1. 修复当前错误
2. 添加测试捕获这类错误
3. 更新静态检查规则
4. 运行完整测试套件
5. 提交时自动运行检查

### 核心原则

> **测试不是为了通过 CI，而是为了确保代码在实际环境中能正常运行。**

- 测试要覆盖**实际执行路径**（不只是理想情况）
- 测试要覆盖**所有层级**（静态检查 → 导入 → 单元 → 集成 → E2E）
- 测试要**自动化**（不依赖人工记忆）
- 测试要**快速反馈**（秒级 → 分钟级 → 小时级）

### 下一步行动

1. **立即**：运行 `python scripts/check_code_patterns.py` 修复所有错误
2. **今天**：将静态检查添加到 pre-commit hook
3. **本周**：提升单元测试覆盖率到 80%
4. **本月**：完成集成测试套件

---

**记住**：每次发现新错误，都是改进测试的机会。修复错误的同时，必须更新测试和检查工具，这样才能避免类似错误再次出现。
