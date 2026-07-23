#!/usr/bin/env python3
"""代码模式检查工具 - 在 commit 前自动运行。

捕获常见错误模式：
1. 路径函数被当作 Path 对象使用
2. 缺少 workspace_id 参数
3. Optional 导入在文档字符串内
4. Python 3.9 不兼容的类型注解

用法：
    python scripts/check_code_patterns.py              # 检查所有文件
    python scripts/check_code_patterns.py app.py       # 检查特定文件
    python scripts/check_code_patterns.py --fix        # 自动修复（如果可能）
"""
import re
import sys
from pathlib import Path
from typing import List, Tuple


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
    content = file_path.read_text(encoding='utf-8')
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
    """检查 Optional 导入是否在文档字符串内。"""
    issues = []
    content = file_path.read_text(encoding='utf-8')

    # 检查文档字符串中是否有 "from typing import Optional"
    match = re.search(r'"""[^"]*\nfrom typing import Optional\n[^"]*"""', content, re.MULTILINE)
    if match:
        line_num = content[:match.start()].count('\n') + 2
        issues.append(CodeIssue(
            str(file_path), line_num,
            "from typing import Optional (在文档字符串内)",
            "将导入移到文档字符串后面",
            "error"
        ))

    return issues


def check_type_annotation_compatibility(file_path: Path) -> List[CodeIssue]:
    """检查 Python 3.9 不兼容的类型注解。"""
    issues = []
    content = file_path.read_text(encoding='utf-8')
    lines = content.split('\n')

    # Python 3.9 不支持 PEP 604 (X | Y) 语法
    type_union_pattern = re.compile(r'\b(dict|list|str|int|float|bool)\s*\|\s*None\b')

    for i, line in enumerate(lines, 1):
        # 跳过注释
        if line.strip().startswith('#'):
            continue

        match = type_union_pattern.search(line)
        if match:
            issues.append(CodeIssue(
                str(file_path), i,
                match.group(0),
                f"Python 3.9 不支持。使用 Optional[{match.group(1)}] 替代",
                "error"
            ))

    return issues


def check_missing_workspace_id(file_path: Path) -> List[CodeIssue]:
    """检查可能遗漏的 workspace_id 参数（仅警告）。"""
    issues = []

    # 只检查 app.py 和 pages/
    if not (file_path.name == 'app.py' or 'pages/' in str(file_path)):
        return issues

    content = file_path.read_text(encoding='utf-8')
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
        # 排除 .venv, tests, __pycache__
        files = [f for f in files if '.venv' not in str(f) and '__pycache__' not in str(f)]

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
