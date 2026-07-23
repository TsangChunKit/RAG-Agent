"""后台看门狗：监控 private.nosync/data/raw/，一旦发现还没入库的新逐字稿，就自动跑
ingest_new_file()（入库 + 摘要 + 更新 LONG_TERM_MEMORY.md）——不用再手动敲 `ingest_new`。

和 scripts/chat_memory_watcher.py 一样是独立常驻进程，由 launchd 拉起（见 scripts/launchd/）。
手动前台调试：
    python -m scripts.raw_ingest_watcher [--workspace <workspace_id>]

判定"新文件"：文件名（= chunks.jsonl 里的 source_file）不在已索引集合里。ingest_new_file()
本身对已入库文件是幂等的（会跳过 chunk/摘要），但我们只对真正的新文件调用它，入库后它的
chunk 就进了 chunks.jsonl，下一轮不再是候选，所以不会重复触发 Gemini 摘要。

两个防呆：
  1) 文件可能还在复制/写入途中——只处理"最近 STABLE_SECONDS 秒内 mtime 没再变过"的文件
     （认为已写完），避免抓到半个文件就入库。
  2) 有的文件可能永远解析失败（比如文件名没有 14 位日期前缀）——记进内存里的 _failed，
     本进程生命周期内只报一次错、之后跳过，避免每轮刷屏（launchd 重启后会重置，可接受）。

支持 workspace：通过命令行参数指定 workspace。
"""
import json
import sys
import time
from pathlib import Path

from config import RAW_DIR
from scripts.chunk import CHUNKS_JSONL_PATH
from scripts.ingest_new import ingest_new_file

CHECK_INTERVAL_SECONDS = 120  # 每 2 分钟扫一次
STABLE_SECONDS = 30           # 文件 mtime 至少稳定这么久才认为写完、可以入库

# 本进程内已确认永久失败（解析/入库报错）的文件名，跳过不再重试
_failed: set[str] = set()


def _indexed_source_files(workspace_id: Optional[str] = None) -> set[str]:
    """
from typing import Optional
获取已入库的文件列表（workspace 感知）。"""
    chunks_path = CHUNKS_JSONL_PATH(workspace_id)
    if not chunks_path.exists():
        return set()
    with open(chunks_path, encoding="utf-8") as f:
        return {json.loads(line)["source_file"] for line in f}


def _pending_files(workspace_id: Optional[str] = None) -> list[Path]:
    """raw 目录里还没入库、且已写完（mtime 稳定）的 .txt 文件（workspace 感知）。"""
    raw_dir = RAW_DIR(workspace_id)
    if not raw_dir.exists():
        return []
    indexed = _indexed_source_files(workspace_id)
    now = time.time()
    pending = []
    for p in sorted(raw_dir.glob("*.txt")):
        if p.name in indexed or p.name in _failed:
            continue
        if now - p.stat().st_mtime < STABLE_SECONDS:
            continue  # 可能还在写入，等下一轮
        pending.append(p)
    return pending


def check_and_ingest(workspace_id: Optional[str] = None) -> int:
    """检查一次；返回本轮成功入库的新文件数（方便测试，workspace 感知）。"""
    pending = _pending_files(workspace_id)
    if not pending:
        return 0

    done = 0
    for p in pending:
        print(f"[raw_ingest_watcher] 发现新逐字稿，开始入库：{p.name}", flush=True)
        try:
            ingest_new_file(p, workspace_id=workspace_id)
            print(f"[raw_ingest_watcher] 已入库 {p.name}", flush=True)
            done += 1
        except Exception as e:  # noqa: BLE001
            _failed.add(p.name)
            print(f"[raw_ingest_watcher] 入库失败，已标记跳过（本进程内不再重试）：{p.name} — {e}", flush=True)
    return done


if __name__ == "__main__":
    workspace_id = None
    if "--workspace" in sys.argv:
        idx = sys.argv.index("--workspace")
        if idx + 1 < len(sys.argv):
            workspace_id = sys.argv[idx + 1]

    raw_dir = RAW_DIR(workspace_id)
    print(
        f"[raw_ingest_watcher] 启动，每 {CHECK_INTERVAL_SECONDS}s 扫一次 {raw_dir}，"
        f"发现新逐字稿自动入库（文件写完 {STABLE_SECONDS}s 后才处理）",
        flush=True,
    )
    while True:
        try:
            check_and_ingest(workspace_id)
        except Exception as e:  # noqa: BLE001
            print(f"[raw_ingest_watcher] 出错: {e}", flush=True)
        time.sleep(CHECK_INTERVAL_SECONDS)
