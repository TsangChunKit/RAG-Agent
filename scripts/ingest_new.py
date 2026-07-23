"""增量更新：新逐字稿加入时，一条命令完成 入库 + 摘要 + 记忆更新，无需重跑全量。

用法：python -m scripts.ingest_new <新文件路径> [--force] [--workspace <workspace_id>]

流程：parse → chunk → embed → 追加进 LanceDB（append，非重建）→ summarize（只处理这一份）
→ update_memory（汇总全部摘要，重新生成 LONG_TERM_MEMORY.md）。

幂等：如果该文件已经处理过（chunks.jsonl 里已有它的 chunk，或摘要 JSON 已存在），
默认跳过对应步骤；传 --force 强制重新处理。

支持 workspace：所有函数支持 workspace_id 参数。
"""
import json
import shutil
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from config import RAW_DIR
from scripts.chunk import CHUNKS_JSONL_PATH, chunk_session
from scripts.index_records import append_change_record
from scripts.ingest import ingest
from scripts.parse import parse_transcript
from scripts.summarize import summarize_session, summary_path
from scripts.update_memory import load_summaries, update_memory


def _ensure_in_raw_dir(path: Path, workspace_id: Optional[str] = None) -> Path:
    """确保文件在 raw/ 目录下（workspace 感知）。"""
    raw_dir = RAW_DIR(workspace_id)
    raw_dir.mkdir(parents=True, exist_ok=True)
    target = raw_dir / path.name
    if path.resolve() != target.resolve():
        shutil.copy2(path, target)
        print(f"已复制到 {target}")
    return target


def _existing_chunk_source_files(workspace_id: Optional[str] = None) -> set[str]:
    """获取已入库的文件列表（workspace 感知）。"""
    chunks_path = CHUNKS_JSONL_PATH(workspace_id)
    if not chunks_path.exists():
        return set()
    with open(chunks_path, encoding="utf-8") as f:
        return {json.loads(line)["source_file"] for line in f}


def ingest_new_file(path: Path, force: bool = False, workspace_id: Optional[str] = None) -> dict:
    """增量摄取单个文件（workspace 感知）。"""
    path = _ensure_in_raw_dir(path, workspace_id)
    session = parse_transcript(path)

    already_chunked = session.source_file in _existing_chunk_source_files(workspace_id)
    if already_chunked and not force:
        print(f"[跳过] {session.source_file} 已在向量库中，如需重新处理请加 --force")
        append_change_record(
            "skipped", session.source_file, session.session_date, note="已在向量库中，未重复入库",
            workspace_id=workspace_id
        )
    else:
        new_chunks = chunk_session(session, workspace_id=workspace_id)
        chunk_dicts = [asdict(c) for c in new_chunks]

        chunks_path = CHUNKS_JSONL_PATH(workspace_id)
        chunks_path.parent.mkdir(parents=True, exist_ok=True)
        with open(chunks_path, "a", encoding="utf-8") as f:
            for c in chunk_dicts:
                f.write(json.dumps(c, ensure_ascii=False) + "\n")

        ingest(chunks=chunk_dicts, mode="append", workspace_id=workspace_id)
        print(f"已追加 {len(new_chunks)} 个 chunk 到 LanceDB（{session.source_file}）")
        append_change_record(
            "reindexed" if already_chunked else "added",
            session.source_file,
            session.session_date,
            n_chunks=len(new_chunks),
            note="--force 重新入库" if already_chunked else "新逐字稿入库",
            workspace_id=workspace_id
        )

    out_path = summary_path(session.source_file, workspace_id)
    if out_path.exists() and not force:
        print(f"[跳过] {session.source_file} 摘要已存在")
        summary = json.loads(out_path.read_text(encoding="utf-8"))
    else:
        summary = summarize_session(session)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"已生成摘要 {out_path}")

    summaries = load_summaries(workspace_id)
    update_memory(summaries, workspace_id)
    print("已更新 LONG_TERM_MEMORY.md")

    return summary


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    force = "--force" in sys.argv
    workspace_id = None
    if "--workspace" in sys.argv:
        idx = sys.argv.index("--workspace")
        if idx + 1 < len(sys.argv):
            workspace_id = sys.argv[idx + 1]

    if not args:
        print("用法: python -m scripts.ingest_new <新文件路径> [--force] [--workspace <workspace_id>]")
        sys.exit(1)

    for a in args:
        ingest_new_file(Path(a), force=force, workspace_id=workspace_id)
