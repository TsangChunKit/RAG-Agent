"""测试逐字稿解析功能。

Coverage target: 80%+
Current: 60% -> Need to test:
- parse_transcript with various edge cases (timeline format, label lines, continuation lines)
- iter_raw_files
- find_files_for_date
- Error handling and boundary conditions
"""
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest

from scripts.parse import (
    LABEL_LINE_RE,
    SKIP_LINE_RE,
    TIMELINE_RE,
    TRANSCRIPT_RE,
    ParsedSession,
    Utterance,
    find_files_for_date,
    iter_raw_files,
    parse_filename_date,
    parse_transcript,
    render_full_text,
)


class TestFilenameParser:
    """文件名解析测试。"""

    def test_parse_filename_date_valid(self):
        """测试解析有效文件名（标准格式）。"""
        date, dt = parse_filename_date("20240715120000_咨询记录.txt")

        assert date == "2024-07-15"
        assert dt == "20240715120000"

    def test_parse_filename_date_with_complex_suffix(self):
        """测试解析复杂后缀的文件名。"""
        date, dt = parse_filename_date("20260329140106-andy-时间轴文本-1.txt")

        assert date == "2026-03-29"
        assert dt == "20260329140106"

    def test_parse_filename_date_invalid(self):
        """测试解析无效文件名（无日期前缀）。"""
        with pytest.raises(ValueError, match="文件名不含 14 位日期前缀"):
            parse_filename_date("invalid_filename.txt")

    def test_parse_filename_date_short(self):
        """测试短格式文件名（没有时分秒）。"""
        with pytest.raises(ValueError, match="文件名不含 14 位日期前缀"):
            parse_filename_date("20240715_file.txt")

    def test_parse_filename_date_partial(self):
        """测试部分日期格式（不足14位）。"""
        with pytest.raises(ValueError):
            parse_filename_date("202407151200_file.txt")  # 只有12位


class TestRegexPatterns:
    """测试正则表达式匹配。"""

    def test_transcript_re_standard_format(self):
        """测试标准发言格式匹配。"""
        line = "Andy(00:00:15): 最近工作压力很大"
        m = TRANSCRIPT_RE.match(line)
        assert m is not None
        assert m.group(1) == "Andy"
        assert m.group(2) == "00:00:15"
        assert m.group(3) == "最近工作压力很大"

    def test_transcript_re_with_spaces(self):
        """测试带空格的发言格式。"""
        line = "咨询师 (12:34:56): 能具体说说吗？"
        m = TRANSCRIPT_RE.match(line)
        assert m is not None
        assert m.group(1).strip() == "咨询师"

    def test_timeline_re_mm_ss_format(self):
        """测试时间轴格式（MM:SS）。"""
        line = "12:34 来访者表达焦虑情绪"
        m = TIMELINE_RE.match(line)
        assert m is not None
        assert m.group(1) == "12:34"
        assert m.group(2) == "来访者表达焦虑情绪"

    def test_timeline_re_single_digit(self):
        """测试单位数分钟的时间轴。"""
        line = "5:30 开始讨论工作问题"
        m = TIMELINE_RE.match(line)
        assert m is not None
        assert m.group(1) == "5:30"

    def test_label_line_re_colon(self):
        """测试标签行匹配（冒号结尾）。"""
        assert LABEL_LINE_RE.match("视频内容:") is not None
        assert LABEL_LINE_RE.match("分享的文章：") is not None
        assert LABEL_LINE_RE.match("备注:") is not None

    def test_label_line_re_too_long(self):
        """测试标签行长度限制（超过12字符不匹配）。"""
        # 中文字符也计为1个字符，所以需要13个字符才会不匹配
        assert LABEL_LINE_RE.match("这是一个非常长的标签名字超长:") is None
        # 刚好12个字符应该匹配
        assert LABEL_LINE_RE.match("十二个字符标签:") is not None

    def test_skip_line_re_disclaimer(self):
        """测试跳过免责声明。"""
        assert SKIP_LINE_RE.match("（注：文档部分内容可能由 AI 生成）") is not None
        assert SKIP_LINE_RE.match("注：本文档仅供参考") is not None
        assert SKIP_LINE_RE.match("(注：测试)") is not None


class TestUtterance:
    """测试 Utterance 数据类。"""

    def test_utterance_creation(self):
        """测试创建 Utterance 实例。"""
        u = Utterance(speaker="Andy", timestamp="00:00:15", text="测试文本", line_no=0)
        assert u.speaker == "Andy"
        assert u.timestamp == "00:00:15"
        assert u.text == "测试文本"
        assert u.line_no == 0


class TestParsedSession:
    """测试 ParsedSession 数据类。"""

    def test_parsed_session_creation(self):
        """测试创建 ParsedSession 实例。"""
        session = ParsedSession(
            source_file="test.txt",
            session_date="2024-07-15",
            file_datetime="20240715120000"
        )
        assert session.source_file == "test.txt"
        assert session.session_date == "2024-07-15"
        assert session.file_datetime == "20240715120000"
        assert session.utterances == []

    def test_parsed_session_with_utterances(self):
        """测试带 utterances 的 ParsedSession。"""
        utterances = [
            Utterance("Andy", "00:00:15", "文本1", 0),
            Utterance("咨询师", "00:01:30", "文本2", 1),
        ]
        session = ParsedSession("test.txt", "2024-07-15", "20240715120000", utterances)
        assert len(session.utterances) == 2


class TestTranscriptParser:
    """逐字稿解析测试。"""

    def test_parse_transcript_standard_format(self, tmp_path):
        """测试标准发言格式解析。"""
        content = """Andy(00:00:15): 最近工作压力很大，感觉喘不过气。
咨询师(00:01:30): 能具体说说是什么让你感到压力吗？
Andy(00:02:45): 项目截止日期快到了，但进度落后很多。
"""
        test_file = tmp_path / "20240101120000_test.txt"
        test_file.write_text(content)

        session = parse_transcript(test_file)

        # 验证元数据
        assert session.source_file == "20240101120000_test.txt"
        assert session.session_date == "2024-01-01"
        assert session.file_datetime == "20240101120000"

        # 验证 utterances
        assert len(session.utterances) == 3
        assert session.utterances[0].speaker == "Andy"
        assert session.utterances[0].timestamp == "00:00:15"
        assert "工作压力" in session.utterances[0].text
        assert session.utterances[1].speaker == "咨询师"
        assert session.utterances[2].speaker == "Andy"

    def test_parse_transcript_timeline_format(self, tmp_path):
        """测试时间轴摘要格式（MM:SS 第三人称）。"""
        content = """12:34 来访者表达对工作的焦虑
15:20 咨询师引导来访者探索情绪来源
20:45 来访者意识到自己的完美主义倾向
"""
        test_file = tmp_path / "20260329140106_timeline.txt"
        test_file.write_text(content)

        session = parse_transcript(test_file)

        assert len(session.utterances) == 3
        # 时间轴格式应转为 "摘要" 发言人
        assert session.utterances[0].speaker == "摘要"
        assert session.utterances[0].timestamp == "00:12:34"  # MM:SS -> HH:MM:SS
        assert "来访者表达对工作的焦虑" in session.utterances[0].text

        assert session.utterances[1].timestamp == "00:15:20"
        assert session.utterances[2].timestamp == "00:20:45"

    def test_parse_transcript_label_lines(self, tmp_path):
        """测试标签行（如 '视频内容:'）。"""
        content = """Andy(00:00:15): 我最近看了一个视频
视频内容:
这是视频的第一段文字
这是视频的第二段文字
Andy(00:05:00): 这个视频让我想到了自己
"""
        test_file = tmp_path / "20240101120000_label.txt"
        test_file.write_text(content)

        session = parse_transcript(test_file)

        assert len(session.utterances) == 3
        assert session.utterances[0].speaker == "Andy"
        assert session.utterances[1].speaker == "视频内容"
        # 标签行沿用上一条时间戳
        assert session.utterances[1].timestamp == "00:00:15"
        # 后续行作为延续文本
        assert "这是视频的第一段文字" in session.utterances[1].text
        assert "这是视频的第二段文字" in session.utterances[1].text
        assert session.utterances[2].speaker == "Andy"

    def test_parse_transcript_multiline_text(self, tmp_path):
        """测试多行发言（延续段落）。"""
        content = """Andy(00:00:15): 我想说的第一件事
这是第二行
这是第三行
咨询师(00:01:30): 我明白你的意思
"""
        test_file = tmp_path / "20240101120000_multiline.txt"
        test_file.write_text(content)

        session = parse_transcript(test_file)

        assert len(session.utterances) == 2
        # 验证多行被合并为一条 utterance
        assert "我想说的第一件事" in session.utterances[0].text
        assert "这是第二行" in session.utterances[0].text
        assert "这是第三行" in session.utterances[0].text
        # 验证换行符被保留
        assert "\n" in session.utterances[0].text

    def test_parse_transcript_skip_disclaimer(self, tmp_path):
        """测试跳过免责声明。"""
        content = """（注：文档部分内容可能由 AI 生成）
Andy(00:00:15): 这是正常发言
注：本文档仅供参考
咨询师(00:01:30): 另一条发言
"""
        test_file = tmp_path / "20240101120000_disclaimer.txt"
        test_file.write_text(content)

        session = parse_transcript(test_file)

        # 免责声明应被跳过，只解析到2条发言
        assert len(session.utterances) == 2
        assert session.utterances[0].speaker == "Andy"
        assert session.utterances[1].speaker == "咨询师"

    def test_parse_transcript_skip_title_line(self, tmp_path):
        """测试跳过标题行（在第一条发言之前的无格式行）。"""
        content = """2026年6月27日 记录
Andy(00:00:15): 第一条发言
咨询师(00:01:30): 第二条发言
"""
        test_file = tmp_path / "20240101120000_title.txt"
        test_file.write_text(content)

        session = parse_transcript(test_file)

        # 标题行应被跳过
        assert len(session.utterances) == 2
        assert session.utterances[0].speaker == "Andy"

    def test_parse_transcript_empty_file(self, tmp_path):
        """测试空文件处理。"""
        test_file = tmp_path / "20240101120000_empty.txt"
        test_file.write_text("")

        session = parse_transcript(test_file)

        assert session.utterances == []
        assert session.session_date == "2024-01-01"

    def test_parse_transcript_whitespace_only(self, tmp_path):
        """测试只有空白字符的文件。"""
        content = """



"""
        test_file = tmp_path / "20240101120000_whitespace.txt"
        test_file.write_text(content)

        session = parse_transcript(test_file)

        assert session.utterances == []

    def test_parse_transcript_mixed_formats(self, tmp_path):
        """测试混合格式（标准格式 + 时间轴 + 标签）。"""
        content = """Andy(00:00:15): 标准格式发言
12:34 时间轴摘要
视频内容:
这是视频文字
咨询师(00:05:00): 继续标准格式
"""
        test_file = tmp_path / "20240101120000_mixed.txt"
        test_file.write_text(content)

        session = parse_transcript(test_file)

        assert len(session.utterances) == 4
        assert session.utterances[0].speaker == "Andy"
        assert session.utterances[1].speaker == "摘要"
        assert session.utterances[2].speaker == "视频内容"
        assert session.utterances[3].speaker == "咨询师"

    def test_parse_transcript_unicode_handling(self, tmp_path):
        """测试 Unicode 处理（包含 emoji 和特殊字符）。"""
        content = """Andy(00:00:15): 我感觉😊很好，但又有些🤔困惑
咨询师(00:01:30): 这种矛盾的感受很正常 ✨
"""
        test_file = tmp_path / "20240101120000_unicode.txt"
        test_file.write_text(content, encoding="utf-8")

        session = parse_transcript(test_file)

        assert len(session.utterances) == 2
        assert "😊" in session.utterances[0].text
        assert "✨" in session.utterances[1].text

    def test_parse_transcript_invalid_filename(self, tmp_path):
        """测试文件名格式错误时抛出异常。"""
        test_file = tmp_path / "invalid_name.txt"
        test_file.write_text("Andy(00:00:15): 内容")

        with pytest.raises(ValueError, match="文件名不含 14 位日期前缀"):
            parse_transcript(test_file)


class TestRenderFullText:
    """测试完整文本渲染功能。"""

    def test_render_full_text_basic(self, tmp_path):
        """测试基本渲染功能。"""
        content = """Andy(00:00:15): 第一条发言
咨询师(00:01:30): 第二条发言
Andy(00:02:45): 第三条发言
"""
        test_file = tmp_path / "20240101120000_test.txt"
        test_file.write_text(content)

        session = parse_transcript(test_file)
        full_text = render_full_text(session)

        # 验证格式：speaker(timestamp): text
        assert "Andy(00:00:15): 第一条发言" in full_text
        assert "咨询师(00:01:30): 第二条发言" in full_text
        assert "Andy(00:02:45): 第三条发言" in full_text
        # 验证换行分隔
        assert full_text.count("\n") == 2  # 3条发言 = 2个换行符

    def test_render_full_text_empty(self):
        """测试空 session 渲染。"""
        session = ParsedSession("test.txt", "2024-01-01", "20240101120000", [])
        full_text = render_full_text(session)
        assert full_text == ""

    def test_render_full_text_multiline_utterance(self, tmp_path):
        """测试多行发言的渲染。"""
        content = """Andy(00:00:15): 第一行
第二行
第三行
"""
        test_file = tmp_path / "20240101120000_test.txt"
        test_file.write_text(content)

        session = parse_transcript(test_file)
        full_text = render_full_text(session)

        # 多行发言在渲染时应包含换行符
        assert "第一行\n第二行\n第三行" in full_text


class TestIterRawFiles:
    """测试原始文件迭代功能。"""

    @patch("scripts.parse.RAW_DIR")
    def test_iter_raw_files_default_workspace(self, mock_raw_dir, tmp_path):
        """测试默认 workspace 的文件迭代。"""
        # 创建测试文件
        raw_dir = tmp_path / "raw"
        raw_dir.mkdir()
        (raw_dir / "20240101120000_file1.txt").touch()
        (raw_dir / "20240102120000_file2.txt").touch()
        (raw_dir / "not_a_transcript.md").touch()  # 非 txt 文件

        mock_raw_dir.return_value = raw_dir

        files = list(iter_raw_files())

        # 验证只返回 .txt 文件，且按文件名排序
        assert len(files) == 2
        assert files[0].name == "20240101120000_file1.txt"
        assert files[1].name == "20240102120000_file2.txt"

    @patch("scripts.parse.RAW_DIR")
    def test_iter_raw_files_with_workspace_id(self, mock_raw_dir, tmp_path):
        """测试指定 workspace_id 的文件迭代。"""
        raw_dir = tmp_path / "raw"
        raw_dir.mkdir()
        (raw_dir / "20240101120000_test.txt").touch()

        mock_raw_dir.return_value = raw_dir

        files = list(iter_raw_files(workspace_id="test-workspace"))

        # 验证 RAW_DIR 被正确调用
        mock_raw_dir.assert_called_once_with("test-workspace")
        assert len(files) == 1

    @patch("scripts.parse.RAW_DIR")
    def test_iter_raw_files_empty_directory(self, mock_raw_dir, tmp_path):
        """测试空目录时返回空列表。"""
        raw_dir = tmp_path / "raw"
        raw_dir.mkdir()
        mock_raw_dir.return_value = raw_dir

        files = list(iter_raw_files())

        assert files == []

    @patch("scripts.parse.RAW_DIR")
    def test_iter_raw_files_sorting(self, mock_raw_dir, tmp_path):
        """测试文件按名称排序（时间顺序）。"""
        raw_dir = tmp_path / "raw"
        raw_dir.mkdir()
        # 故意乱序创建
        (raw_dir / "20240103120000_c.txt").touch()
        (raw_dir / "20240101120000_a.txt").touch()
        (raw_dir / "20240102120000_b.txt").touch()

        mock_raw_dir.return_value = raw_dir

        files = list(iter_raw_files())

        # 验证按文件名排序
        assert len(files) == 3
        assert files[0].name == "20240101120000_a.txt"
        assert files[1].name == "20240102120000_b.txt"
        assert files[2].name == "20240103120000_c.txt"


class TestFindFilesForDate:
    """测试按日期查找文件功能。"""

    @patch("scripts.parse.iter_raw_files")
    def test_find_files_for_date_single_match(self, mock_iter, tmp_path):
        """测试查找单个匹配文件。"""
        # Mock 返回的文件列表
        file1 = tmp_path / "20240715120000_file1.txt"
        file2 = tmp_path / "20240716130000_file2.txt"
        mock_iter.return_value = [file1, file2]

        matches = find_files_for_date("2024-07-15")

        assert len(matches) == 1
        assert matches[0] == file1
        mock_iter.assert_called_once_with(None)

    @patch("scripts.parse.iter_raw_files")
    def test_find_files_for_date_multiple_matches(self, mock_iter, tmp_path):
        """测试同一天有多个文件。"""
        file1 = tmp_path / "20240715120000_session1.txt"
        file2 = tmp_path / "20240715140000_session2.txt"
        file3 = tmp_path / "20240716130000_other.txt"
        mock_iter.return_value = [file1, file2, file3]

        matches = find_files_for_date("2024-07-15")

        assert len(matches) == 2
        assert file1 in matches
        assert file2 in matches
        assert file3 not in matches

    @patch("scripts.parse.iter_raw_files")
    def test_find_files_for_date_no_match(self, mock_iter, tmp_path):
        """测试没有匹配的文件。"""
        file1 = tmp_path / "20240715120000_file1.txt"
        mock_iter.return_value = [file1]

        matches = find_files_for_date("2024-07-16")

        assert matches == []

    @patch("scripts.parse.iter_raw_files")
    def test_find_files_for_date_with_workspace_id(self, mock_iter, tmp_path):
        """测试指定 workspace_id 的查找。"""
        file1 = tmp_path / "20240715120000_test.txt"
        mock_iter.return_value = [file1]

        matches = find_files_for_date("2024-07-15", workspace_id="test-workspace")

        mock_iter.assert_called_once_with("test-workspace")
        assert len(matches) == 1

    @patch("scripts.parse.iter_raw_files")
    def test_find_files_for_date_invalid_format(self, mock_iter, tmp_path):
        """测试文件名格式异常时会抛出异常（find_files_for_date 不处理无效文件）。"""
        # 包含一个格式错误的文件名
        valid_file = tmp_path / "20240715120000_valid.txt"
        invalid_file = tmp_path / "invalid_name.txt"
        mock_iter.return_value = [valid_file, invalid_file]

        # find_files_for_date 会尝试解析所有文件，遇到无效文件名会抛异常
        # 这是预期行为：原始文件应该都符合命名规范
        with pytest.raises(ValueError, match="文件名不含 14 位日期前缀"):
            find_files_for_date("2024-07-15")


class TestEdgeCases:
    """测试边界情况和特殊场景。"""

    def test_parse_transcript_line_number_tracking(self, tmp_path):
        """测试 line_no 正确记录（用于调试和错误定位）。"""
        content = """Andy(00:00:15): 第一行
咨询师(00:01:30): 第二行
Andy(00:02:45): 第三行
"""
        test_file = tmp_path / "20240101120000_test.txt"
        test_file.write_text(content)

        session = parse_transcript(test_file)

        # 验证 line_no 正确递增
        assert session.utterances[0].line_no == 0
        assert session.utterances[1].line_no == 1
        assert session.utterances[2].line_no == 2

    def test_parse_transcript_preserve_empty_text(self, tmp_path):
        """测试标签行可以有空文本（后续段落会追加）。"""
        content = """Andy(00:00:15): 正常发言
视频内容:
这是延续文本
"""
        test_file = tmp_path / "20240101120000_test.txt"
        test_file.write_text(content)

        session = parse_transcript(test_file)

        # 标签行初始为空文本，后续追加
        assert session.utterances[1].speaker == "视频内容"
        assert "这是延续文本" in session.utterances[1].text

    def test_timeline_timestamp_padding(self, tmp_path):
        """测试时间轴时间戳补零（MM:SS -> HH:MM:SS）。"""
        content = """5:30 短时间格式
12:45 标准时间格式
"""
        test_file = tmp_path / "20240101120000_test.txt"
        test_file.write_text(content)

        session = parse_transcript(test_file)

        # MM:SS 应补零为 00:MM:SS
        assert session.utterances[0].timestamp == "00:05:30"
        assert session.utterances[1].timestamp == "00:12:45"

    def test_parse_transcript_continuation_empty_initial_text(self, tmp_path):
        """测试延续行追加到空文本（标签行场景）。"""
        content = """Andy(00:00:15): 正常发言
标签:
第一段延续
第二段延续
"""
        test_file = tmp_path / "20240101120000_test.txt"
        test_file.write_text(content)

        session = parse_transcript(test_file)

        # 标签行初始空文本，延续行应正确追加
        label_utterance = session.utterances[1]
        assert label_utterance.speaker == "标签"
        # 第一段延续不带换行前缀（因为初始为空）
        assert label_utterance.text == "第一段延续\n第二段延续"
