"""导入测试（Smoke Test）- 确保所有模块都能成功导入。

这是最基本的测试，能捕获：
- 语法错误
- 导入错误
- 类型注解兼容性问题
"""
import pytest


class TestImports:
    """测试所有 scripts 模块都能成功导入。"""

    def test_import_workspace_manager(self):
        """测试 workspace_manager 导入。"""
        from scripts import workspace_manager
        assert workspace_manager is not None

    def test_import_chunk(self):
        """测试 chunk 导入。"""
        from scripts import chunk
        assert chunk is not None

    def test_import_parse(self):
        """测试 parse 导入。"""
        from scripts import parse
        assert parse is not None

    def test_import_settings(self):
        """测试 settings 导入（会触发 Optional 导入问题）。"""
        from scripts import settings
        assert settings is not None

    def test_import_index_settings(self):
        """测试 index_settings 导入。"""
        from scripts import index_settings
        assert index_settings is not None

    def test_import_graph_schema_loader(self):
        """测试 graph_schema_loader 导入。"""
        from scripts import graph_schema_loader
        assert graph_schema_loader is not None

    def test_import_ask(self):
        """测试 ask 导入（最复杂的模块）。"""
        from scripts import ask
        assert ask is not None

    def test_import_ingest(self):
        """测试 ingest 导入。"""
        from scripts import ingest
        assert ingest is not None

    def test_import_build_graph(self):
        """测试 build_graph 导入。"""
        from scripts import build_graph
        assert build_graph is not None

    def test_import_llm(self):
        """测试 llm 导入。"""
        from scripts import llm
        assert llm is not None

    def test_import_all_scripts(self):
        """一次性导入所有核心模块。"""
        from scripts import (
            ask,
            build_graph,
            chunk,
            graph_schema_loader,
            ingest,
            llm,
            parse,
            settings,
            workspace_manager,
        )
        # 如果能到这里，说明所有导入都成功
        assert True


class TestStreamlitImports:
    """测试 Streamlit 应用的导入路径。"""

    def test_app_imports(self):
        """测试 app.py 的导入语句（模拟 Streamlit 启动）。"""
        # 这是 app.py 的实际导入顺序
        from scripts import index_records, index_settings, settings
        from scripts.workspace_manager import (
            get_current_workspace,
            list_workspaces,
            load_workspace_config,
        )

        assert index_records is not None
        assert index_settings is not None
        assert settings is not None
        assert get_current_workspace is not None
        assert list_workspaces is not None
        assert load_workspace_config is not None
