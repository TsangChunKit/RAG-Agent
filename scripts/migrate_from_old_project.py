#!/usr/bin/env python3
"""
从旧项目（心理咨詢agent）迁移数据到新项目的 workspace。

迁移内容：
- data/raw/          逐字稿原始文件
- data/processed/    处理后的文件
- data/summaries/    摘要文件
- data/graph_fragments/  图谱片段
- data/graph.json    主图谱
- data/chat_graph.json   对话图谱
- data/chat_sessions/    对话历史（重要！）
- data/index_changelog.jsonl  索引变更记录
- db/                向量数据库
- LONG_TERM_MEMORY.md   长期记忆
- CHAT_MEMORY.md        对话记忆
"""
import json
import shutil
from pathlib import Path
from datetime import datetime


# 配置
SOURCE_PROJECT = Path("/Users/andytsang/Documents/Project/心理咨詢agent")
TARGET_PROJECT = Path("/Users/andytsang/Documents/Project/RAG-Agent")
WORKSPACE_NAME = "counseling"
WORKSPACE_DISPLAY_NAME = "心理咨询"


def main():
    print("=" * 60)
    print("🔄 数据迁移工具")
    print("=" * 60)
    print()
    print(f"📂 源项目: {SOURCE_PROJECT}")
    print(f"📂 目标项目: {TARGET_PROJECT}")
    print(f"🗂️  新 Workspace: {WORKSPACE_NAME} ({WORKSPACE_DISPLAY_NAME})")
    print()

    # 1. 验证源路径
    source_private = SOURCE_PROJECT / "private.nosync"
    if not source_private.exists():
        print("❌ 错误：源项目 private.nosync 不存在")
        return

    source_data = source_private / "data"
    source_db = source_private / "db"

    if not source_data.exists():
        print("❌ 错误：源数据目录不存在")
        return

    print("✅ 源路径验证通过")
    print()

    # 2. 创建目标 workspace 目录
    target_private = TARGET_PROJECT / "private.nosync"
    target_workspaces = target_private / "workspaces"
    target_ws = target_workspaces / WORKSPACE_NAME

    if target_ws.exists():
        print(f"⚠️  警告：Workspace {WORKSPACE_NAME} 已存在")
        response = input("是否覆盖？(yes/no): ")
        if response.lower() != "yes":
            print("❌ 迁移取消")
            return
        print(f"🗑️  删除旧 workspace...")
        shutil.rmtree(target_ws)

    print(f"📁 [1/5] 创建 workspace 目录结构...")
    target_ws.mkdir(parents=True, exist_ok=True)
    (target_ws / "data").mkdir(exist_ok=True)
    (target_ws / "db").mkdir(exist_ok=True)
    print("   ✅ 目录创建完成")
    print()

    # 3. 复制数据目录
    print(f"📋 [2/5] 复制数据目录...")
    data_items = [
        ("raw", "原始逐字稿"),
        ("processed", "处理后的文件"),
        ("summaries", "摘要文件"),
        ("graph_fragments", "图谱片段"),
        ("chat_sessions", "对话历史"),
        ("incoming", "incoming 目录"),
    ]

    for item, desc in data_items:
        source_item = source_data / item
        target_item = target_ws / "data" / item

        if source_item.exists():
            if source_item.is_dir():
                shutil.copytree(source_item, target_item, dirs_exist_ok=True)
                file_count = len(list(target_item.rglob("*")))
                print(f"   ✅ {desc}: {file_count} 个文件")
            else:
                shutil.copy2(source_item, target_item)
                print(f"   ✅ {desc}")
        else:
            print(f"   ⚠️  {desc}: 不存在，跳过")

    # 复制 JSON 文件
    json_files = [
        ("graph.json", "主图谱"),
        ("chat_graph.json", "对话图谱"),
        ("index_changelog.jsonl", "索引变更记录"),
    ]

    for filename, desc in json_files:
        source_file = source_data / filename
        target_file = target_ws / "data" / filename

        if source_file.exists():
            shutil.copy2(source_file, target_file)
            size = source_file.stat().st_size / 1024
            print(f"   ✅ {desc}: {size:.1f} KB")
        else:
            print(f"   ⚠️  {desc}: 不存在，跳过")

    print()

    # 4. 复制向量数据库
    print(f"🗄️  [3/5] 复制向量数据库...")
    if source_db.exists():
        shutil.copytree(source_db, target_ws / "db", dirs_exist_ok=True)
        db_size = sum(f.stat().st_size for f in (target_ws / "db").rglob("*") if f.is_file())
        print(f"   ✅ 数据库复制完成: {db_size / 1024 / 1024:.1f} MB")
    else:
        print("   ⚠️  数据库不存在，跳过")
    print()

    # 5. 复制记忆文件
    print(f"📝 [4/5] 复制记忆文件...")
    memory_files = [
        ("LONG_TERM_MEMORY.md", "长期记忆"),
        ("CHAT_MEMORY.md", "对话记忆"),
    ]

    for filename, desc in memory_files:
        source_file = source_private / filename
        target_file = target_ws / filename

        if source_file.exists():
            shutil.copy2(source_file, target_file)
            lines = len(source_file.read_text(encoding="utf-8").splitlines())
            print(f"   ✅ {desc}: {lines} 行")
        else:
            print(f"   ⚠️  {desc}: 不存在，跳过")
    print()

    # 6. 创建 workspace 配置
    print(f"⚙️  [5/5] 创建 workspace 配置...")
    config = {
        "name": WORKSPACE_NAME,
        "display_name": WORKSPACE_DISPLAY_NAME,
        "description": "从旧项目迁移的心理咨询知识库",
        "domain": "counseling",
        "graph_schema": {
            "mode": "predefined",
            "schema_file": "counseling.json"
        },
        "persona": {
            "system_instruction_file": None,
            "ai_name": "AI 心理咨询助手",
            "context_role": "咨询师海特"
        },
        "chunk_prefix_template": "[{session_date} {domain_label}｜发言人：{speakers}｜时间段：{start_ts}–{end_ts}]",
        "domain_label": "咨询",
        "created_at": datetime.now().isoformat(),
        "migrated_from": str(SOURCE_PROJECT),
    }

    config_file = target_ws / ".workspace_config.json"
    config_file.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"   ✅ 配置文件已创建")
    print()

    # 7. 统计信息
    print("=" * 60)
    print("✨ 迁移完成！")
    print("=" * 60)
    print()
    print("📊 迁移统计：")

    raw_count = len(list((target_ws / "data" / "raw").glob("*.txt"))) if (target_ws / "data" / "raw").exists() else 0
    chat_count = len(list((target_ws / "data" / "chat_sessions").glob("*.json"))) if (target_ws / "data" / "chat_sessions").exists() else 0
    summary_count = len(list((target_ws / "data" / "summaries").glob("*.json"))) if (target_ws / "data" / "summaries").exists() else 0

    print(f"   📄 逐字稿: {raw_count} 份")
    print(f"   💬 对话历史: {chat_count} 个会话")
    print(f"   📝 摘要: {summary_count} 份")

    if (target_ws / "data" / "graph.json").exists():
        graph = json.loads((target_ws / "data" / "graph.json").read_text())
        print(f"   🕸️  图谱节点: {len(graph.get('nodes', []))} 个")
        print(f"   🕸️  图谱边: {len(graph.get('edges', []))} 条")

    total_size = sum(f.stat().st_size for f in target_ws.rglob("*") if f.is_file())
    print(f"   💾 总大小: {total_size / 1024 / 1024:.1f} MB")
    print()
    print("📍 Workspace 位置:")
    print(f"   {target_ws}")
    print()
    print("🎯 下一步：")
    print("   1. 重启 UI: ./scripts/restart_ui.sh")
    print(f"   2. 在侧边栏切换到「{WORKSPACE_DISPLAY_NAME}」")
    print("   3. 开始使用你的心理咨询知识库！")
    print()


if __name__ == "__main__":
    main()
