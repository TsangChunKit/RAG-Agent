"""测试静态检查工具本身（scripts/check_code_patterns.py）。

这个脚本是 pre-commit hook 的第一道闸门。它自己没测试的话，「检查通过」这句话就没有分量：
误报会挡住正常提交，漏报会让路径函数误用之类的真实错误溜进去。

重点测试：
1. check_path_function_usage() - 路径函数被当 Path 用（RAW_DIR.exists vs RAW_DIR().exists）
2. check_type_annotation_compatibility() - 已停用（Python 3.12 下 PEP 604 合法，恒返回空）
3. check_missing_workspace_id() - 只对 app.py / pages/ 发警告
4. main() - 退出码：有 error → 1，只有 warning → 0，全过 → 0
"""
import pytest

from scripts.check_code_patterns import (
    CodeIssue,
    check_missing_workspace_id,
    check_optional_in_docstring,
    check_path_function_usage,
    check_type_annotation_compatibility,
    main,
)


# ── Helpers ───────────────────────────────────────────────────────────────


def _py(tmp_path, content, name="sample.py"):
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p


# ── CodeIssue ─────────────────────────────────────────────────────────────


class TestCodeIssue:
    def test_error_str_has_cross_icon(self):
        issue = CodeIssue("app.py", 12, "RAW_DIR.exists", "用 RAW_DIR().exists")

        text = str(issue)

        assert text.startswith("❌")
        assert "app.py:12" in text
        assert "Found: RAW_DIR.exists" in text
        assert "Fix: 用 RAW_DIR().exists" in text

    def test_warning_str_has_warning_icon(self):
        issue = CodeIssue("app.py", 3, "update_memory()", "传 workspace_id", severity="warning")

        assert str(issue).startswith("⚠️")

    def test_default_severity_is_error(self):
        assert CodeIssue("f.py", 1, "p", "s").severity == "error"


# ── 路径函数误用 ───────────────────────────────────────────────────────────


class TestCheckPathFunctionUsage:
    """config.py 里的路径「常量」其实是函数，忘记加 () 是本项目最常见的错。"""

    def test_detects_path_function_used_as_object(self, tmp_path):
        f = _py(tmp_path, "if RAW_DIR.exists():\n    pass\n")

        issues = check_path_function_usage(f)

        assert len(issues) == 1
        assert issues[0].pattern == "RAW_DIR.exists"
        assert issues[0].line_num == 1
        assert issues[0].severity == "error"

    def test_correct_usage_is_not_flagged(self, tmp_path):
        f = _py(tmp_path, "if RAW_DIR(workspace_id).exists():\n    pass\n")

        assert check_path_function_usage(f) == []

    @pytest.mark.parametrize("method", ["exists", "read_text", "write_text", "mkdir", "glob"])
    def test_covers_common_path_methods(self, tmp_path, method):
        f = _py(tmp_path, f"GRAPH_JSON_PATH.{method}()\n")

        assert len(check_path_function_usage(f)) == 1

    @pytest.mark.parametrize(
        "func",
        ["RAW_DIR", "SUMMARIES_DIR", "CHUNKS_JSONL_PATH", "LONG_TERM_MEMORY_PATH", "DB_DIR"],
    )
    def test_covers_known_path_functions(self, tmp_path, func):
        f = _py(tmp_path, f"{func}.mkdir(parents=True)\n")

        assert len(check_path_function_usage(f)) == 1

    def test_clean_file_returns_empty(self, tmp_path):
        f = _py(tmp_path, "def f():\n    return 1\n")

        assert check_path_function_usage(f) == []

    def test_reports_line_numbers(self, tmp_path):
        f = _py(tmp_path, "x = 1\n\nDB_DIR.exists()\n")

        assert check_path_function_usage(f)[0].line_num == 3


# ── 已禁用的检查 ───────────────────────────────────────────────────────────


class TestCheckOptionalInDocstring:
    def test_disabled_returns_empty(self, tmp_path):
        """这条检查误报太多、已停用；保留测试是为了防止有人「顺手」把它打开。"""
        f = _py(tmp_path, '"""docstring\nfrom typing import Optional\n"""\n')

        assert check_optional_in_docstring(f) == []


# ── 类型注解兼容性（已停用）────────────────────────────────────────────────


class TestCheckTypeAnnotationCompatibility:
    """项目钉 Python 3.12，PEP 604 的 `X | None` 合法；此检查恒返回空。"""

    @pytest.mark.parametrize("annotation", ["dict | None", "list | None", "str | None", "int | None"])
    def test_pep604_unions_are_not_flagged(self, tmp_path, annotation):
        f = _py(tmp_path, f"def f() -> {annotation}:\n    return None\n")

        assert check_type_annotation_compatibility(f) == []

    def test_optional_form_is_not_flagged(self, tmp_path):
        f = _py(tmp_path, "def f() -> Optional[dict]:\n    return None\n")

        assert check_type_annotation_compatibility(f) == []

    def test_comment_lines_still_empty(self, tmp_path):
        f = _py(tmp_path, "# dict | None is fine on 3.12\n")

        assert check_type_annotation_compatibility(f) == []

    def test_always_returns_empty_list(self, tmp_path):
        """停用后签名仍是 List[CodeIssue]，调用方（main）依赖可迭代空列表。"""
        f = _py(tmp_path, "x: list | None = None\n")
        result = check_type_annotation_compatibility(f)
        assert result == []
        assert isinstance(result, list)


# ── workspace_id 警告 ─────────────────────────────────────────────────────


class TestCheckMissingWorkspaceId:
    """只针对 UI 层：app.py / pages/ 里忘了传 workspace_id 会串 workspace 的数据。"""

    def test_warns_in_app_py(self, tmp_path):
        f = _py(tmp_path, "update_memory()\n", name="app.py")

        issues = check_missing_workspace_id(f)

        assert len(issues) == 1
        assert issues[0].severity == "warning"
        assert issues[0].pattern == "update_memory()"

    def test_ignores_non_ui_files(self, tmp_path):
        """scripts/ 里的函数自己就带 workspace_id 默认值，不该被警告。"""
        f = _py(tmp_path, "update_memory()\n", name="some_script.py")

        assert check_missing_workspace_id(f) == []

    def test_call_with_argument_is_not_flagged(self, tmp_path):
        f = _py(tmp_path, "update_memory(workspace_id=ws)\n", name="app.py")

        assert check_missing_workspace_id(f) == []

    def test_covers_pages_dir(self, tmp_path):
        pages = tmp_path / "pages"
        pages.mkdir()
        f = pages / "心智地图.py"
        f.write_text("build_chat_graph()\n", encoding="utf-8")

        assert len(check_missing_workspace_id(f)) == 1


# ── main() ────────────────────────────────────────────────────────────────


class TestMain:
    """退出码就是 pre-commit hook 的判据。"""

    def test_clean_file_exits_zero(self, tmp_path, monkeypatch, capsys):
        f = _py(tmp_path, "def f():\n    return 1\n")
        monkeypatch.setattr("sys.argv", ["check_code_patterns.py", str(f)])

        with pytest.raises(SystemExit) as exc:
            main()

        assert exc.value.code == 0
        assert "所有检查通过" in capsys.readouterr().out

    def test_error_exits_one(self, tmp_path, monkeypatch, capsys):
        # 用路径函数误用作为 error 源（PEP 604 不再报错）
        f = _py(tmp_path, "if RAW_DIR.exists():\n    pass\n")
        monkeypatch.setattr("sys.argv", ["check_code_patterns.py", str(f)])

        with pytest.raises(SystemExit) as exc:
            main()

        assert exc.value.code == 1
        out = capsys.readouterr().out
        assert "发现错误" in out
        assert "1 个错误" in out

    def test_warning_only_exits_zero(self, tmp_path, monkeypatch, capsys):
        """警告不挡提交，但要打印出来。"""
        f = _py(tmp_path, "update_memory()\n", name="app.py")
        monkeypatch.setattr("sys.argv", ["check_code_patterns.py", str(f)])

        with pytest.raises(SystemExit) as exc:
            main()

        assert exc.value.code == 0
        assert "1 个警告" in capsys.readouterr().out

    def test_skips_nonexistent_paths(self, tmp_path, monkeypatch):
        monkeypatch.setattr("sys.argv", ["check_code_patterns.py", str(tmp_path / "nope.py")])

        with pytest.raises(SystemExit) as exc:
            main()

        assert exc.value.code == 0

    def test_ignores_double_dash_args(self, tmp_path, monkeypatch):
        f = _py(tmp_path, "x = 1\n")
        monkeypatch.setattr("sys.argv", ["check_code_patterns.py", str(f), "--fix"])

        with pytest.raises(SystemExit) as exc:
            main()

        assert exc.value.code == 0

    def test_no_args_scans_cwd_recursively(self, tmp_path, monkeypatch):
        """不给参数 → 递归扫当前目录（chdir 到 tmp_path，别扫真实项目）。"""
        (tmp_path / "pkg").mkdir()
        (tmp_path / "pkg" / "bad.py").write_text("if RAW_DIR.exists():\n    pass\n", encoding="utf-8")
        (tmp_path / ".venv").mkdir()
        (tmp_path / ".venv" / "vendored.py").write_text("if DB_DIR.exists():\n    pass\n", encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("sys.argv", ["check_code_patterns.py"])

        with pytest.raises(SystemExit) as exc:
            main()

        assert exc.value.code == 1  # 找到 pkg/bad.py

    def test_pep604_file_exits_zero(self, tmp_path, monkeypatch, capsys):
        """PEP 604 在 3.12 下合法，整文件只有 `X | None` 时应通过。"""
        f = _py(tmp_path, "def f() -> dict | None:\n    return None\n")
        monkeypatch.setattr("sys.argv", ["check_code_patterns.py", str(f)])

        with pytest.raises(SystemExit) as exc:
            main()

        assert exc.value.code == 0
        assert "所有检查通过" in capsys.readouterr().out

    @pytest.mark.parametrize("excluded_dir", [".venv", "__pycache__", "tests"])
    def test_excluded_dirs_not_scanned(self, tmp_path, monkeypatch, capsys, excluded_dir):
        """.venv/__pycache__ 不属于我们；tests/ 会故意放坏模式当 fixture（就像本文件）。

        扫它们只会产生假阳性，把正常提交挡在门外。
        """
        d = tmp_path / excluded_dir
        d.mkdir()
        (d / "sample.py").write_text("if RAW_DIR.exists():\n    pass\n", encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("sys.argv", ["check_code_patterns.py"])

        with pytest.raises(SystemExit) as exc:
            main()

        assert exc.value.code == 0
        assert "所有检查通过" in capsys.readouterr().out

    def test_nested_excluded_dir_not_scanned(self, tmp_path, monkeypatch):
        """排除是按路径层级判断的，深层嵌套（tests/unit/…）同样跳过。"""
        nested = tmp_path / "tests" / "unit"
        nested.mkdir(parents=True)
        (nested / "test_x.py").write_text("if RAW_DIR.exists():\n    pass\n", encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("sys.argv", ["check_code_patterns.py"])

        with pytest.raises(SystemExit) as exc:
            main()

        assert exc.value.code == 0

    def test_explicit_test_file_arg_is_still_checked(self, tmp_path, monkeypatch):
        """排除只作用于「不给参数」的递归扫描；显式点名一个文件时仍然检查它。"""
        f = _py(tmp_path, "if RAW_DIR.exists():\n    pass\n", name="test_something.py")
        monkeypatch.setattr("sys.argv", ["check_code_patterns.py", str(f)])

        with pytest.raises(SystemExit) as exc:
            main()

        assert exc.value.code == 1
