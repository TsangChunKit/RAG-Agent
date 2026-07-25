"""测试已索引记录清单与变更 changelog（scripts/index_records.py）。

这个模块是「📚 已索引的记录」UI 的数据源，纯文件 I/O、无外部依赖，
所以测试全部用 tmp_path + monkeypatch 替换 config 的路径函数，不碰真实 workspace。

重点测试：
1. list_indexed_records() - 按 source_file 聚合 chunks.jsonl、日期倒序、has_summary
2. append_change_record() - 追加 JSONL 审计记录
3. load_change_log() - 倒序读取、limit 截断、坏行跳过
"""
import json

import pytest

from scripts import index_records
from scripts.index_records import (
    ACTION_LABELS,
    append_change_record,
    list_indexed_records,
    load_change_log,
)


# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def paths(tmp_path, monkeypatch):
    """把 index_records 用到的三个路径函数指到 tmp_path。

    注意 CHUNKS_JSONL_PATH 是在函数内部 `from scripts.chunk import ...` 的，
    所以要 patch 源模块 scripts.chunk，而不是 index_records。
    """
    chunks_path = tmp_path / "processed" / "chunks.jsonl"
    changelog_path = tmp_path / "index_changelog.jsonl"
    summaries_dir = tmp_path / "summaries"
    summaries_dir.mkdir()

    import scripts.chunk

    monkeypatch.setattr(scripts.chunk, "CHUNKS_JSONL_PATH", lambda ws=None: chunks_path)
    monkeypatch.setattr(index_records, "INDEX_CHANGELOG_PATH", lambda ws=None: changelog_path)
    monkeypatch.setattr(index_records, "SUMMARIES_DIR", lambda ws=None: summaries_dir)

    return {
        "chunks": chunks_path,
        "changelog": changelog_path,
        "summaries": summaries_dir,
    }


def _write_chunks(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


# ── list_indexed_records ──────────────────────────────────────────────────


class TestListIndexedRecords:
    """已索引记录清单。"""

    def test_no_chunks_file_returns_empty(self, paths):
        """chunks.jsonl 还没生成 → 空列表（不是抛错）。"""
        assert list_indexed_records() == []

    def test_aggregates_chunks_by_source_file(self, paths):
        """同一个 source_file 的多个 chunk 聚合成一条，n_chunks 累加。"""
        _write_chunks(paths["chunks"], [
            {"source_file": "a.txt", "session_date": "2026-01-01"},
            {"source_file": "a.txt", "session_date": "2026-01-01"},
            {"source_file": "b.txt", "session_date": "2026-02-01"},
        ])

        records = list_indexed_records()

        assert len(records) == 2
        by_file = {r["source_file"]: r for r in records}
        assert by_file["a.txt"]["n_chunks"] == 2
        assert by_file["b.txt"]["n_chunks"] == 1

    def test_sorted_by_date_desc(self, paths):
        """新的咨询在前（UI 期望倒序）。"""
        _write_chunks(paths["chunks"], [
            {"source_file": "old.txt", "session_date": "2026-01-01"},
            {"source_file": "new.txt", "session_date": "2026-06-01"},
        ])

        records = list_indexed_records()

        assert [r["source_file"] for r in records] == ["new.txt", "old.txt"]

    def test_has_summary_flag(self, paths):
        """摘要 JSON 存在 → has_summary=True（按文件名 stem 匹配）。"""
        _write_chunks(paths["chunks"], [
            {"source_file": "done.txt", "session_date": "2026-01-01"},
            {"source_file": "todo.txt", "session_date": "2026-01-02"},
        ])
        (paths["summaries"] / "done.json").write_text("{}", encoding="utf-8")

        by_file = {r["source_file"]: r for r in list_indexed_records()}

        assert by_file["done.txt"]["has_summary"] is True
        assert by_file["todo.txt"]["has_summary"] is False

    def test_skips_blank_lines(self, paths):
        """空行不算一个 chunk。"""
        paths["chunks"].parent.mkdir(parents=True, exist_ok=True)
        paths["chunks"].write_text(
            json.dumps({"source_file": "a.txt", "session_date": "2026-01-01"}) + "\n\n   \n",
            encoding="utf-8",
        )

        records = list_indexed_records()

        assert len(records) == 1
        assert records[0]["n_chunks"] == 1

    def test_missing_session_date_defaults_to_empty(self, paths):
        """老数据没有 session_date → 空串，不抛 KeyError。"""
        _write_chunks(paths["chunks"], [{"source_file": "a.txt"}])

        assert list_indexed_records()[0]["session_date"] == ""


# ── changelog ─────────────────────────────────────────────────────────────


class TestChangeLog:
    """索引变更记录（append-only 审计日志）。"""

    def test_append_creates_file_and_returns_record(self, paths):
        rec = append_change_record("added", "a.txt", "2026-01-01", n_chunks=7, note="新逐字稿入库")

        assert rec["action"] == "added"
        assert rec["source_file"] == "a.txt"
        assert rec["n_chunks"] == 7
        assert rec["note"] == "新逐字稿入库"
        assert "ts" in rec
        assert paths["changelog"].exists()

        written = json.loads(paths["changelog"].read_text(encoding="utf-8").strip())
        assert written == rec

    def test_append_is_additive(self, paths):
        """第二次写不覆盖第一次。"""
        append_change_record("added", "a.txt")
        append_change_record("skipped", "b.txt")

        lines = paths["changelog"].read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 2

    def test_defaults(self, paths):
        """只给 action + source_file 也能写（其余字段有默认值）。"""
        rec = append_change_record("full_rebuild", "all")

        assert rec["session_date"] == ""
        assert rec["n_chunks"] == 0
        assert rec["note"] == ""

    def test_load_missing_file_returns_empty(self, paths):
        assert load_change_log() == []

    def test_load_newest_first(self, paths):
        append_change_record("added", "first.txt")
        append_change_record("added", "second.txt")

        entries = load_change_log()

        assert [e["source_file"] for e in entries] == ["second.txt", "first.txt"]

    def test_load_respects_limit(self, paths):
        for i in range(5):
            append_change_record("added", f"f{i}.txt")

        entries = load_change_log(limit=2)

        assert len(entries) == 2
        assert entries[0]["source_file"] == "f4.txt"

    def test_load_skips_corrupted_lines(self, paths):
        """半行/坏 JSON 不能让整份日志读不出来。"""
        append_change_record("added", "good.txt")
        with open(paths["changelog"], "a", encoding="utf-8") as f:
            f.write("{not json}\n")
            f.write("\n")

        entries = load_change_log()

        assert len(entries) == 1
        assert entries[0]["source_file"] == "good.txt"

    def test_action_labels_cover_written_actions(self):
        """ingest_new / ingest 里写入的 action 都要有中文标签，否则 UI 显示空白。"""
        for action in ("added", "reindexed", "skipped", "full_rebuild", "summary"):
            assert action in ACTION_LABELS
