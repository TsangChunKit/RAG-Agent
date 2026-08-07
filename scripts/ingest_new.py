"""增量更新：新逐字稿加入时，一条命令完成 入库 + 摘要 + 记忆更新，无需重跑全量。

用法：python -m scripts.ingest_new <新文件路径> [--force] [--workspace <workspace_id>]

流程：parse → chunk → embed → 追加进 LanceDB（append，非重建）→ summarize（只处理这一份）
→ update_memory（汇总全部摘要，重新生成 LONG_TERM_MEMORY.md）。

幂等：如果该文件已经处理过（chunks.jsonl 里已有它的 chunk，或摘要 JSON 已存在），
默认跳过对应步骤；传 --force 强制重新处理。

支持 workspace：所有函数支持 workspace_id 参数。

`pending_raw_files()` 是「raw/ 里还有哪些没入库」这条判定的**单一真相源**：看门狗
（scripts/raw_ingest_watcher.py）和 UI 的「⚡ 立即入库」按钮都走它，避免两处各写一套规则、
哪天改了一处忘了另一处。`ingest_pending()` 是它的批量执行版（供 UI 按钮调用，返回结构化
结果给界面渲染，不打印）。
"""


from typing import Dict, List, Optional, Set
import json
import shutil
import sys
import time
from dataclasses import asdict
from pathlib import Path

from config import RAW_DIR
from scripts.chunk import CHUNKS_JSONL_PATH, chunk_session
from scripts.index_records import append_change_record, list_indexed_records
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

        # ⚠️ 顺序很重要：先入 LanceDB，成功了才把 chunk 写进 chunks.jsonl。
        # chunks.jsonl 是「哪些文件已入库」的真相源（pending_raw_files() 读它），先写它就等于
        # 提前宣布成功——2026-07-26 真实事故：ingest() 因 FTS 分词器配置报错，但 chunks.jsonl
        # 已经写完，那份逐字稿从此既不在库里、又不在待入库清单里，摘要/长期记忆/变更记录全没跑，
        # 且看门狗永远不会重试它。反过来（先入库后写清单）最坏只是下次重试一遍，是可恢复的。
        ingest(chunks=chunk_dicts, mode="append", workspace_id=workspace_id)

        chunks_path = CHUNKS_JSONL_PATH(workspace_id)
        chunks_path.parent.mkdir(parents=True, exist_ok=True)
        with open(chunks_path, "a", encoding="utf-8") as f:
            for c in chunk_dicts:
                f.write(json.dumps(c, ensure_ascii=False) + "\n")

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


def pending_raw_files(
    workspace_id: Optional[str] = None,
    stable_seconds: int = 0,
    skip: Optional[Set[str]] = None,
) -> List[Path]:
    """raw/ 里还没入库的 .txt 文件（按文件名排序，workspace 感知）。

    「没入库」= 文件名不在 chunks.jsonl 的 source_file 集合里。

    Args:
        workspace_id: workspace 名称，None = 当前 workspace
        stable_seconds: 只返回 mtime 已经稳定这么多秒的文件（>0 时生效）。看门狗传 30，
            避免抓到还在复制途中的半个文件；UI 手动触发传 0（用户点按钮 = 明确说已放好）。
        skip: 额外跳过的文件名集合（看门狗用它跳过已知永久失败的文件）

    Returns:
        待入库文件路径列表（raw 目录不存在时返回空列表）
    """
    raw_dir = RAW_DIR(workspace_id)
    if not raw_dir.exists():
        return []

    indexed = _existing_chunk_source_files(workspace_id)
    skip = skip or set()
    now = time.time()

    pending = []
    for p in sorted(raw_dir.glob("*.txt")):
        if p.name in indexed or p.name in skip:
            continue
        if stable_seconds and now - p.stat().st_mtime < stable_seconds:
            continue  # 可能还在写入
        pending.append(p)
    return pending


def ingest_pending(
    workspace_id: Optional[str] = None,
    force: bool = False,
    stable_seconds: int = 0,
) -> Dict[str, list]:
    """把 raw/ 里所有还没入库的逐字稿逐份入库（供 UI「⚡ 立即入库」按钮用）。

    一份失败不影响其余（原样报回给调用方渲染），因为一批里往往只有个别文件名不合规。

    Args:
        workspace_id: workspace 名称，None = 当前 workspace
        force: 传给 ingest_new_file()，强制重新处理
        stable_seconds: 传给 pending_raw_files()

    Returns:
        {"ingested": [文件名, ...], "failed": [{"file": 文件名, "error": 错误信息}, ...]}
    """
    result: Dict[str, list] = {"ingested": [], "failed": []}
    for p in pending_raw_files(workspace_id, stable_seconds=stable_seconds):
        try:
            ingest_new_file(p, force=force, workspace_id=workspace_id)
            result["ingested"].append(p.name)
        except Exception as e:  # noqa: BLE001 — 一份坏文件不该拖垮整批
            result["failed"].append({"file": p.name, "error": str(e)})
    return result


def missing_summary_files(workspace_id: Optional[str] = None) -> List[str]:
    """已入库但摘要 JSON 还没生成的 source_file 列表（workspace 感知）。

    是「补生成摘要」这条通路的单一真相源：直接复用 index_records.list_indexed_records()
    的 has_summary 判定（同一份 chunks.jsonl 现状快照），不重新发明一套判断逻辑。
    典型成因：入库时 chunks/向量化都成功了，但后面那步 LLM 摘要调用失败（比如 API key/
    OAuth 过期）——ingest_pending() 会把这份记进 failed，但 chunks 已经落了地，
    所以它不会再出现在 pending_raw_files() 里，得靠这个函数才找得回来。

    Args:
        workspace_id: workspace 名称，None = 当前 workspace

    Returns:
        缺摘要的 source_file 文件名列表
    """
    return [r["source_file"] for r in list_indexed_records(workspace_id) if not r["has_summary"]]


def regenerate_missing_summaries(workspace_id: Optional[str] = None) -> Dict[str, list]:
    """把已入库但缺摘要的文件逐份补生成摘要（UI「🔁 补生成摘要」按钮的后端）。

    只重跑摘要这一步，不碰 chunks/LanceDB（那些已经在库里了）——跟 ingest_pending()
    是同一种「批量 + 局部失败不拖累整批」的模式，失败信息原样返回给调用方渲染，
    不打印、不抛出。至少成功一份才会刷新长期记忆（update_memory 开销不小，全军覆没时不必跑）。

    Args:
        workspace_id: workspace 名称，None = 当前 workspace

    Returns:
        {"generated": [文件名, ...], "failed": [{"file": 文件名, "error": 错误信息}, ...]}
    """
    result: Dict[str, list] = {"generated": [], "failed": []}
    raw_dir = RAW_DIR(workspace_id)

    for source_file in missing_summary_files(workspace_id):
        try:
            session = parse_transcript(raw_dir / source_file)
            summary = summarize_session(session)
            out_path = summary_path(session.source_file, workspace_id)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
            append_change_record(
                "summary", session.source_file, session.session_date,
                note="补生成摘要", workspace_id=workspace_id,
            )
            result["generated"].append(source_file)
        except Exception as e:  # noqa: BLE001 — 一份坏文件不该拖垮整批
            result["failed"].append({"file": source_file, "error": str(e)})

    if result["generated"]:
        summaries = load_summaries(workspace_id)
        update_memory(summaries, workspace_id)

    return result


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
