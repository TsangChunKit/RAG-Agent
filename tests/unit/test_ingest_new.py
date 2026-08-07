"""测试增量入库（scripts/ingest_new.py）。

这个模块的核心承诺是**幂等**：同一份逐字稿反复跑，不会重复入库、不会重复烧 LLM 摘要，
除非显式 --force。测试按「新文件 / 已入库 / --force」三条路径覆盖，并检查写入的
changelog action 是否正确（added / reindexed / skipped）。

所有下游（parse / chunk / ingest / summarize / update_memory）都 mock 掉——
这里测的是编排逻辑，不是它们各自的实现。

`pending_raw_files()` / `ingest_pending()` 是「raw 里还有哪些没入库」这条规则的**单一真相源**，
看门狗（scripts/raw_ingest_watcher.py）和 UI 的「⚡ 立即入库」按钮都走它，所以规则本身
（已索引 / 还在写入 / 非 .txt / 排序）在这里测，看门狗那边只测它自己的 _failed 记忆。
"""
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from scripts import ingest_new as inew
from scripts.ingest_new import (
    ingest_new_file,
    ingest_pending,
    missing_summary_files,
    pending_raw_files,
    regenerate_missing_summaries,
)


# ── Fixtures ──────────────────────────────────────────────────────────────


@dataclass
class _FakeChunk:
    """asdict() 需要真正的 dataclass，MagicMock 不行。"""
    id: str


@pytest.fixture
def env(tmp_path, monkeypatch):
    """把路径指到 tmp_path，并 mock 全部下游步骤。"""
    raw_dir = tmp_path / "raw"
    chunks_path = tmp_path / "processed" / "chunks.jsonl"
    summaries_dir = tmp_path / "summaries"
    summaries_dir.mkdir()

    session = MagicMock()
    session.source_file = "20260725120000-咨询.txt"
    session.session_date = "2026-07-25"

    chunk = MagicMock()
    mocks = {
        "parse": MagicMock(return_value=session),
        # chunk_session 返回 dataclass 实例，被 asdict() 消费 → 用真的 dataclass
        "chunk": MagicMock(return_value=[_FakeChunk("c0"), _FakeChunk("c1")]),
        "ingest": MagicMock(),
        "record": MagicMock(),
        "summarize": MagicMock(return_value={"session_date": "2026-07-25", "topics": ["职业"]}),
        "update_memory": MagicMock(),
        "load_summaries": MagicMock(return_value=[{"session_date": "2026-07-25"}]),
    }

    monkeypatch.setattr(inew, "RAW_DIR", lambda ws=None: raw_dir)
    monkeypatch.setattr(inew, "CHUNKS_JSONL_PATH", lambda ws=None: chunks_path)
    monkeypatch.setattr(inew, "parse_transcript", mocks["parse"])
    monkeypatch.setattr(inew, "chunk_session", mocks["chunk"])
    monkeypatch.setattr(inew, "ingest", mocks["ingest"])
    monkeypatch.setattr(inew, "append_change_record", mocks["record"])
    monkeypatch.setattr(inew, "summarize_session", mocks["summarize"])
    monkeypatch.setattr(inew, "load_summaries", mocks["load_summaries"])
    monkeypatch.setattr(inew, "update_memory", mocks["update_memory"])
    monkeypatch.setattr(
        inew, "summary_path",
        lambda source_file, ws=None: summaries_dir / f"{Path(source_file).stem}.json",
    )

    return {
        "raw": raw_dir,
        "chunks": chunks_path,
        "summaries": summaries_dir,
        "session": session,
        "mocks": mocks,
        "tmp": tmp_path,
    }


def _incoming_file(tmp_path, name="20260725120000-咨询.txt"):
    """模拟一份刚从腾讯会议下载到别处的逐字稿。"""
    downloads = tmp_path / "downloads"
    downloads.mkdir(exist_ok=True)
    p = downloads / name
    p.write_text("Andy(00:00:01): 测试。", encoding="utf-8")
    return p


def _mark_already_chunked(chunks_path, source_file):
    chunks_path.parent.mkdir(parents=True, exist_ok=True)
    with open(chunks_path, "a", encoding="utf-8") as f:
        f.write(json.dumps({"source_file": source_file}) + "\n")


# ── _ensure_in_raw_dir ────────────────────────────────────────────────────


class TestEnsureInRawDir:
    def test_copies_external_file_into_raw(self, env):
        src = _incoming_file(env["tmp"])

        target = inew._ensure_in_raw_dir(src)

        assert target == env["raw"] / src.name
        assert target.read_text(encoding="utf-8") == src.read_text(encoding="utf-8")

    def test_file_already_in_raw_is_not_copied(self, env):
        env["raw"].mkdir(parents=True)
        p = env["raw"] / "a.txt"
        p.write_text("原样", encoding="utf-8")

        assert inew._ensure_in_raw_dir(p) == p
        assert p.read_text(encoding="utf-8") == "原样"

    def test_creates_raw_dir(self, env):
        assert not env["raw"].exists()

        inew._ensure_in_raw_dir(_incoming_file(env["tmp"]))

        assert env["raw"].is_dir()


# ── _existing_chunk_source_files ──────────────────────────────────────────


class TestExistingChunkSourceFiles:
    def test_empty_when_no_chunks_file(self, env):
        assert inew._existing_chunk_source_files() == set()

    def test_reads_unique_source_files(self, env):
        _mark_already_chunked(env["chunks"], "a.txt")
        _mark_already_chunked(env["chunks"], "a.txt")
        _mark_already_chunked(env["chunks"], "b.txt")

        assert inew._existing_chunk_source_files() == {"a.txt", "b.txt"}


# ── 新文件（完整路径）─────────────────────────────────────────────────────


class TestIngestNewFile:
    def test_new_file_full_pipeline(self, env):
        src = _incoming_file(env["tmp"])

        summary = ingest_new_file(src)

        m = env["mocks"]
        m["chunk"].assert_called_once()
        m["ingest"].assert_called_once()
        assert m["ingest"].call_args.kwargs["mode"] == "append"  # 追加，不重建全表
        m["summarize"].assert_called_once()
        m["update_memory"].assert_called_once()
        assert summary["topics"] == ["职业"]

    def test_appends_chunks_to_jsonl(self, env):
        ingest_new_file(_incoming_file(env["tmp"]))

        lines = env["chunks"].read_text(encoding="utf-8").strip().split("\n")
        assert [json.loads(x)["id"] for x in lines] == ["c0", "c1"]

    def test_writes_summary_json(self, env):
        ingest_new_file(_incoming_file(env["tmp"]))

        out = env["summaries"] / "20260725120000-咨询.json"
        assert json.loads(out.read_text(encoding="utf-8"))["topics"] == ["职业"]

    def test_records_added_action(self, env):
        ingest_new_file(_incoming_file(env["tmp"]))

        args, kwargs = env["mocks"]["record"].call_args
        assert args[0] == "added"
        assert kwargs["n_chunks"] == 2

    def test_passes_workspace_id_downstream(self, env):
        ingest_new_file(_incoming_file(env["tmp"]), workspace_id="counseling")

        m = env["mocks"]
        assert m["chunk"].call_args.kwargs["workspace_id"] == "counseling"
        assert m["ingest"].call_args.kwargs["workspace_id"] == "counseling"
        m["load_summaries"].assert_called_once_with("counseling")
        m["update_memory"].assert_called_once_with(m["load_summaries"].return_value, "counseling")


# ── 幂等 / --force ────────────────────────────────────────────────────────


class TestFailureLeavesNoPhantomIndex:
    """入库失败时不能留下"已索引"的假象。

    2026-07-26 真实事故：ingest() 因 FTS 分词器配置报错，但 chunks.jsonl 已经先写了 61 行
    → 该文件从此被当成"已入库"，pending_raw_files() 再也不返回它，看门狗和 UI 都不会重试，
    而摘要 / 长期记忆 / 变更记录全都没跑。所以 chunks.jsonl 必须在 ingest() **成功之后**才写。
    """

    def test_chunks_jsonl_not_written_when_ingest_fails(self, env):
        env["mocks"]["ingest"].side_effect = RuntimeError("lance error: unknown base tokenizer")

        with pytest.raises(RuntimeError):
            ingest_new_file(_incoming_file(env["tmp"]))

        assert not env["chunks"].exists()

    def test_file_still_pending_after_failed_ingest(self, env):
        """失败后它必须仍在待入库清单里，下一轮/点一次按钮能重试。"""
        env["mocks"]["ingest"].side_effect = RuntimeError("boom")

        with pytest.raises(RuntimeError):
            ingest_new_file(_incoming_file(env["tmp"]))

        assert [p.name for p in pending_raw_files()] == ["20260725120000-咨询.txt"]

    def test_no_change_record_when_ingest_fails(self, env):
        """变更记录也不能记 added——那会让「📚 已索引的咨询记录」显示一份并不存在的索引。"""
        env["mocks"]["ingest"].side_effect = RuntimeError("boom")

        with pytest.raises(RuntimeError):
            ingest_new_file(_incoming_file(env["tmp"]))

        env["mocks"]["record"].assert_not_called()


class TestIdempotency:
    def test_already_chunked_skips_ingest(self, env):
        """已在库中 → 不重复 chunk/入库，只记一条 skipped。"""
        _mark_already_chunked(env["chunks"], env["session"].source_file)

        ingest_new_file(_incoming_file(env["tmp"]))

        m = env["mocks"]
        m["chunk"].assert_not_called()
        m["ingest"].assert_not_called()
        assert m["record"].call_args[0][0] == "skipped"

    def test_existing_summary_is_reused_not_regenerated(self, env):
        """摘要已存在 → 读旧的，不再烧一次 LLM。"""
        out = env["summaries"] / "20260725120000-咨询.json"
        out.write_text(json.dumps({"topics": ["旧摘要"]}, ensure_ascii=False), encoding="utf-8")

        summary = ingest_new_file(_incoming_file(env["tmp"]))

        env["mocks"]["summarize"].assert_not_called()
        assert summary["topics"] == ["旧摘要"]

    def test_force_reindexes_and_regenerates(self, env):
        """--force：即使已入库、摘要已存在，也全部重做，action 记 reindexed。"""
        _mark_already_chunked(env["chunks"], env["session"].source_file)
        out = env["summaries"] / "20260725120000-咨询.json"
        out.write_text(json.dumps({"topics": ["旧摘要"]}, ensure_ascii=False), encoding="utf-8")

        summary = ingest_new_file(_incoming_file(env["tmp"]), force=True)

        m = env["mocks"]
        m["chunk"].assert_called_once()
        m["ingest"].assert_called_once()
        m["summarize"].assert_called_once()
        assert m["record"].call_args[0][0] == "reindexed"
        assert summary["topics"] == ["职业"]  # 新摘要覆盖旧的

    def test_memory_updated_even_when_everything_skipped(self, env):
        """记忆是全量重算的，跳过入库/摘要也要刷一次（摘要集合可能被别处改过）。"""
        _mark_already_chunked(env["chunks"], env["session"].source_file)
        (env["summaries"] / "20260725120000-咨询.json").write_text("{}", encoding="utf-8")

        ingest_new_file(_incoming_file(env["tmp"]))

        env["mocks"]["update_memory"].assert_called_once()


# ── 待入库清单（看门狗 + UI 按钮共用的真相源）────────────────────────────────


def _raw_txt(raw_dir, name, age_seconds=3600):
    """在 raw/ 放一个 txt；age_seconds 是把 mtime 往前调多久（模拟「写完多久了」）。"""
    raw_dir.mkdir(parents=True, exist_ok=True)
    p = raw_dir / name
    p.write_text("Andy(00:00:01): 测试。", encoding="utf-8")
    old = time.time() - age_seconds
    os.utime(p, (old, old))
    return p


class TestPendingRawFiles:
    def test_empty_when_raw_dir_missing(self, env):
        assert pending_raw_files() == []

    def test_finds_new_txt(self, env):
        _raw_txt(env["raw"], "20260726110034-新咨询.txt")

        assert [p.name for p in pending_raw_files()] == ["20260726110034-新咨询.txt"]

    def test_skips_already_indexed(self, env):
        _raw_txt(env["raw"], "old.txt")
        _mark_already_chunked(env["chunks"], "old.txt")

        assert pending_raw_files() == []

    def test_ignores_non_txt(self, env):
        _raw_txt(env["raw"], "note.md")

        assert pending_raw_files() == []

    def test_sorted_by_name(self, env):
        _raw_txt(env["raw"], "b.txt")
        _raw_txt(env["raw"], "a.txt")

        assert [p.name for p in pending_raw_files()] == ["a.txt", "b.txt"]

    def test_skip_set_is_excluded(self, env):
        """看门狗用它跳过已知永久失败的文件。"""
        _raw_txt(env["raw"], "broken.txt")
        _raw_txt(env["raw"], "ok.txt")

        assert [p.name for p in pending_raw_files(skip={"broken.txt"})] == ["ok.txt"]

    def test_stable_seconds_skips_file_still_being_written(self, env):
        """mtime 太新 → 可能还在复制，等下一轮（看门狗传 stable_seconds=30）。"""
        _raw_txt(env["raw"], "copying.txt", age_seconds=1)

        assert pending_raw_files(stable_seconds=30) == []

    def test_stable_seconds_zero_returns_even_brand_new_file(self, env):
        """UI 手动触发不等稳定窗口——用户点按钮就是明确说「这份已经放好了」。"""
        _raw_txt(env["raw"], "just_copied.txt", age_seconds=0)

        assert [p.name for p in pending_raw_files()] == ["just_copied.txt"]


# ── ingest_pending（UI「⚡ 立即入库」按钮的后端）──────────────────────────────


class TestIngestPending:
    def test_nothing_pending(self, env):
        assert ingest_pending() == {"ingested": [], "failed": []}

    def test_ingests_all_pending(self, env, monkeypatch):
        _raw_txt(env["raw"], "a.txt")
        _raw_txt(env["raw"], "b.txt")
        done = []
        monkeypatch.setattr(
            inew, "ingest_new_file",
            lambda p, force=False, workspace_id=None: done.append(p.name),
        )

        result = ingest_pending()

        assert done == ["a.txt", "b.txt"]
        assert result == {"ingested": ["a.txt", "b.txt"], "failed": []}

    def test_passes_workspace_and_force(self, env, monkeypatch):
        _raw_txt(env["raw"], "a.txt")
        calls = []
        monkeypatch.setattr(
            inew, "ingest_new_file",
            lambda p, force=False, workspace_id=None: calls.append((p.name, force, workspace_id)),
        )

        ingest_pending(workspace_id="counseling", force=True)

        assert calls == [("a.txt", True, "counseling")]

    def test_one_failure_does_not_block_others(self, env, monkeypatch):
        """一份坏文件不能拖垮整批——失败的原样报回去让 UI 显示。"""
        _raw_txt(env["raw"], "a_bad.txt")
        _raw_txt(env["raw"], "b_ok.txt")

        def half_broken(p, force=False, workspace_id=None):
            if "bad" in p.name:
                raise ValueError("文件名没有 14 位日期前缀")

        monkeypatch.setattr(inew, "ingest_new_file", half_broken)

        result = ingest_pending()

        assert result["ingested"] == ["b_ok.txt"]
        assert result["failed"] == [
            {"file": "a_bad.txt", "error": "文件名没有 14 位日期前缀"}
        ]


# ── 补摘要清单（已入库但摘要生成失败时的手动补跑通路）──────────────────────


def _indexed_record(source_file, session_date="2026-07-25", n_chunks=10, has_summary=False):
    return {
        "source_file": source_file,
        "session_date": session_date,
        "n_chunks": n_chunks,
        "has_summary": has_summary,
    }


class TestMissingSummaryFiles:
    def test_empty_when_all_have_summary(self, env, monkeypatch):
        monkeypatch.setattr(
            inew, "list_indexed_records",
            lambda ws=None: [_indexed_record("a.txt", has_summary=True)],
        )
        assert missing_summary_files() == []

    def test_filters_to_records_missing_summary(self, env, monkeypatch):
        monkeypatch.setattr(
            inew, "list_indexed_records",
            lambda ws=None: [
                _indexed_record("a.txt", has_summary=True),
                _indexed_record("b.txt", has_summary=False),
                _indexed_record("c.txt", has_summary=False),
            ],
        )
        assert missing_summary_files() == ["b.txt", "c.txt"]

    def test_empty_when_nothing_indexed(self, env, monkeypatch):
        monkeypatch.setattr(inew, "list_indexed_records", lambda ws=None: [])
        assert missing_summary_files() == []

    def test_passes_workspace_id_through(self, env, monkeypatch):
        seen = []
        monkeypatch.setattr(
            inew, "list_indexed_records",
            lambda ws=None: seen.append(ws) or [],
        )
        missing_summary_files(workspace_id="counseling")
        assert seen == ["counseling"]


class TestRegenerateMissingSummaries:
    """补摘要跟 ingest_pending() 是同一种「批量 + 局部失败不拖累整批」的模式，
    区别是只重跑摘要这一步，不碰 chunks/LanceDB（那些已经在库里了）。"""

    def test_nothing_missing(self, env, monkeypatch):
        monkeypatch.setattr(inew, "list_indexed_records", lambda ws=None: [])

        result = regenerate_missing_summaries()

        assert result == {"generated": [], "failed": []}
        env["mocks"]["update_memory"].assert_not_called()

    def test_generates_for_each_missing_file(self, env, monkeypatch):
        monkeypatch.setattr(
            inew, "list_indexed_records",
            lambda ws=None: [
                _indexed_record("a.txt", has_summary=False),
                _indexed_record("b.txt", has_summary=False),
            ],
        )

        result = regenerate_missing_summaries()

        assert result == {"generated": ["a.txt", "b.txt"], "failed": []}
        assert env["mocks"]["summarize"].call_count == 2
        env["mocks"]["update_memory"].assert_called_once()

    def test_writes_summary_json_to_disk(self, env, monkeypatch):
        monkeypatch.setattr(
            inew, "list_indexed_records",
            lambda ws=None: [_indexed_record("20260725120000-咨询.txt", has_summary=False)],
        )

        regenerate_missing_summaries()

        out = env["summaries"] / "20260725120000-咨询.json"
        assert out.exists()
        assert json.loads(out.read_text(encoding="utf-8"))["session_date"] == "2026-07-25"

    def test_records_summary_change_action(self, env, monkeypatch):
        monkeypatch.setattr(
            inew, "list_indexed_records",
            lambda ws=None: [_indexed_record("a.txt", has_summary=False)],
        )

        regenerate_missing_summaries()

        # 落地用 session.source_file/session.session_date（跟 ingest_new_file 一致的约定：
        # 以实际 parse 出来的会话为准，而不是清单里的文件名字符串）
        env["mocks"]["record"].assert_called_once_with(
            "summary", "20260725120000-咨询.txt", "2026-07-25",
            note="补生成摘要", workspace_id=None,
        )

    def test_calls_parse_transcript_with_raw_dir_path(self, env, monkeypatch):
        monkeypatch.setattr(
            inew, "list_indexed_records",
            lambda ws=None: [_indexed_record("20260725120000-咨询.txt", has_summary=False)],
        )

        regenerate_missing_summaries()

        env["mocks"]["parse"].assert_called_once_with(env["raw"] / "20260725120000-咨询.txt")

    def test_one_failure_does_not_block_others(self, env, monkeypatch):
        monkeypatch.setattr(
            inew, "list_indexed_records",
            lambda ws=None: [
                _indexed_record("bad.txt", has_summary=False),
                _indexed_record("ok.txt", has_summary=False),
            ],
        )

        def half_broken(path):
            if "bad" in path.name:
                raise RuntimeError("LLM 调用失败：401")
            return env["session"]

        monkeypatch.setattr(inew, "parse_transcript", half_broken)

        result = regenerate_missing_summaries()

        assert result["generated"] == ["ok.txt"]
        assert result["failed"] == [{"file": "bad.txt", "error": "LLM 调用失败：401"}]
        env["mocks"]["update_memory"].assert_called_once()  # 还有成功的一份，仍要刷新长期记忆

    def test_update_memory_not_called_when_all_fail(self, env, monkeypatch):
        monkeypatch.setattr(
            inew, "list_indexed_records",
            lambda ws=None: [_indexed_record("bad.txt", has_summary=False)],
        )
        monkeypatch.setattr(inew, "parse_transcript", MagicMock(side_effect=RuntimeError("401")))

        result = regenerate_missing_summaries()

        assert result == {"generated": [], "failed": [{"file": "bad.txt", "error": "401"}]}
        env["mocks"]["update_memory"].assert_not_called()

    def test_passes_workspace_id_through(self, env, monkeypatch):
        seen = {}

        def _list_indexed_records(ws=None):
            seen["list_ws"] = ws
            return [_indexed_record("a.txt", has_summary=False)]

        monkeypatch.setattr(inew, "list_indexed_records", _list_indexed_records)

        regenerate_missing_summaries(workspace_id="counseling")

        assert seen["list_ws"] == "counseling"
        env["mocks"]["load_summaries"].assert_called_once_with("counseling")
        env["mocks"]["update_memory"].assert_called_once_with(
            env["mocks"]["load_summaries"].return_value, "counseling"
        )
