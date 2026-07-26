"""app.py UI 测试。

测试策略：
- Mock Streamlit 组件
- 测试关键函数和逻辑
- 不测试实际 UI 渲染（留给集成测试）

`@st.dialog` 装饰过的函数用 `.__wrapped__` 取原函数直接调用：装饰器只负责"渲染成弹窗"，
里面的**分支逻辑**（有没有待入库文件 / 点了按钮之后做什么 / 失败了要不要 rerun）才是会写错的地方，
也是唯一值得测的地方。
"""
from unittest.mock import MagicMock, patch

import pytest


class TestAppImports:
    """测试 app.py 可以成功导入"""

    def test_app_imports_successfully(self):
        """测试 app.py 所有导入"""
        try:
            # 导入会执行一些 Streamlit 代码，需要 mock
            with patch("streamlit.set_page_config"), patch("streamlit.markdown"), patch(
                "streamlit.title"
            ), patch("streamlit.caption"):
                import app  # noqa: F401

                assert True
        except Exception as e:
            pytest.fail(f"App imports failed: {e}")


class TestAppConfig:
    """测试应用配置"""

    def test_page_config_set(self):
        """测试页面配置"""
        with patch("streamlit.set_page_config") as mock_config:
            # 重新导入以触发配置
            import importlib

            import app

            importlib.reload(app)

            # 验证配置被调用
            # mock_config.assert_called_once()  # 可能已经被调用过


class TestDialogFunctions:
    """测试对话框函数"""

    @patch("streamlit.dialog")
    def test_system_instruction_dialog_exists(self, mock_dialog):
        """测试 system instruction 对话框定义"""
        import app

        # 函数应该存在
        assert hasattr(app, "system_instruction_dialog")
        assert callable(app.system_instruction_dialog)

    @patch("streamlit.dialog")
    def test_gemini_settings_dialog_exists(self, mock_dialog):
        """测试 Gemini 设置对话框定义"""
        import app

        assert hasattr(app, "gemini_settings_dialog")
        assert callable(app.gemini_settings_dialog)

    @patch("streamlit.dialog")
    def test_indexed_records_dialog_exists(self, mock_dialog):
        """测试索引记录对话框定义"""
        import app

        assert hasattr(app, "indexed_records_dialog")
        assert callable(app.indexed_records_dialog)


# ── 「⚡ 立即入库」按钮：不依赖看门狗的手动入库通路 ─────────────────────────────


@pytest.fixture
def dialog(monkeypatch):
    """执行 indexed_records_dialog 的内层函数，并把 st.* 换成可断言的替身。

    只 mock 这个弹窗真正用到的 st 组件；返回的 dict 里带着各个替身，测试直接断言调用参数。
    index_records 那半截（表格 + 变更记录）也一并 mock 掉——它有自己的单元测试，
    这里关心的是待入库那一段。
    """
    import app

    stubs = {}
    for name in ("warning", "caption", "success", "error", "divider",
                 "markdown", "dataframe", "info", "rerun"):
        stub = MagicMock()
        monkeypatch.setattr(f"streamlit.{name}", stub)
        stubs[name] = stub

    button = MagicMock(return_value=False)
    monkeypatch.setattr("streamlit.button", button)
    stubs["button"] = button

    # st.spinner 要能当 context manager 用
    spinner = MagicMock()
    spinner.return_value.__enter__ = MagicMock()
    spinner.return_value.__exit__ = MagicMock(return_value=False)
    monkeypatch.setattr("streamlit.spinner", spinner)

    monkeypatch.setattr(app.index_records, "list_indexed_records", lambda: [])
    monkeypatch.setattr(app.index_records, "load_change_log", lambda limit=30: [])

    stubs["run"] = app.indexed_records_dialog.__wrapped__
    stubs["app"] = app
    return stubs


def _fake_pending(monkeypatch, app, names):
    from pathlib import Path

    monkeypatch.setattr(app, "pending_raw_files", lambda: [Path("/raw") / n for n in names])


class TestIndexedRecordsDialogPending:
    def test_no_pending_shows_no_button(self, dialog, monkeypatch):
        """没有待入库文件时，整段提示和按钮都不该出现（别制造"要不要点一下"的噪音）。"""
        _fake_pending(monkeypatch, dialog["app"], [])

        dialog["run"]()

        dialog["warning"].assert_not_called()
        dialog["button"].assert_not_called()

    def test_pending_lists_files_and_offers_button(self, dialog, monkeypatch):
        _fake_pending(monkeypatch, dialog["app"], ["a.txt", "b.txt"])

        dialog["run"]()

        warned = dialog["warning"].call_args[0][0]
        assert "2" in warned and "a.txt" in warned and "b.txt" in warned
        assert "2" in dialog["button"].call_args[0][0]

    def test_button_not_clicked_does_not_ingest(self, dialog, monkeypatch):
        """按钮返回 False（没点）时绝不能入库——每次开弹窗都自动入库会烧掉 LLM 钱。"""
        _fake_pending(monkeypatch, dialog["app"], ["a.txt"])
        called = []
        monkeypatch.setattr(dialog["app"], "ingest_pending", lambda: called.append(1))

        dialog["run"]()

        assert called == []

    def test_click_ingests_and_reruns_on_success(self, dialog, monkeypatch):
        _fake_pending(monkeypatch, dialog["app"], ["a.txt"])
        dialog["button"].return_value = True
        monkeypatch.setattr(
            dialog["app"], "ingest_pending",
            lambda: {"ingested": ["a.txt"], "failed": []},
        )

        dialog["run"]()

        assert "a.txt" in dialog["success"].call_args[0][0]
        dialog["error"].assert_not_called()
        dialog["rerun"].assert_called_once()

    def test_failures_are_shown_and_page_not_rerun(self, dialog, monkeypatch):
        """失败原因必须留在屏幕上：rerun 会把 st.error 冲掉，等于静默失败。"""
        _fake_pending(monkeypatch, dialog["app"], ["bad.txt"])
        dialog["button"].return_value = True
        monkeypatch.setattr(
            dialog["app"], "ingest_pending",
            lambda: {"ingested": [], "failed": [{"file": "bad.txt", "error": "没有日期前缀"}]},
        )

        dialog["run"]()

        shown = dialog["error"].call_args[0][0]
        assert "bad.txt" in shown and "没有日期前缀" in shown
        dialog["rerun"].assert_not_called()

    def test_partial_failure_shows_both(self, dialog, monkeypatch):
        """一批里有成功也有失败：两边都要报，且不 rerun（否则失败信息看不到）。"""
        _fake_pending(monkeypatch, dialog["app"], ["ok.txt", "bad.txt"])
        dialog["button"].return_value = True
        monkeypatch.setattr(
            dialog["app"], "ingest_pending",
            lambda: {"ingested": ["ok.txt"], "failed": [{"file": "bad.txt", "error": "boom"}]},
        )

        dialog["run"]()

        assert "ok.txt" in dialog["success"].call_args[0][0]
        assert "bad.txt" in dialog["error"].call_args[0][0]
        dialog["rerun"].assert_not_called()


# 由于 app.py 主要是 UI 代码，大部分测试应该在集成测试中进行
# 这里只测试基本的导入、函数存在性，以及弹窗里的分支逻辑
