"""已索引记录的清单 + 索引变更记录（changelog），供 Streamlit「📚 已索引的记录」UI 用。

两件事：
1. list_indexed_records()：向量库里当前有哪些文档（按 source_file 聚合 chunks.jsonl，
   给出日期、片段数、是否已生成摘要），是"现状快照"。
2. append_change_record() / load_change_log()：每次新增/重建/跳过入库时追加一行审计记录到
   INDEX_CHANGELOG_PATH（append-only JSONL），是"变更历史"。

真相源：chunks.jsonl（分块产物）就是"已索引内容"的权威列表——LanceDB 的行就是从它来的。
不额外查 LanceDB，避免在只想看清单时也去加载向量库依赖。

支持 workspace：所有函数支持 workspace_id 参数。
"""
import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from config import INDEX_CHANGELOG_PATH, PROCESSED_DIR, SUMMARIES_DIR

# 变更动作 -> 中文展示标签
ACTION_LABELS = {
    "added": "➕ 新增入库",
    "reindexed": "♻️ 重新处理（--force）",
    "skipped": "⏭️ 跳过（已在库中）",
    "full_rebuild": "🔄 全量重建",
    "summary": "📝 生成摘要",
}


def _summary_exists(source_file: str, workspace_id: Optional[str] = None) -> bool:
    """摘要 JSON 是否已生成（workspace 感知）。"""
    return (SUMMARIES_DIR(workspace_id) / f"{Path(source_file).stem}.json").exists()


def list_indexed_records(workspace_id: Optional[str] = None) -> list[dict]:
    """读 chunks.jsonl，按 source_file 聚合成已索引记录列表（workspace 感知）。

    每条：{source_file, session_date, n_chunks, has_summary}，按日期倒序（新的在前）。
    """
    from scripts.chunk import CHUNKS_JSONL_PATH

    chunks_path = CHUNKS_JSONL_PATH(workspace_id)
    if not chunks_path.exists():
        return []

    agg: dict[str, dict] = {}
    with open(chunks_path, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            c = json.loads(line)
            sf = c["source_file"]
            entry = agg.setdefault(
                sf, {"source_file": sf, "session_date": c.get("session_date", ""), "n_chunks": 0}
            )
            entry["n_chunks"] += 1

    records = list(agg.values())
    for e in records:
        e["has_summary"] = _summary_exists(e["source_file"], workspace_id)
    records.sort(key=lambda r: (r["session_date"], r["source_file"]), reverse=True)
    return records


def append_change_record(
    action: str,
    source_file: str,
    session_date: str = "",
    n_chunks: int = 0,
    note: str = "",
    workspace_id: Optional[str] = None
) -> dict:
    """追加一条索引变更记录（workspace 感知）。

    action ∈ ACTION_LABELS 的键。返回写入的记录 dict。
    """
    rec = {
        "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
        "action": action,
        "source_file": source_file,
        "session_date": session_date,
        "n_chunks": n_chunks,
        "note": note,
    }
    changelog_path = INDEX_CHANGELOG_PATH(workspace_id)
    changelog_path.parent.mkdir(parents=True, exist_ok=True)
    with open(changelog_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def load_change_log(limit: int = 50, workspace_id: Optional[str] = None) -> list[dict]:
    """读取最近 limit 条变更记录（workspace 感知），最新的在前。"""
    changelog_path = INDEX_CHANGELOG_PATH(workspace_id)
    if not changelog_path.exists():
        return []
    entries: list[dict] = []
    with open(changelog_path, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    entries.reverse()
    return entries[:limit]
