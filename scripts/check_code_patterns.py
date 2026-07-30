#!/usr/bin/env python3
"""代码模式检查工具 - 在 commit 前自动运行。

捕获常见错误模式：
1. 路径函数被当作 Path 对象使用
2. 缺少 workspace_id 参数（仅 app.py / pages/ 警告）
3. Optional 导入在文档字符串内（已停用，恒返回空）
4. 类型注解兼容性（已停用：项目钉 Python 3.12，PEP 604 合法）

用法：
    python scripts/check_code_patterns.py              # 检查所有文件（不含 tests/）
    python scripts/check_code_patterns.py app.py       # 检查特定文件（显式指定则不排除）
    python scripts/check_code_patterns.py --fix        # 自动修复（如果可能）

扫描范围：递归扫当前目录，排除 .venv/ 、__pycache__/ 和 tests/。排除 tests/ 是必须的——
测试文件里会**故意**放坏模式当 fixture 数据（例如路径函数少写调用括号），
扫它们只会产生假阳性，把正常提交挡在门外。
"""
import re
import sys
from pathlib import Path
from typing import List, Tuple


def _read_text_safe(file_path: Path) -> str:
    """读文件内容；非 UTF-8（例如误扫进第三方 venv 里的测试 fixture）就跳过而不是让整个
    检查崩溃——一个读不了的文件不该挡住其他文件的检查结果。"""
    try:
        return file_path.read_text(encoding='utf-8')
    except (UnicodeDecodeError, OSError):
        return ""


class CodeIssue:
    """代码问题。"""
    def __init__(self, file_path: str, line_num: int, pattern: str, suggestion: str, severity: str = "error"):
        self.file_path = file_path
        self.line_num = line_num
        self.pattern = pattern
        self.suggestion = suggestion
        self.severity = severity

    def __str__(self):
        icon = "❌" if self.severity == "error" else "⚠️ "
        return f"{icon} {self.file_path}:{self.line_num}\n   Found: {self.pattern}\n   Fix: {self.suggestion}"


def check_path_function_usage(file_path: Path) -> List[CodeIssue]:
    """检查路径函数是否被正确使用。"""
    issues = []
    content = _read_text_safe(file_path)
    lines = content.split('\n')

    # config.py 中定义为函数的路径常量
    PATH_FUNCTIONS = [
        'RAW_DIR', 'PROCESSED_DIR', 'SUMMARIES_DIR', 'GRAPH_FRAGMENTS_DIR',
        'CHAT_SESSIONS_DIR', 'DB_DIR', 'GRAPH_JSON_PATH', 'CHAT_GRAPH_JSON_PATH',
        'LONG_TERM_MEMORY_PATH', 'CHAT_MEMORY_PATH', 'EXPLICIT_CACHE_STATE_PATH',
        'INDEX_CHANGELOG_PATH', 'CHUNKS_JSONL_PATH'
    ]

    # Path 对象的方法
    PATH_METHODS = ['exists', 'read_text', 'write_text', 'mkdir', 'parent', 'glob', 'iterdir']

    for i, line in enumerate(lines, 1):
        for path_func in PATH_FUNCTIONS:
            for method in PATH_METHODS:
                # 检查模式: PATH_FUNCTION.method (没有括号)
                pattern = f"{path_func}.{method}"
                if pattern in line and f"{path_func}(" not in line:
                    issues.append(CodeIssue(
                        str(file_path), i, pattern,
                        f"应该使用 {path_func}().{method}（{path_func} 是函数，需要先调用）",
                        "error"
                    ))

    return issues


def check_optional_in_docstring(file_path: Path) -> List[CodeIssue]:
    """检查 Optional 导入是否在文档字符串内（过于严格，暂时禁用）。"""
    # 这个检查经常误报，因为文档字符串后紧跟 import 语句
    # 而正则匹配跨度大，会匹配到正常的代码结构
    # TODO: 改进检测逻辑或完全移除此检查
    return []


def check_type_annotation_compatibility(file_path: Path) -> List[CodeIssue]:
    """类型注解兼容性检查（已停用）。

    历史上用于拦截 PEP 604 的 ``X | Y``（Python 3.9 不支持）。项目现钉
    ``requires-python = "==3.12.*"``，该语法合法，继续 hard-fail 只会挡住现代写法。

    保留函数签名与 main() 调用点，避免破坏既有 import；恒返回空。
    """
    return []


def check_missing_workspace_id(file_path: Path) -> List[CodeIssue]:
    """检查可能遗漏的 workspace_id 参数（仅警告）。"""
    issues = []

    # 只检查 app.py 和 pages/
    if not (file_path.name == 'app.py' or 'pages/' in str(file_path)):
        return issues

    content = _read_text_safe(file_path)
    lines = content.split('\n')

    # 需要 workspace_id 的函数
    WORKSPACE_FUNCTIONS = [
        'update_chat_memory', 'build_chat_graph', 'load_summaries',
        'update_memory', 'ensure_fragments', 'build_graph'
    ]

    for i, line in enumerate(lines, 1):
        for func in WORKSPACE_FUNCTIONS:
            # 查找函数调用但没有 workspace_id
            if f"{func}()" in line:
                issues.append(CodeIssue(
                    str(file_path), i,
                    f"{func}()",
                    f"考虑传递 workspace_id 参数",
                    "warning"
                ))

    return issues


def main():
    """主函数。"""
    # 获取要检查的文件
    if len(sys.argv) > 1 and sys.argv[1] != '--fix':
        files = [Path(arg) for arg in sys.argv[1:] if not arg.startswith('--')]
    else:
        # 检查所有 Python 文件
        files = list(Path('.').glob('**/*.py'))
        # 排除 .venv*（第三方代码，含历史上出现过的 .venv312 这类改名残留）、__pycache__
        # （缓存）、tests（故意包含坏模式当 fixture）
        EXCLUDED_PARTS = {'__pycache__', 'tests'}
        files = [
            f for f in files
            if not (EXCLUDED_PARTS & set(f.parts))
            and not any(part.startswith('.venv') for part in f.parts)
        ]

    all_issues = []

    for file_path in files:
        if not file_path.exists():
            continue

        issues = []
        issues.extend(check_path_function_usage(file_path))
        issues.extend(check_optional_in_docstring(file_path))
        issues.extend(check_type_annotation_compatibility(file_path))
        issues.extend(check_missing_workspace_id(file_path))

        all_issues.extend(issues)

    # 报告结果
    errors = [i for i in all_issues if i.severity == "error"]
    warnings = [i for i in all_issues if i.severity == "warning"]

    if errors:
        print("🚨 发现错误：\n")
        for issue in errors:
            print(issue)
            print()

    if warnings:
        print("⚠️  警告：\n")
        for issue in warnings:
            print(issue)
            print()

    # 总结
    if errors:
        print(f"❌ 检查失败：{len(errors)} 个错误，{len(warnings)} 个警告")
        sys.exit(1)
    elif warnings:
        print(f"⚠️  检查通过但有 {len(warnings)} 个警告")
        sys.exit(0)
    else:
        print("✅ 所有检查通过！")
        sys.exit(0)


if __name__ == "__main__":
    main()
