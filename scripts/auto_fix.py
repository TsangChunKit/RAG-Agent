#!/usr/bin/env python3
"""自动修复所有已知的代码模式错误。"""
import re
from pathlib import Path


def fix_optional_in_docstring(filepath):
    """修复 Optional 导入在文档字符串内的问题。"""
    content = filepath.read_text(encoding='utf-8')
    original = content

    # 检查是否有问题
    if '"""\n' not in content and '"""' not in content:
        return False

    # 移除文档字符串中的导入
    content = re.sub(r'("""[^"]*)\nfrom typing import Optional\n([^"]*""")', r'\1\n\2', content)

    # 确保在文档字符串后有导入
    if 'from typing import Optional' not in content and 'Optional[' in content:
        # 找到文档字符串结束位置
        match = re.search(r'"""[^"]*"""', content)
        if match:
            end_pos = match.end()
            # 在文档字符串后插入导入
            content = content[:end_pos] + '\nfrom typing import Optional\n' + content[end_pos:]

    if content != original:
        filepath.write_text(content, encoding='utf-8')
        print(f"✅ Fixed Optional import in {filepath}")
        return True
    return False


def fix_type_annotations(filepath):
    """修复 X | None -> Optional[X]"""
    content = filepath.read_text(encoding='utf-8')
    original = content

    lines = content.split('\n')
    fixed_lines = []
    for line in lines:
        if not line.strip().startswith('#'):
            # 各种 | None 模式
            line = re.sub(r'\bdict\s*\|\s*None\b', 'Optional[dict]', line)
            line = re.sub(r'\blist\s*\|\s*None\b', 'Optional[list]', line)
            line = re.sub(r'\bstr\s*\|\s*None\b', 'Optional[str]', line)
            line = re.sub(r'\bint\s*\|\s*None\b', 'Optional[int]', line)
        fixed_lines.append(line)

    content = '\n'.join(fixed_lines)

    if content != original:
        filepath.write_text(content, encoding='utf-8')
        print(f"✅ Fixed type annotations in {filepath}")
        return True
    return False


def fix_path_function_calls(filepath):
    """修复路径函数调用（PATH_CONSTANT.method -> PATH_CONSTANT().method）"""
    content = filepath.read_text(encoding='utf-8')
    original = content

    # 路径函数列表
    PATH_FUNCTIONS = [
        'GRAPH_JSON_PATH', 'CHAT_GRAPH_JSON_PATH', 'LONG_TERM_MEMORY_PATH',
        'CHAT_MEMORY_PATH', 'RAW_DIR', 'PROCESSED_DIR', 'SUMMARIES_DIR',
        'GRAPH_FRAGMENTS_DIR', 'CHAT_SESSIONS_DIR', 'DB_DIR',
        'EXPLICIT_CACHE_STATE_PATH', 'INDEX_CHANGELOG_PATH', 'CHUNKS_JSONL_PATH'
    ]

    for path_func in PATH_FUNCTIONS:
        # 替换模式：PATH_FUNC.method -> PATH_FUNC().method
        # 但要确保不是已经 PATH_FUNC() 的情况
        pattern = rf'\b({path_func})\.(\w+)'

        def replace(match):
            func_name = match.group(1)
            method_name = match.group(2)
            # 检查前面是否已经有括号
            return f'{func_name}().{method_name}'

        content = re.sub(pattern, replace, content)

    if content != original:
        filepath.write_text(content, encoding='utf-8')
        print(f"✅ Fixed path function calls in {filepath}")
        return True
    return False


def main():
    """批量修复所有问题。"""
    # 获取所有 Python 文件
    files = []
    files.extend(Path('.').glob('*.py'))
    files.extend(Path('scripts').glob('*.py'))
    files.extend(Path('pages').glob('*.py'))
    files.extend(Path('tests').glob('**/*.py'))

    # 排除 .venv
    files = [f for f in files if '.venv' not in str(f) and '__pycache__' not in str(f)]

    total_fixed = 0

    for filepath in files:
        if not filepath.exists():
            continue

        fixed = False
        fixed |= fix_optional_in_docstring(filepath)
        fixed |= fix_type_annotations(filepath)
        fixed |= fix_path_function_calls(filepath)

        if fixed:
            total_fixed += 1

    print(f"\n✅ 修复完成！共修改 {total_fixed} 个文件")

    # 再次运行检查
    print("\n🔍 验证修复结果...")
    import subprocess
    result = subprocess.run(['python3', 'scripts/check_code_patterns.py'], capture_output=True, text=True)
    if result.returncode == 0:
        print("✅ 所有检查通过！")
    else:
        print("⚠️  仍有问题，请查看详细输出：")
        print(result.stdout)


if __name__ == "__main__":
    main()
