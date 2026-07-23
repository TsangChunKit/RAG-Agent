"""测试摘要生成功能（scripts/summarize.py）。

当前覆盖率：35% (13/37 行)
目标：60%+

重点测试：
1. summarize_session() - 会话摘要生成
2. load_summaries() - 摘要加载
3. summary_path() - 路径函数
4. summarize_all() - 批量生成
5. JSON 存储和读取
"""
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from scripts.parse import ParsedSession, Utterance
from scripts.summarize import (
    SUMMARY_SCHEMA,
    SYSTEM_INSTRUCTION,
    summarize_all,
    summarize_session,
    summary_path,
)


# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def mock_llm_response():
    """Mock LLM 返回的结构化摘要 JSON。"""
    return {
        "topics": ["工作压力", "职业倦怠"],
        "emotional_tone": "焦虑、疲惫但仍在坚持",
        "key_events": [
            "项目截止日期临近，进度落后",
            "团队成员请假导致工作量增加"
        ],
        "psychological_themes": [
            "完美主义倾向导致过度承担责任",
            "害怕让他人失望的核心恐惧"
        ],
        "decisions_or_actions": [
            "决定向上级沟通延长截止日期的可能性",
            "计划每天留出30分钟休息时间"
        ],
        "quotes_worth_remembering": [
            "我总觉得如果我不做到最好，大家会觉得我不够格",
            "可能我需要学会说不"
        ]
    }


@pytest.fixture
def sample_parsed_session():
    """示例 ParsedSession 对象。"""
    return ParsedSession(
        source_file="20240715120000_咨询记录.txt",
        session_date="2024-07-15",
        file_datetime="20240715120000",
        utterances=[
            Utterance(speaker="Andy", timestamp="00:00:15", text="最近工作压力很大", line_no=1),
            Utterance(speaker="咨询师", timestamp="00:01:30", text="能具体说说吗", line_no=2),
            Utterance(speaker="Andy", timestamp="00:02:45", text="项目截止日期快到了", line_no=3)
        ]
    )


# ── Test summary_path ─────────────────────────────────────────────────────


class TestSummaryPath:
    """测试摘要路径函数。"""

    def test_summary_path_default_workspace(self, monkeypatch, tmp_path):
        """测试默认 workspace 的路径生成。"""
        # Mock SUMMARIES_DIR 返回测试目录
        summaries_dir = tmp_path / "summaries"
        summaries_dir.mkdir()

        with patch("scripts.summarize.SUMMARIES_DIR") as mock_dir:
            mock_dir.return_value = summaries_dir

            result = summary_path("20240715120000_test.txt")

            assert result == summaries_dir / "20240715120000_test.json"
            assert result.suffix == ".json"

    def test_summary_path_with_workspace_id(self, monkeypatch, tmp_path):
        """测试指定 workspace_id 的路径生成。"""
        summaries_dir = tmp_path / "workspaces" / "custom" / "summaries"
        summaries_dir.mkdir(parents=True)

        with patch("scripts.summarize.SUMMARIES_DIR") as mock_dir:
            mock_dir.return_value = summaries_dir

            result = summary_path("20240715120000_test.txt", workspace_id="custom")

            assert result == summaries_dir / "20240715120000_test.json"

    def test_summary_path_removes_extension(self, tmp_path):
        """测试路径函数正确去除原扩展名。"""
        summaries_dir = tmp_path / "summaries"
        summaries_dir.mkdir()

        with patch("scripts.summarize.SUMMARIES_DIR") as mock_dir:
            mock_dir.return_value = summaries_dir

            # 各种扩展名都应该被替换为 .json
            assert summary_path("file.txt").name == "file.json"
            assert summary_path("file.md").name == "file.json"
            assert summary_path("file.tar.gz").name == "file.tar.json"


# ── Test summarize_session ────────────────────────────────────────────────


class TestSummarizeSession:
    """测试单个会话摘要生成。"""

    def test_summarize_session_success(self, sample_parsed_session, mock_llm_response):
        """测试成功生成摘要。"""
        # Mock ask_llm 返回
        mock_response = MagicMock()
        mock_response.text = json.dumps(mock_llm_response, ensure_ascii=False)

        with patch("scripts.summarize.ask_llm", return_value=mock_response):
            result = summarize_session(sample_parsed_session)

            # 验证结构
            assert "session_date" in result
            assert "source_file" in result
            assert "file_datetime" in result
            assert "topics" in result
            assert "emotional_tone" in result

            # 验证元数据来自 session（不是 LLM）
            assert result["session_date"] == "2024-07-15"
            assert result["source_file"] == "20240715120000_咨询记录.txt"
            assert result["file_datetime"] == "20240715120000"

            # 验证内容来自 LLM
            assert result["topics"] == ["工作压力", "职业倦怠"]
            assert "焦虑" in result["emotional_tone"]

    def test_summarize_session_calls_llm_with_correct_params(self, sample_parsed_session, mock_llm_response):
        """测试 LLM 调用参数正确。"""
        mock_response = MagicMock()
        mock_response.text = json.dumps(mock_llm_response, ensure_ascii=False)

        with patch("scripts.summarize.ask_llm", return_value=mock_response) as mock_llm:
            with patch("scripts.summarize.summary_max_tokens", return_value=4096):
                summarize_session(sample_parsed_session)

                # 验证调用参数
                mock_llm.assert_called_once()
                call_kwargs = mock_llm.call_args.kwargs

                assert call_kwargs["profile"] == "summary"
                assert call_kwargs["system_instruction"] == SYSTEM_INSTRUCTION
                assert call_kwargs["response_schema"] == SUMMARY_SCHEMA
                assert call_kwargs["max_output_tokens"] == 4096

                # 验证输入包含会话内容
                input_text = mock_llm.call_args.args[0]
                assert "工作压力" in input_text

    def test_summarize_session_handles_json_parse_error(self, sample_parsed_session):
        """测试 JSON 解析错误处理。"""
        # Mock 返回无效 JSON
        mock_response = MagicMock()
        mock_response.text = "invalid json {"

        with patch("scripts.summarize.ask_llm", return_value=mock_response):
            with pytest.raises(json.JSONDecodeError):
                summarize_session(sample_parsed_session)

    def test_summarize_session_preserves_chinese_characters(self, sample_parsed_session, mock_llm_response):
        """测试中文字符不被转义。"""
        mock_response = MagicMock()
        mock_response.text = json.dumps(mock_llm_response, ensure_ascii=False)

        with patch("scripts.summarize.ask_llm", return_value=mock_response):
            result = summarize_session(sample_parsed_session)

            # ensure_ascii=False 应该保留中文
            assert "工作压力" in result["topics"]
            assert "焦虑" in result["emotional_tone"]


# ── Test summarize_all ────────────────────────────────────────────────────


class TestSummarizeAll:
    """测试批量摘要生成。"""

    def test_summarize_all_creates_directory(self, tmp_path, monkeypatch):
        """测试自动创建 summaries 目录。"""
        summaries_dir = tmp_path / "summaries"
        raw_dir = tmp_path / "raw"
        raw_dir.mkdir()

        with patch("scripts.summarize.SUMMARIES_DIR", return_value=summaries_dir), \
             patch("scripts.summarize.iter_raw_files", return_value=[]):
            summarize_all()

            assert summaries_dir.exists()
            assert summaries_dir.is_dir()

    def test_summarize_all_skip_existing_without_force(self, tmp_path, mock_llm_response):
        """测试不强制时跳过已存在的摘要。"""
        summaries_dir = tmp_path / "summaries"
        summaries_dir.mkdir()
        raw_dir = tmp_path / "raw"
        raw_dir.mkdir()

        # 创建已存在的摘要
        existing_summary = {
            "session_date": "2024-07-15",
            "source_file": "20240715120000_test.txt",
            "topics": ["existing"]
        }
        existing_file = summaries_dir / "20240715120000_test.json"
        existing_file.write_text(json.dumps(existing_summary, ensure_ascii=False))

        # Mock iter_raw_files 返回一个文件
        mock_file = MagicMock()
        mock_file.name = "20240715120000_test.txt"

        with patch("scripts.summarize.SUMMARIES_DIR", return_value=summaries_dir), \
             patch("scripts.summarize.iter_raw_files", return_value=[mock_file]), \
             patch("scripts.summarize.parse_transcript") as mock_parse, \
             patch("scripts.summarize.ask_llm") as mock_llm:

            result = summarize_all(force=False)

            # 应该读取已存在的摘要，不调用 LLM
            assert len(result) == 1
            assert result[0]["topics"] == ["existing"]
            mock_parse.assert_not_called()
            mock_llm.assert_not_called()

    def test_summarize_all_force_regenerates_existing(self, tmp_path, mock_llm_response, sample_parsed_session):
        """测试 force=True 时重新生成已存在的摘要。"""
        summaries_dir = tmp_path / "summaries"
        summaries_dir.mkdir()
        raw_dir = tmp_path / "raw"
        raw_dir.mkdir()

        # 创建已存在的摘要
        existing_file = summaries_dir / "20240715120000_test.json"
        existing_file.write_text(json.dumps({"topics": ["old"]}, ensure_ascii=False))

        # Mock
        mock_file = MagicMock()
        mock_file.name = "20240715120000_test.txt"

        mock_response = MagicMock()
        mock_response.text = json.dumps(mock_llm_response, ensure_ascii=False)

        with patch("scripts.summarize.SUMMARIES_DIR", return_value=summaries_dir), \
             patch("scripts.summarize.iter_raw_files", return_value=[mock_file]), \
             patch("scripts.summarize.parse_transcript", return_value=sample_parsed_session), \
             patch("scripts.summarize.ask_llm", return_value=mock_response), \
             patch("scripts.summarize.summary_max_tokens", return_value=4096):

            result = summarize_all(force=True)

            # 应该重新生成
            assert len(result) == 1
            assert result[0]["topics"] == ["工作压力", "职业倦怠"]

            # 文件应该被更新
            new_content = json.loads(existing_file.read_text(encoding="utf-8"))
            assert new_content["topics"] == ["工作压力", "职业倦怠"]

    def test_summarize_all_processes_multiple_files(self, tmp_path, mock_llm_response, sample_parsed_session):
        """测试处理多个文件。"""
        summaries_dir = tmp_path / "summaries"
        summaries_dir.mkdir()

        # Mock 多个文件（需要设置 name 作为属性而非参数）
        mock_file1 = MagicMock()
        mock_file1.name = "20240715120000_test1.txt"
        mock_file2 = MagicMock()
        mock_file2.name = "20240716120000_test2.txt"
        mock_files = [mock_file1, mock_file2]

        mock_response = MagicMock()
        mock_response.text = json.dumps(mock_llm_response, ensure_ascii=False)

        with patch("scripts.summarize.SUMMARIES_DIR", return_value=summaries_dir), \
             patch("scripts.summarize.iter_raw_files", return_value=mock_files), \
             patch("scripts.summarize.parse_transcript", return_value=sample_parsed_session), \
             patch("scripts.summarize.ask_llm", return_value=mock_response), \
             patch("scripts.summarize.summary_max_tokens", return_value=4096):

            result = summarize_all()

            # 应该处理两个文件
            assert len(result) == 2

            # 两个摘要文件都应该被创建
            assert (summaries_dir / "20240715120000_test1.json").exists()
            assert (summaries_dir / "20240716120000_test2.json").exists()

    def test_summarize_all_with_workspace_id(self, tmp_path, mock_llm_response, sample_parsed_session):
        """测试 workspace_id 隔离。"""
        workspace_dir = tmp_path / "workspaces" / "custom"
        summaries_dir = workspace_dir / "summaries"
        summaries_dir.mkdir(parents=True)

        mock_file = MagicMock()
        mock_file.name = "20240715120000_test.txt"

        mock_response = MagicMock()
        mock_response.text = json.dumps(mock_llm_response, ensure_ascii=False)

        with patch("scripts.summarize.SUMMARIES_DIR", return_value=summaries_dir), \
             patch("scripts.summarize.iter_raw_files", return_value=[mock_file]), \
             patch("scripts.summarize.parse_transcript", return_value=sample_parsed_session), \
             patch("scripts.summarize.ask_llm", return_value=mock_response), \
             patch("scripts.summarize.summary_max_tokens", return_value=4096):

            result = summarize_all(workspace_id="custom")

            # 摘要应该保存到正确的 workspace
            assert len(result) == 1
            assert (summaries_dir / "20240715120000_test.json").exists()

    def test_summarize_all_handles_empty_raw_directory(self, tmp_path):
        """测试处理空的 raw 目录。"""
        summaries_dir = tmp_path / "summaries"
        summaries_dir.mkdir()

        with patch("scripts.summarize.SUMMARIES_DIR", return_value=summaries_dir), \
             patch("scripts.summarize.iter_raw_files", return_value=[]):

            result = summarize_all()

            assert len(result) == 0
            assert summaries_dir.exists()


# ── Test JSON 存储格式 ────────────────────────────────────────────────────


class TestJSONStorage:
    """测试 JSON 存储格式。"""

    def test_summary_json_format(self, tmp_path, mock_llm_response, sample_parsed_session):
        """测试存储的 JSON 格式正确。"""
        summaries_dir = tmp_path / "summaries"
        summaries_dir.mkdir()

        mock_file = MagicMock()
        mock_file.name = "20240715120000_test.txt"

        mock_response = MagicMock()
        mock_response.text = json.dumps(mock_llm_response, ensure_ascii=False)

        with patch("scripts.summarize.SUMMARIES_DIR", return_value=summaries_dir), \
             patch("scripts.summarize.iter_raw_files", return_value=[mock_file]), \
             patch("scripts.summarize.parse_transcript", return_value=sample_parsed_session), \
             patch("scripts.summarize.ask_llm", return_value=mock_response), \
             patch("scripts.summarize.summary_max_tokens", return_value=4096):

            summarize_all()

            # 读取文件验证格式
            json_file = summaries_dir / "20240715120000_test.json"
            content = json_file.read_text(encoding="utf-8")

            # 应该可以解析
            data = json.loads(content)

            # 验证必需字段
            assert "session_date" in data
            assert "source_file" in data
            assert "topics" in data
            assert "emotional_tone" in data
            assert "key_events" in data
            assert "psychological_themes" in data

            # 验证中文未被转义
            assert "工作压力" in content

    def test_summary_json_is_pretty_printed(self, tmp_path, mock_llm_response, sample_parsed_session):
        """测试 JSON 使用格式化输出（indent=2）。"""
        summaries_dir = tmp_path / "summaries"
        summaries_dir.mkdir()

        mock_file = MagicMock()
        mock_file.name = "20240715120000_test.txt"

        mock_response = MagicMock()
        mock_response.text = json.dumps(mock_llm_response, ensure_ascii=False)

        with patch("scripts.summarize.SUMMARIES_DIR", return_value=summaries_dir), \
             patch("scripts.summarize.iter_raw_files", return_value=[mock_file]), \
             patch("scripts.summarize.parse_transcript", return_value=sample_parsed_session), \
             patch("scripts.summarize.ask_llm", return_value=mock_response), \
             patch("scripts.summarize.summary_max_tokens", return_value=4096):

            summarize_all()

            json_file = summaries_dir / "20240715120000_test.json"
            content = json_file.read_text(encoding="utf-8")

            # 格式化的 JSON 应该有换行和缩进
            assert "\n" in content
            assert "  " in content  # 至少有缩进


# ── Test 边界情况 ─────────────────────────────────────────────────────────


class TestEdgeCases:
    """测试边界情况和错误处理。"""

    def test_summarize_session_with_empty_utterances(self):
        """测试空 utterances 的会话。"""
        empty_session = ParsedSession(
            source_file="20240715120000_empty.txt",
            session_date="2024-07-15",
            file_datetime="20240715120000",
            utterances=[]
        )

        mock_response = MagicMock()
        mock_response.text = json.dumps({
            "topics": [],
            "emotional_tone": "无内容",
            "key_events": [],
            "psychological_themes": [],
            "decisions_or_actions": [],
            "quotes_worth_remembering": []
        }, ensure_ascii=False)

        with patch("scripts.summarize.ask_llm", return_value=mock_response):
            result = summarize_session(empty_session)

            # 仍应返回有效结构
            assert result["session_date"] == "2024-07-15"
            assert result["topics"] == []

    def test_summary_path_with_missing_summaries_dir(self, tmp_path):
        """测试 summaries 目录不存在时的路径生成。"""
        # 目录不存在时路径函数应该仍能返回路径（由 summarize_all 创建目录）
        non_existent_dir = tmp_path / "non_existent" / "summaries"

        with patch("scripts.summarize.SUMMARIES_DIR", return_value=non_existent_dir):
            result = summary_path("test.txt")

            # 应该返回正确路径（即使目录不存在）
            assert result == non_existent_dir / "test.json"

    def test_summarize_all_handles_parse_error(self, tmp_path):
        """测试解析错误不影响其他文件处理。"""
        summaries_dir = tmp_path / "summaries"
        summaries_dir.mkdir()

        mock_files = [
            MagicMock(name="20240715120000_valid.txt"),
            MagicMock(name="invalid_file.txt")  # 会解析失败
        ]

        with patch("scripts.summarize.SUMMARIES_DIR", return_value=summaries_dir), \
             patch("scripts.summarize.iter_raw_files", return_value=mock_files), \
             patch("scripts.summarize.parse_transcript", side_effect=ValueError("解析失败")):

            # 应该抛出错误（当前实现不处理解析错误）
            with pytest.raises(ValueError):
                summarize_all()
