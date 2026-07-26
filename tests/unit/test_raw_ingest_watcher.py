"""测试逐字稿入库看门狗（scripts/raw_ingest_watcher.py）。

这个模块存在的理由是两个防呆，必须有测试守住：
  1. 只处理 mtime 已稳定 STABLE_SECONDS 的文件（否则会抓到半个正在复制的文件就入库）；
  2. 永久失败的文件记进 _failed，本进程内只报一次错（否则每 2 分钟刷屏一次）。

「哪些文件算待入库」的规则本身住在 scripts/ingest_new.pending_raw_files()（UI 按钮共用），
在 tests/unit/test_ingest_new.py 里测；这里只测看门狗自己的部分：是否把 STABLE_SECONDS
和 _failed 正确传下去、逐份入库、一份失败不拖垮其余。

_failed 是模块级全局状态，每个测试都要清空，否则测试之间互相污染。
"""
import os
import time

import pytest

from scripts import ingest_new as inew
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
    """raw 目录 + chunks.jsonl 指到 tmp_path，ingest_new_file 全程 mock。

    路径要 patch 在 scripts.ingest_new 上——pending_raw_files() 住在那个模块里，
    读的是它的模块全局。
    """
    raw_dir = tmp_path / "raw"
    chunks_path = tmp_path / "processed" / "chunks.jsonl"

    calls = []

    def fake_ingest(path, workspace_id=None):
        calls.append((path.name, workspace_id))
        return {"session_date": "2026-07-25"}

    monkeypatch.setattr(inew, "RAW_DIR", lambda ws=None: raw_dir)
    monkeypatch.setattr(inew, "CHUNKS_JSONL_PATH", lambda ws=None: chunks_path)
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

    def test_skips_file_still_being_written(self, env):
        """看门狗必须传 STABLE_SECONDS 下去，否则会抓到还在复制的半个文件。"""
        _make_txt(env["raw"], "copying.txt", stable=False)

        assert check_and_ingest() == 0
        assert env["calls"] == []

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
