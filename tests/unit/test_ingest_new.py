"""测试增量入库（scripts/ingest_new.py）。

这个模块的核心承诺是**幂等**：同一份逐字稿反复跑，不会重复入库、不会重复烧 LLM 摘要，
除非显式 --force。测试按「新文件 / 已入库 / --force」三条路径覆盖，并检查写入的
changelog action 是否正确（added / reindexed / skipped）。

所有下游（parse / chunk / ingest / summarize / update_memory）都 mock 掉——
这里测的是编排逻辑，不是它们各自的实现。
"""
import json
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from scripts import ingest_new as inew
from scripts.ingest_new import ingest_new_file


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
