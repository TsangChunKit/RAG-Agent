# 测试策略：如何避免"修复完下次还有错误"

## 问题分析

**症状**：不断发现新的运行时错误（AttributeError, NameError），修复后又发现新的。

**根本原因**：
1. ❌ **测试覆盖率不足**（2%）
2. ❌ **测试层次不完整**（只有单元测试，没有集成测试）
3. ❌ **没有静态代码检查**（依赖手动发现错误）
4. ❌ **缺少端到端测试**（没有模拟实际运行环境）

---

## 解决方案：四层防护体系

### 第一层：静态代码检查（最快）⚡️

**工具**: `scripts/check_code_patterns.py`

**检查内容**：
- ✅ 路径函数被当作 Path 对象使用（如 `CHAT_MEMORY_PATH.exists()`）
- ✅ Optional 导入在文档字符串内
- ✅ Python 3.9 不兼容的类型注解（`dict | None`）
- ✅ 缺少 workspace_id 参数

**使用**：
```bash
# 检查所有文件
python scripts/check_code_patterns.py

# 检查特定文件
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

**当前状态**：
- 覆盖率：12%（需要提升到 80%）
- 测试文件：5 个（需要增加）

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

| 层级 | 当前 | 目标 | 优先级 |
|------|------|------|--------|
| 静态检查 | ✅ 100% | ✅ 100% | P0 |
| 导入测试 | ✅ 83% (10/12) | ✅ 100% | P0 |
| 单元测试 | ❌ 12% | ✅ 80% | P1 |
| 集成测试 | ❌ 0% | ✅ 60% | P1 |
| **整体** | **❌ 15%** | **✅ 75%** | **P0** |

---

## 常见错误模式 Checklist

在每次 commit 前检查：

- [ ] 所有路径常量都用 `CONSTANT()` 调用（不是 `CONSTANT.method`）
- [ ] 所有 `from typing import` 在文档字符串外面
- [ ] 没有 `dict | None` 语法（Python 3.9 不支持）
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
