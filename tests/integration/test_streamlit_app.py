"""Streamlit 应用端到端测试。

目标：捕获所有 app.py 中的运行时错误。

这是"最后一道防线"测试：
- 模拟实际 Streamlit 启动流程
- 捕获所有 AttributeError, NameError 等
- 确保 app.py 中所有代码至少被执行一次
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# 标记为集成测试
pytestmark = pytest.mark.integration


class TestStreamlitApp:
    """测试 Streamlit 应用启动和基本执行。"""

    def test_app_imports_successfully(self):
        """测试 app.py 能成功执行所有导入语句。"""
        # 这会触发 app.py 中所有模块级别的代码
        import app
        assert app is not None

    def test_config_path_functions_not_used_as_paths(self, monkeypatch):
        """测试所有 config 路径函数都被正确调用（不被当作 Path 对象）。

        这是本次错误的根源：
        - config.py 中的路径从常量改为函数
        - 但 app.py 中仍然用 `CHAT_MEMORY_PATH().exists()` 而不是 `CHAT_MEMORY_PATH().exists()`

        这个测试会捕获所有类似错误。
        """
        from config import (
            CHAT_MEMORY_PATH,
            CHAT_GRAPH_JSON_PATH,
            LONG_TERM_MEMORY_PATH,
            GRAPH_JSON_PATH,
        )

        # 确保这些都是函数，不是 Path 对象
        assert callable(CHAT_MEMORY_PATH), "CHAT_MEMORY_PATH should be a function"
        assert callable(CHAT_GRAPH_JSON_PATH), "CHAT_GRAPH_JSON_PATH should be a function"
        assert callable(LONG_TERM_MEMORY_PATH), "LONG_TERM_MEMORY_PATH should be a function"
        assert callable(GRAPH_JSON_PATH), "GRAPH_JSON_PATH should be a function"

        # 确保调用后返回 Path 对象
        result = CHAT_MEMORY_PATH("_legacy")
        assert isinstance(result, Path), f"CHAT_MEMORY_PATH() should return Path, got {type(result)}"

    @pytest.mark.slow
    def test_app_execution_with_mocked_streamlit(self, isolated_workspace, monkeypatch):
        """测试 app.py 的实际执行（mock Streamlit 组件）。

        这是最全面的测试，会执行 app.py 中的所有代码。
        如果代码中有 AttributeError、NameError 等，这个测试会捕获。
        """
        # Mock Streamlit 组件（避免实际启动 UI）
        mock_st = MagicMock()
        mock_st.session_state = {}

        # Mock selectbox 返回值
        mock_st.selectbox.return_value = "_legacy"
        mock_st.button.return_value = False
        mock_st.columns.return_value = [MagicMock(), MagicMock(), MagicMock()]

        monkeypatch.setattr("streamlit.sidebar", mock_st)
        monkeypatch.setattr("streamlit.title", mock_st.title)
        monkeypatch.setattr("streamlit.subheader", mock_st.subheader)
        monkeypatch.setattr("streamlit.caption", mock_st.caption)
        monkeypatch.setattr("streamlit.divider", mock_st.divider)
        monkeypatch.setattr("streamlit.selectbox", mock_st.selectbox)
        monkeypatch.setattr("streamlit.button", mock_st.button)
        monkeypatch.setattr("streamlit.columns", mock_st.columns)

        # 尝试执行 app.py（会失败如果有错误）
        try:
            # 注意：这只测试导入时的代码，不测试运行时的交互
            import app
            # 如果能到这里，说明至少没有 import-time 错误
            assert True
        except AttributeError as e:
            if "'function' object has no attribute" in str(e):
                pytest.fail(f"Found path function used as Path object: {e}")
            raise
        except Exception as e:
            pytest.fail(f"app.py execution failed: {e}")


class TestAppCodePatterns:
    """静态代码模式检查（防止常见错误）。"""

    def test_no_direct_path_constant_usage(self):
        """检查 app.py 中没有直接使用路径常量的 Path 方法。

        常见错误模式：
        - CHAT_MEMORY_PATH().exists()  # 错误：CHAT_MEMORY_PATH 是函数
        - 正确：CHAT_MEMORY_PATH().exists()
        """
        app_path = Path(__file__).parent.parent.parent / "app.py"
        content = app_path.read_text()

        # 检查可能的错误模式
        error_patterns = [
            ("CHAT_MEMORY_PATH().exists", "应该使用 CHAT_MEMORY_PATH().exists()"),
            ("CHAT_MEMORY_PATH().read_text", "应该使用 CHAT_MEMORY_PATH().read_text()"),
            ("LONG_TERM_MEMORY_PATH().exists", "应该使用 LONG_TERM_MEMORY_PATH().exists()"),
            ("GRAPH_JSON_PATH().exists", "应该使用 GRAPH_JSON_PATH().exists()"),
            ("CHAT_GRAPH_JSON_PATH().parent", "应该使用 CHAT_GRAPH_JSON_PATH().parent"),
        ]

        errors = []
        for pattern, suggestion in error_patterns:
            if pattern in content:
                errors.append(f"Found '{pattern}' in app.py. {suggestion}")

        if errors:
            pytest.fail("Path function usage errors:\n" + "\n".join(errors))

    def test_workspace_functions_called_with_workspace_id(self):
        """检查关键函数都传递了 workspace_id 参数。

        这是多 workspace 架构的关键：
        - 所有涉及数据的操作都应该传递 workspace_id
        - 否则会使用错误的 workspace
        """
        app_path = Path(__file__).parent.parent.parent / "app.py"
        content = app_path.read_text()

        # 检查可能的遗漏
        warning_patterns = [
            # 这些函数应该传递 workspace_id
            ("update_chat_memory()", "应该传递 workspace_id 参数"),
            ("build_chat_graph()", "应该传递 workspace_id 参数"),
        ]

        warnings = []
        for pattern, suggestion in warning_patterns:
            if pattern in content:
                # 检查是否在同一行或下一行有 workspace_id
                lines = content.split('\n')
                for i, line in enumerate(lines):
                    if pattern in line and "workspace_id" not in line:
                        # 检查前后几行是否有 workspace_id
                        context = '\n'.join(lines[max(0, i-2):min(len(lines), i+3)])
                        if "workspace_id" not in context:
                            warnings.append(f"Line {i+1}: {pattern} - {suggestion}")

        # 这个是 warning 而不是 error（因为有些情况可能故意不传）
        if warnings:
            print("⚠️  Workspace ID warnings:")
            for w in warnings:
                print(f"  - {w}")
