# 测试文档

## 概述

本项目使用 pytest 作为测试框架，参考了以下优秀项目的测试实践：
- **LlamaIndex**: LLM mock 和 fixture 组织
- **LangChain**: 单元/集成测试分层
- **LanceDB**: 文件系统隔离和环境管理

## 测试结构

```
tests/
├── conftest.py              # 共享 fixtures 和配置
├── unit/                    # 单元测试（快速，无外部依赖）
│   ├── test_workspace_manager.py
│   ├── test_chunk.py
│   ├── test_parse.py
│   └── test_graph_schema_loader.py
├── integration/             # 集成测试（需要 --integration flag）
│   └── test_full_pipeline.py
└── fixtures/                # 测试数据和 fixtures
```

## 运行测试

### 安装测试依赖

```bash
pip install -r requirements-dev.txt
```

### 运行所有单元测试

```bash
pytest tests/unit/ -v
```

### 运行特定测试文件

```bash
pytest tests/unit/test_workspace_manager.py -v
```

### 运行集成测试

```bash
pytest tests/integration/ -v --integration
```

### 查看代码覆盖率

```bash
pytest tests/unit/ --cov=scripts --cov-report=html
open htmlcov/index.html
```

### 并行运行（加速）

```bash
pytest tests/unit/ -n auto
```

### 跳过慢速测试

```bash
pytest -m "not slow"
```

## 编写测试

### 基本示例

```python
def test_example(isolated_workspace, mock_gemini):
    """测试描述。
    
    使用 fixtures:
    - isolated_workspace: 独立的测试环境
    - mock_gemini: Mock LLM 调用
    """
    from scripts.my_module import my_function
    
    result = my_function("test input")
    
    assert result == "expected output"
```

### 可用 Fixtures

#### 环境隔离
- `isolated_workspace`: 独立的 workspace 目录（自动清理）
- `test_lancedb`: 独立的 LanceDB 实例

#### Mock
- `mock_gemini`: Mock Google Gemini API
- `mock_embedder`: Mock BGE-M3 embeddings
- `mock_env_vars`: Mock 所有环境变量（自动应用）

#### 测试数据
- `sample_transcript`: 示例逐字稿内容
- `sample_workspace_config`: 示例 workspace 配置

### 最佳实践

1. **使用 isolated_workspace**: 每个测试独立环境
   ```python
   def test_something(isolated_workspace):
       # 自动创建临时目录，测试后自动清理
   ```

2. **Mock LLM 调用**: 避免真实 API 调用
   ```python
   def test_with_llm(mock_gemini):
       # mock_gemini 自动拦截 API 调用
   ```

3. **集成测试标记**: 慢速测试加 marker
   ```python
   @pytest.mark.integration
   def test_slow_integration():
       # 需要 --integration flag
   ```

4. **文件系统操作**: 使用 tmp_path
   ```python
   def test_file_ops(tmp_path):
       test_file = tmp_path / "test.txt"
       test_file.write_text("content")
   ```

## CI/CD

GitHub Actions 自动运行测试：
- **每次 push/PR**: 运行单元测试
- **Main 分支**: 额外运行集成测试
- **Python 版本**: 3.9, 3.10, 3.11

查看状态：[![Tests](../../actions/workflows/test.yml/badge.svg)](../../actions/workflows/test.yml)

## 调试测试

### 使用 ipdb

```python
def test_debug():
    import ipdb; ipdb.set_trace()
    # 代码在这里暂停
```

### 查看详细输出

```bash
pytest tests/unit/test_example.py -v -s
```

### 只运行失败的测试

```bash
pytest --lf  # last failed
```

## 常见问题

### Q: 测试报错 "No module named 'scripts'"
A: 确保在项目根目录运行 pytest

### Q: LanceDB 相关测试失败
A: 使用 `test_lancedb` fixture，它会自动清理

### Q: Mock 不生效
A: 检查 patch 路径是否正确（应该是使用的地方，不是定义的地方）

## 参考资源

- [pytest 文档](https://docs.pytest.org/)
- [pytest-cov](https://pytest-cov.readthedocs.io/)
- [LlamaIndex 测试](https://github.com/run-llama/llama_index/tree/main/llama-index-core/tests)
- [LangChain 测试](https://github.com/langchain-ai/langchain/tree/master/libs/langchain/tests)
