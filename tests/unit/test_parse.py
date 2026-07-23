"""测试逐字稿解析功能。"""
from pathlib import Path

import pytest

from scripts.parse import parse_filename_date, parse_transcript, render_full_text


class TestFilenameParser:
    """文件名解析测试。"""

    def test_parse_filename_date_valid(self):
        """测试解析有效文件名。"""
        date, dt = parse_filename_date("20240715120000_咨询记录.txt")

        assert date == "2024-07-15"
        assert dt == "20240715120000"

    def test_parse_filename_date_invalid(self):
        """测试解析无效文件名。"""
        with pytest.raises(ValueError):
            parse_filename_date("invalid_filename.txt")

    def test_parse_filename_date_short(self):
        """测试短格式文件名（没有时分秒）。"""
        with pytest.raises(ValueError):
            parse_filename_date("20240715_file.txt")


class TestTranscriptParser:
    """逐字稿解析测试。"""

    def test_parse_transcript_basic(self, tmp_path, sample_transcript):
        """测试基本解析功能。"""
        # 写入测试文件
        test_file = tmp_path / "20240101120000_test.txt"
        test_file.write_text(sample_transcript)

        # 解析
        session = parse_transcript(test_file)

        # 验证元数据
        assert session.source_file == "20240101120000_test.txt"
        assert session.session_date == "2024-01-01"

        # 验证 utterances
        assert len(session.utterances) == 3
        assert session.utterances[0].speaker == "Andy"
        assert session.utterances[0].timestamp == "00:00:15"
        assert "工作压力" in session.utterances[0].text

    def test_parse_transcript_empty_file(self, tmp_path):
        """测试空文件处理。"""
        test_file = tmp_path / "20240101120000_empty.txt"
        test_file.write_text("")

        session = parse_transcript(test_file)

        assert session.utterances == []

    def test_render_full_text(self, tmp_path, sample_transcript):
        """测试渲染完整文本。"""
        test_file = tmp_path / "20240101120000_test.txt"
        test_file.write_text(sample_transcript)

        session = parse_transcript(test_file)
        full_text = render_full_text(session)

        # 验证格式
        assert "Andy:" in full_text
        assert "咨询师:" in full_text
        assert "工作压力" in full_text
