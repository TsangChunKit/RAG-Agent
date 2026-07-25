"""测试逐字稿入库看门狗（scripts/raw_ingest_watcher.py）。

两个防呆是这个模块存在的理由，必须有测试守住：
  1. 只处理 mtime 已稳定 STABLE_SECONDS 的文件（否则会抓到半个正在复制的文件就入库）；
  2. 永久失败的文件记进 _failed，本进程内只报一次错（否则每 2 分钟刷屏一次）。

_failed 是模块级全局状态，每个测试都要清空，否则测试之间互相污染。
"""
import json
import os
import time

import pytest

from scripts import raw_ingest_watcher as w
from scripts.raw_ingest_watcher import (
    CHECK_INTERVAL_SECONDS,
    STABLE_SECONDS,
    check_and_ingest,
)


# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def clear_failed():
    """_failed 是模块级 set，测试间必须清空。"""
    w._failed.clear()
    yield
    w._failed.clear()


@pytest.fixture
def env(tmp_path, monkeypatch):
    """raw 目录 + chunks.jsonl 指到 tmp_path，ingest_new_file 全程 mock。"""
    raw_dir = tmp_path / "raw"
    chunks_path = tmp_path / "processed" / "chunks.jsonl"

    calls = []

    def fake_ingest(path, workspace_id=None):
        calls.append((path.name, workspace_id))
        return {"session_date": "2026-07-25"}

    monkeypatch.setattr(w, "RAW_DIR", lambda ws=None: raw_dir)
    monkeypatch.setattr(w, "CHUNKS_JSONL_PATH", lambda ws=None: chunks_path)
    monkeypatch.setattr(w, "ingest_new_file", fake_ingest)

    return {"raw": raw_dir, "chunks": chunks_path, "calls": calls}


def _make_txt(raw_dir, name, stable=True):
    """在 raw 目录放一个 txt；stable=True 时把 mtime 调老，模拟「已写完」。"""
    raw_dir.mkdir(parents=True, exist_ok=True)
    p = raw_dir / name
    p.write_text("Andy(00:00:01): 测试。", encoding="utf-8")
    if stable:
        old = time.time() - (STABLE_SECONDS + 60)
        os.utime(p, (old, old))
    return p


def _write_chunks(chunks_path, source_files):
    chunks_path.parent.mkdir(parents=True, exist_ok=True)
    with open(chunks_path, "w", encoding="utf-8") as f:
        for sf in source_files:
            f.write(json.dumps({"source_file": sf}) + "\n")


# ── 已索引集合 ─────────────────────────────────────────────────────────────


class TestIndexedSourceFiles:
    def test_empty_when_no_chunks_file(self, env):
        assert w._indexed_source_files() == set()

    def test_reads_source_files(self, env):
        _write_chunks(env["chunks"], ["a.txt", "a.txt", "b.txt"])

        assert w._indexed_source_files() == {"a.txt", "b.txt"}


# ── 候选文件 ───────────────────────────────────────────────────────────────


class TestPendingFiles:
    def test_empty_when_raw_dir_missing(self, env):
        assert w._pending_files() == []

    def test_finds_new_stable_txt(self, env):
        _make_txt(env["raw"], "20260725120000-新咨询.txt")

        assert [p.name for p in w._pending_files()] == ["20260725120000-新咨询.txt"]

    def test_skips_already_indexed(self, env):
        _make_txt(env["raw"], "old.txt")
        _write_chunks(env["chunks"], ["old.txt"])

        assert w._pending_files() == []

    def test_skips_file_still_being_written(self, env):
        """mtime 太新 → 可能还在复制，等下一轮。"""
        _make_txt(env["raw"], "copying.txt", stable=False)

        assert w._pending_files() == []

    def test_skips_known_failed(self, env):
        _make_txt(env["raw"], "broken.txt")
        w._failed.add("broken.txt")

        assert w._pending_files() == []

    def test_ignores_non_txt(self, env):
        _make_txt(env["raw"], "note.md")

        assert w._pending_files() == []

    def test_sorted_by_name(self, env):
        _make_txt(env["raw"], "b.txt")
        _make_txt(env["raw"], "a.txt")

        assert [p.name for p in w._pending_files()] == ["a.txt", "b.txt"]


# ── check_and_ingest ──────────────────────────────────────────────────────


class TestCheckAndIngest:
    def test_returns_zero_when_nothing_pending(self, env):
        assert check_and_ingest() == 0
        assert env["calls"] == []

    def test_ingests_pending_files(self, env):
        _make_txt(env["raw"], "a.txt")
        _make_txt(env["raw"], "b.txt")

        assert check_and_ingest() == 2
        assert [name for name, _ in env["calls"]] == ["a.txt", "b.txt"]

    def test_passes_workspace_id(self, env):
        _make_txt(env["raw"], "a.txt")

        check_and_ingest("counseling")

        assert env["calls"] == [("a.txt", "counseling")]

    def test_failure_is_recorded_and_not_retried(self, env, monkeypatch):
        """入库报错 → 计数不加、记进 _failed，下一轮不再重试（避免每 2 分钟刷屏）。"""
        _make_txt(env["raw"], "bad.txt")
        attempts = []

        def boom(path, workspace_id=None):
            attempts.append(path.name)
            raise ValueError("文件名没有 14 位日期前缀")

        monkeypatch.setattr(w, "ingest_new_file", boom)

        assert check_and_ingest() == 0
        assert "bad.txt" in w._failed

        assert check_and_ingest() == 0
        assert attempts == ["bad.txt"]  # 只试过一次

    def test_one_failure_does_not_block_others(self, env, monkeypatch):
        _make_txt(env["raw"], "a_bad.txt")
        _make_txt(env["raw"], "b_ok.txt")
        done = []

        def half_broken(path, workspace_id=None):
            if "bad" in path.name:
                raise RuntimeError("解析失败")
            done.append(path.name)

        monkeypatch.setattr(w, "ingest_new_file", half_broken)

        assert check_and_ingest() == 1
        assert done == ["b_ok.txt"]


class TestConstants:
    def test_stable_window_shorter_than_scan_interval(self):
        """稳定窗口要小于扫描间隔，否则新文件平白多等一轮。"""
        assert STABLE_SECONDS < CHECK_INTERVAL_SECONDS
