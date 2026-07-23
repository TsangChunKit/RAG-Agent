# 开发指南

## 目的

**为 AI 开发者提供开发新功能的完整流程，避免常见错误。**

---

## 开发新功能的标准流程

### Phase 0: 启动前检查（必须）

```bash
# 1. 读取项目状态
Read README.md           # 了解项目全貌
Read CLAUDE.md           # 了解开发规则
Read docs/API_REFERENCE.md     # 了解 API 规范
Read docs/ARCHITECTURE.md      # 了解架构

# 2. 确认环境
source .venv/bin/activate
python --version  # 确保 Python 3.9+
pytest --version  # 确保测试工具可用

# 3. 运行基础检查
python scripts/check_code_patterns.py  # 静态检查
pytest tests/unit/test_imports.py      # 导入测试
```

**如果任何检查失败，必须先修复再开发新功能。**

---

### Phase 1: 需求分析

#### 1.1 明确需求

回答以下问题：
- [ ] 这个功能要解决什么问题？
- [ ] 输入是什么？输出是什么？
- [ ] 有哪些边界情况？
- [ ] 有哪些错误情况？

#### 1.2 检查依赖

- [ ] 需要修改哪些现有模块？
- [ ] 需要调用哪些外部服务（LLM, Embeddings）？
- [ ] 需要访问哪些数据（文件系统, 向量库, 图谱）？
- [ ] 是否需要 workspace 隔离？

#### 1.3 评估影响

参考 `docs/ARCHITECTURE.md`：
- [ ] 这个功能属于哪一层？
- [ ] 会影响哪些下游模块？
- [ ] 是否需要数据迁移？

---

### Phase 2: 设计测试（TDD）

**在写任何实现代码前，先设计测试。**

#### 2.1 创建测试文件

```bash
# 如果模块是 scripts/new_feature.py
touch tests/unit/test_new_feature.py
```

#### 2.2 编写测试框架

```python
"""tests/unit/test_new_feature.py"""
import pytest
from unittest.mock import MagicMock, patch


class TestNewFeature:
    """新功能测试"""

    def test_happy_path(self):
        """测试正常情况"""
        from scripts.new_feature import main_function
        
        result = main_function(valid_input)
        
        assert result == expected_output
    
    def test_edge_case_empty_input(self):
        """测试边缘情况：空输入"""
        from scripts.new_feature import main_function
        
        result = main_function("")
        
        assert result is None  # 或其他合适的行为
    
    def test_error_handling(self):
        """测试错误处理"""
        from scripts.new_feature import main_function
        
        with pytest.raises(ValueError):
            main_function(invalid_input)
    
    @patch('scripts.new_feature.external_dependency')
    def test_with_mock(self, mock_dep):
        """测试 Mock 外部依赖"""
        mock_dep.return_value = "mocked"
        
        result = main_function("input")
        
        assert "mocked" in result
```

#### 2.3 运行测试（应该全部失败）

```bash
pytest tests/unit/test_new_feature.py -v
# 预期：全部失败（因为还没实现）
```

**这是 TDD 的关键：测试先行，确保测试真的在测试功能。**

---

### Phase 3: 实现功能

#### 3.1 创建模块文件

```python
"""scripts/new_feature.py

功能描述：这个模块做什么。

关键函数：
- main_function() - 主要功能
- helper_function() - 辅助功能
"""
from typing import Optional

from config import SOME_PATH
from scripts import workspace_manager


def main_function(input: str, workspace_id: Optional[str] = None) -> Optional[str]:
    """
    主要功能函数。
    
    Args:
        input: 输入参数
        workspace_id: Workspace ID（None = 当前）
    
    Returns:
        处理结果，失败返回 None
    
    Raises:
        ValueError: 输入无效时
    """
    # 参数验证
    if not input:
        return None
    
    if not input.strip():
        raise ValueError("Input cannot be empty")
    
    # 业务逻辑
    result = process(input)
    
    return result


def helper_function(data: dict) -> str:
    """辅助函数（纯函数，易测试）。"""
    return f"Processed: {data}"
```

#### 3.2 实现最小可行版本

**目标：让第一个测试通过**

```bash
pytest tests/unit/test_new_feature.py::TestNewFeature::test_happy_path -v
# 预期：通过 ✅
```

#### 3.3 逐步实现所有功能

每实现一个功能，立即运行对应测试：

```bash
# 实现边缘情况处理
pytest tests/unit/test_new_feature.py::TestNewFeature::test_edge_case_empty_input -v

# 实现错误处理
pytest tests/unit/test_new_feature.py::TestNewFeature::test_error_handling -v
```

**循环：实现 → 测试 → 修复 → 测试通过**

---

### Phase 4: 验证覆盖率

#### 4.1 运行覆盖率检查

```bash
pytest tests/unit/test_new_feature.py \
  --cov=scripts.new_feature \
  --cov-report=term-missing \
  -v
```

**目标：≥ 80% 覆盖率**

#### 4.2 查看未覆盖的行

```bash
# 生成 HTML 报告
pytest tests/unit/test_new_feature.py \
  --cov=scripts.new_feature \
  --cov-report=html

# 打开报告
open htmlcov/index.html
```

红色行 = 未覆盖 → 补充测试

#### 4.3 补充缺失的测试

识别未覆盖的代码路径：
- 错误处理分支
- 特殊条件判断
- 异常情况

为每个未覆盖路径添加测试，直到覆盖率 ≥ 80%。

---

### Phase 5: 集成测试

#### 5.1 测试与其他模块的集成

如果新功能与其他模块交互，添加集成测试：

```python
"""tests/integration/test_new_feature_integration.py"""
import pytest
from unittest.mock import patch


class TestNewFeatureIntegration:
    """新功能集成测试"""
    
    @patch('scripts.llm.ask_llm')
    @patch('scripts.embedder.embed_one')
    def test_full_pipeline(self, mock_embed, mock_llm):
        """测试完整流程"""
        mock_embed.return_value = [0.1] * 768
        mock_llm.return_value = "LLM response"
        
        from scripts.new_feature import main_function
        
        result = main_function("input")
        
        assert result is not None
        mock_embed.assert_called()
        mock_llm.assert_called()
```

#### 5.2 运行集成测试

```bash
pytest tests/integration/test_new_feature_integration.py --integration -v
```

---

### Phase 6: 更新文档

#### 6.1 更新 API Reference

在 `docs/API_REFERENCE.md` 添加新函数文档：

```markdown
### scripts/new_feature.py

#### `main_function()`
\`\`\`python
def main_function(
    input: str,
    workspace_id: Optional[str] = None
) -> Optional[str]:
    """
    功能描述。
    
    Args:
        input: 输入参数
        workspace_id: Workspace ID
    
    Returns:
        处理结果
    """
\`\`\`
```

#### 6.2 更新 ARCHITECTURE.md（如果需要）

如果新功能影响架构：
- 更新数据流图
- 更新模块依赖图
- 添加到扩展点说明

#### 6.3 更新 README.md（如果需要）

如果是用户可见功能，更新用户文档。

---

### Phase 7: 提交前检查

#### 7.1 运行完整测试套件

```bash
# 1. 静态检查
python scripts/check_code_patterns.py

# 2. 导入测试
pytest tests/unit/test_imports.py -v

# 3. 单元测试
pytest tests/unit/ -v

# 4. 集成测试（如果添加了）
pytest tests/integration/ --integration -v

# 5. 覆盖率检查
pytest --cov=scripts --cov-fail-under=70 -v
```

**如果任何检查失败，必须修复。**

#### 7.2 运行 Pre-commit Hook

```bash
# 手动运行 hook
.git/hooks/pre-commit
```

**如果 hook 失败，必须修复。**

#### 7.3 实际测试（如果涉及 UI）

```bash
streamlit run app.py
# 手动测试新功能
```

---

### Phase 8: 提交

#### 8.1 查看改动

```bash
git status
git diff
```

确认：
- [ ] 只包含相关改动
- [ ] 没有调试代码
- [ ] 没有敏感信息（API keys, tokens）
- [ ] 没有临时文件

#### 8.2 提交

```bash
git add -A
git commit -m "feat: 添加新功能

详细说明：
- 实现了 xxx 功能
- 解决了 xxx 问题
- 测试覆盖率：xx%

Breaking changes: 无

Co-Authored-By: Claude Sonnet 4.5 (1M context) <noreply@anthropic.com>
"
```

#### 8.3 推送

```bash
git push
```

---

## 常见开发场景

### 场景 1: 添加新的数据处理函数

**示例：添加文本清理函数**

```python
# 1. 写测试
def test_clean_text():
    from scripts.utils import clean_text
    assert clean_text("  hello  ") == "hello"
    assert clean_text("") == ""

# 2. 实现
def clean_text(text: str) -> str:
    """清理文本（去除首尾空格）。"""
    return text.strip()

# 3. 验证覆盖率
pytest tests/unit/test_utils.py --cov=scripts.utils -v
```

### 场景 2: 添加新的 API 调用

**示例：添加新的 LLM provider**

```python
# 1. 写测试（Mock API 调用）
@patch('requests.post')
def test_call_new_llm(mock_post):
    mock_post.return_value.text = "response"
    result = call_new_llm("prompt")
    assert result == "response"

# 2. 实现
def call_new_llm(prompt: str) -> str:
    response = requests.post(url, json={"prompt": prompt})
    return response.text

# 3. 集成测试
def test_llm_integration():
    # 测试切换 provider
    settings.save(provider="new_llm")
    result = ask_llm("test")
    assert result is not None
```

### 场景 3: 修改现有函数

**原则：先补测试，再修改代码**

```python
# 1. 为现有函数补充测试（如果没有）
def test_existing_function():
    result = existing_function("input")
    assert result == "expected"

# 2. 添加新行为的测试
def test_existing_function_new_behavior():
    result = existing_function("new_input")
    assert result == "new_expected"

# 3. 修改实现
def existing_function(input: str) -> str:
    if input == "new_input":
        return "new_expected"
    return "expected"

# 4. 确保所有测试通过
pytest tests/unit/test_module.py -v
```

---

## 调试技巧

### 使用 pytest 调试

```bash
# 查看详细输出
pytest tests/unit/test_new_feature.py -vv

# 查看 print 输出
pytest tests/unit/test_new_feature.py -s

# 在第一个失败处停止
pytest tests/unit/test_new_feature.py -x

# 使用 pdb 调试
pytest tests/unit/test_new_feature.py --pdb
```

### 使用 coverage 定位问题

```bash
# 生成详细报告
pytest --cov=scripts.new_feature --cov-report=term-missing:skip-covered

# 只显示未覆盖的行
```

### 使用日志

```python
import logging

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

def my_function():
    logger.debug("进入函数")
    logger.info("处理数据")
    logger.warning("潜在问题")
    logger.error("错误发生")
```

---

## 代码规范速查

### Python 3.9 兼容性

```python
# ❌ 不兼容
def func() -> dict | None:
def func() -> str | int:

# ✅ 兼容
from typing import Optional, Union

def func() -> Optional[dict]:
def func() -> Union[str, int]:
```

### 导入顺序

```python
# 1. 标准库
import json
import re
from pathlib import Path
from typing import Optional

# 2. 第三方库
import numpy as np
import pandas as pd

# 3. 项目内部
from config import SOME_PATH
from scripts import module_name
from scripts.module import function_name
```

### Docstring 格式

```python
def function(arg1: str, arg2: Optional[int] = None) -> bool:
    """
    一行简短描述。
    
    详细说明（可选）：
    这个函数做什么，为什么需要它。
    
    Args:
        arg1: 参数1说明
        arg2: 参数2说明（可选，默认 None）
    
    Returns:
        返回值说明
    
    Raises:
        ValueError: 什么情况下抛出
    
    Example:
        >>> function("test")
        True
    """
```

### 命名规范

```python
# 函数/变量：snake_case
def my_function():
    my_variable = 1

# 类：PascalCase
class MyClass:
    pass

# 常量：UPPER_SNAKE_CASE
MAX_RETRIES = 3

# 私有函数：_leading_underscore
def _internal_helper():
    pass
```

---

## 常见错误和解决

### 错误 1: 测试通过但 UI 出错

**原因**：测试覆盖率不足

**解决**：
1. 检查覆盖率：`pytest --cov=scripts.module --cov-report=html`
2. 补充缺失测试
3. 添加集成测试

### 错误 2: Import 错误

**原因**：循环依赖或导入路径错误

**解决**：
1. 检查模块依赖图（docs/ARCHITECTURE.md）
2. 确保只向下依赖
3. 运行 `pytest tests/unit/test_imports.py`

### 错误 3: Workspace 相关错误

**原因**：没有传递 workspace_id

**解决**：
1. 所有数据访问函数添加 `workspace_id: Optional[str] = None`
2. 使用 `get_workspace_dir(workspace_id)` 获取目录
3. 使用 `PATH_FUNCTION(workspace_id)` 获取路径

### 错误 4: Mock 不生效

**原因**：Mock 路径错误

**解决**：
```python
# ❌ 错误：Mock 定义位置
@patch('scripts.module.function')

# ✅ 正确：Mock 使用位置
@patch('scripts.calling_module.function')
```

---

## 性能优化

### 1. 批量操作

```python
# ❌ 慢：逐个处理
for item in items:
    embed_one(item)

# ✅ 快：批量处理
embeddings = embed(items)  # 批量调用
```

### 2. 缓存

```python
from functools import lru_cache

@lru_cache(maxsize=128)
def expensive_function(input: str) -> str:
    # 计算密集型操作
    return result
```

### 3. 避免重复加载

```python
# ❌ 慢：每次都加载
def process():
    model = load_model()  # 重复加载
    return model.predict(...)

# ✅ 快：全局加载一次
_model = None

def get_model():
    global _model
    if _model is None:
        _model = load_model()
    return _model

def process():
    model = get_model()
    return model.predict(...)
```

---

## 快速参考

### 开发检查清单

新功能开发前：
- [ ] 读取 README.md, CLAUDE.md, API_REFERENCE.md
- [ ] 运行基础检查（静态 + 导入）
- [ ] 理解需求和依赖

开发过程中：
- [ ] 测试先行（TDD）
- [ ] 每个函数都有测试
- [ ] 覆盖率 ≥ 80%
- [ ] Mock 所有外部依赖

提交前：
- [ ] 所有测试通过
- [ ] 静态检查通过
- [ ] Pre-commit hook 通过
- [ ] 文档已更新
- [ ] UI 实际测试（如果相关）

### 常用命令

```bash
# 运行测试
pytest tests/unit/test_module.py -v

# 查看覆盖率
pytest --cov=scripts.module --cov-report=html

# 静态检查
python scripts/check_code_patterns.py

# 导入测试
pytest tests/unit/test_imports.py -v

# Pre-commit hook
.git/hooks/pre-commit

# 启动 UI
streamlit run app.py
```

---

## 获取帮助

**遇到问题时的查找顺序**：

1. **API_REFERENCE.md** - 查函数签名和用法
2. **ARCHITECTURE.md** - 查模块依赖和数据流
3. **TESTING_STRATEGY.md** - 查测试方法
4. **CLAUDE.md** - 查开发规则
5. **代码注释** - 查具体实现
6. **Git 历史** - 查类似改动

**调试流程**：

1. 读错误信息（完整的 stack trace）
2. 定位出错行号
3. 运行相关测试
4. 检查覆盖率报告
5. 添加日志/断点调试
6. 修复问题
7. 添加回归测试

---

## 总结

**核心原则**：

1. **测试先行**：先写测试，再写代码
2. **小步迭代**：每次只改一小块，立即测试
3. **覆盖率目标**：≥ 80% 才提交
4. **文档同步**：代码和文档一起更新
5. **自动验证**：依赖 pre-commit hook，不依赖人工

**记住**：
> 前期多花 20 分钟写测试，后期节省无数小时调试。
> 测试不是负担，而是投资。
