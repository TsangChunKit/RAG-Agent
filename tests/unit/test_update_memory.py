"""测试长期记忆生成（scripts/update_memory.py）。

关键契约：头部的「更新时间 / 已纳入次数 / 日期范围」是代码算出来写死的，不问 LLM
（避免它编错数字），LLM 只负责正文。测试要把这条守住。

重点测试：
1. load_summaries() - 按 session_date 排序加载
2. _format_summaries() - 喂给 LLM 的文本格式
3. generate_memory_body() - 用 dialogue 模型 + summary profile
4. update_memory() - 头部数字/日期范围、写文件
"""
import datetime
import json
from unittest.mock import MagicMock

import pytest

from scripts import update_memory as um
from scripts.update_memory import (
    SYSTEM_INSTRUCTION,
    generate_memory_body,
    load_summaries,
    update_memory,
)


# ── Fixtures ──────────────────────────────────────────────────────────────


def _summary(date, topic="职业转换"):
    return {
        "session_date": date,
        "source_file": f"{date.replace('-', '')}000000-x.txt",
        "topics": [topic, "消费心理"],
        "emotional_tone": "整体正向",
        "key_events": ["第二轮面试顺利"],
        "psychological_themes": ["灾难化想象"],
        "decisions_or_actions": ["下周谈待遇"],
        "quotes_worth_remembering": ["阻碍我的未必是换工作本身"],
    }


@pytest.fixture
def paths(tmp_path, monkeypatch):
    """把摘要目录和 LONG_TERM_MEMORY.md 指到 tmp_path。"""
    summaries_dir = tmp_path / "summaries"
    summaries_dir.mkdir()
    ltm_path = tmp_path / "LONG_TERM_MEMORY.md"

    monkeypatch.setattr(um, "SUMMARIES_DIR", lambda ws=None: summaries_dir)
    monkeypatch.setattr(um, "LONG_TERM_MEMORY_PATH", lambda ws=None: ltm_path)

    return {"summaries": summaries_dir, "ltm": ltm_path}


@pytest.fixture
def mock_llm(monkeypatch):
    """Mock ask_llm + settings，避免真实 LLM 调用。"""
    resp = MagicMock()
    resp.text = "  ## 核心议题（反复出现）\n- 职业转换  "
    mock = MagicMock(return_value=resp)

    monkeypatch.setattr(um, "ask_llm", mock)
    monkeypatch.setattr(um, "dialogue_params", lambda: {"model": "grok-4.5"})
    monkeypatch.setattr(um, "summary_max_tokens", lambda kind: 30000)

    return mock


# ── load_summaries ────────────────────────────────────────────────────────


class TestLoadSummaries:
    """摘要加载。"""

    def test_empty_dir_returns_empty_list(self, paths):
        assert load_summaries() == []

    def test_loads_and_sorts_by_session_date(self, paths):
        """文件名顺序不等于日期顺序，必须按 session_date 排。"""
        (paths["summaries"] / "zzz.json").write_text(
            json.dumps(_summary("2026-01-01")), encoding="utf-8"
        )
        (paths["summaries"] / "aaa.json").write_text(
            json.dumps(_summary("2026-06-01")), encoding="utf-8"
        )

        summaries = load_summaries()

        assert [s["session_date"] for s in summaries] == ["2026-01-01", "2026-06-01"]

    def test_ignores_non_json_files(self, paths):
        (paths["summaries"] / "a.json").write_text(json.dumps(_summary("2026-01-01")), encoding="utf-8")
        (paths["summaries"] / "notes.txt").write_text("不是摘要", encoding="utf-8")

        assert len(load_summaries()) == 1


# ── _format_summaries ─────────────────────────────────────────────────────


class TestFormatSummaries:
    """喂给 LLM 的摘要文本。"""

    def test_contains_all_fields(self):
        text = um._format_summaries([_summary("2026-06-27")])

        assert "[2026-06-27]" in text
        assert "职业转换、消费心理" in text  # topics 用「、」连接
        assert "整体正向" in text
        assert "第二轮面试顺利" in text
        assert "灾难化想象" in text
        assert "下周谈待遇" in text
        assert "阻碍我的未必是换工作本身" in text

    def test_blocks_separated_by_blank_line(self):
        text = um._format_summaries([_summary("2026-01-01"), _summary("2026-02-01")])

        assert "\n\n" in text
        assert text.count("主题：") == 2

    def test_empty_input(self):
        assert um._format_summaries([]) == ""


# ── generate_memory_body ──────────────────────────────────────────────────


class TestGenerateMemoryBody:
    """正文生成（LLM 调用参数）。"""

    def test_uses_dialogue_model_with_summary_profile(self, mock_llm):
        """长文+强指令任务：走 summary profile，但显式换成更强的对话模型。"""
        generate_memory_body([_summary("2026-01-01")])

        _, kwargs = mock_llm.call_args
        assert kwargs["profile"] == "summary"
        assert kwargs["model"] == "grok-4.5"
        assert kwargs["system_instruction"] == SYSTEM_INSTRUCTION
        assert kwargs["max_output_tokens"] == 30000

    def test_strips_whitespace(self, mock_llm):
        body = generate_memory_body([_summary("2026-01-01")])

        assert body.startswith("## 核心议题")
        assert not body.endswith(" ")


# ── update_memory ─────────────────────────────────────────────────────────


class TestUpdateMemory:
    """整份 LONG_TERM_MEMORY.md 的组装与写入。"""

    def test_header_counts_and_date_range_from_code(self, paths, mock_llm):
        """头部数字/日期由代码算，不是 LLM 说的。"""
        summaries = [_summary("2026-01-01"), _summary("2026-03-15"), _summary("2026-06-27")]

        content = update_memory(summaries)

        today = datetime.date.today().isoformat()
        assert content.startswith("# 长期记忆（自动维护，请勿手动编辑主体）\n")
        assert f"更新时间：{today}" in content
        assert "已纳入：3 次（2026-01-01 ~ 2026-06-27）" in content

    def test_body_appended_after_header(self, paths, mock_llm):
        content = update_memory([_summary("2026-01-01")])

        assert "## 核心议题（反复出现）" in content
        assert content.endswith("\n")

    def test_writes_file(self, paths, mock_llm):
        content = update_memory([_summary("2026-01-01")])

        assert paths["ltm"].read_text(encoding="utf-8") == content

    def test_loads_summaries_when_not_given(self, paths, mock_llm):
        """不传 summaries → 自己去 SUMMARIES_DIR 加载。"""
        (paths["summaries"] / "a.json").write_text(
            json.dumps(_summary("2026-05-05")), encoding="utf-8"
        )

        content = update_memory()

        assert "已纳入：1 次（2026-05-05 ~ 2026-05-05）" in content

    def test_explicit_empty_list_does_not_fall_back_to_disk(self, paths, mock_llm):
        """传 [] 是「就是空的」，不该被当成 None 去读磁盘——但空列表算不出日期范围。"""
        (paths["summaries"] / "a.json").write_text(
            json.dumps(_summary("2026-05-05")), encoding="utf-8"
        )

        with pytest.raises(ValueError):  # min() 对空序列
            update_memory([])
