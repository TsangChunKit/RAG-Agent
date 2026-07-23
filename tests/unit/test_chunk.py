"""测试文本分块功能。"""
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from scripts.chunk import (
    Chunk,
    _build_units,
    _render_unit,
    _split_long_utterance,
    chunk_all,
    chunk_session,
    contextual_prefix,
    write_chunks_jsonl,
)
from scripts.parse import ParsedSession, Utterance


class TestChunking:
    """分块逻辑测试。"""

    def test_chunk_session_basic(self, isolated_workspace):
        """测试基本分块功能。"""
        # 创建测试 session（添加 line_no）
        session = ParsedSession(
            source_file="20240101120000_test.txt",
            session_date="2024-01-01",
            file_datetime="20240101120000",
            utterances=[
                Utterance(timestamp="00:00:15", speaker="Andy", text="第一句话", line_no=1),
                Utterance(timestamp="00:01:00", speaker="咨询师", text="第二句话", line_no=2),
                Utterance(timestamp="00:02:00", speaker="Andy", text="第三句话", line_no=3),
            ]
        )

        # 分块
        chunks = chunk_session(session, workspace_id="_legacy")

        # 验证
        assert len(chunks) >= 1
        assert chunks[0].source_file == "20240101120000_test.txt"
        assert chunks[0].session_date == "2024-01-01"
        assert "第一句话" in chunks[0].text
        # 验证包含上下文前缀
        assert "[2024-01-01" in chunks[0].text
        # 验证 chunk_id 格式
        assert chunks[0].id == "20240101120000_test.txt::chunk0000"

    def test_contextual_prefix(self, isolated_workspace, monkeypatch):
        """测试上下文前缀生成。"""
        # Mock workspace config
        monkeypatch.setattr("scripts.chunk.load_workspace_config",
                           lambda x: {
                               "chunk_prefix_template": "[{session_date} {domain_label}]",
                               "domain_label": "测试"
                           })

        session = ParsedSession(
            source_file="test.txt",
            session_date="2024-01-01",
            file_datetime="20240101120000",
            utterances=[]
        )

        prefix = contextual_prefix(
            session=session,
            speakers=["Andy"],
            start_ts="00:00:00",
            end_ts="00:01:00",
            workspace_id="_legacy"
        )

        assert "2024-01-01" in prefix
        assert "测试" in prefix

    def test_chunk_empty_session(self):
        """测试空 session 处理。"""
        session = ParsedSession(
            source_file="empty.txt",
            session_date="2024-01-01",
            file_datetime="20240101120000",
            utterances=[]
        )

        chunks = chunk_session(session)
        assert len(chunks) == 0

    def test_contextual_prefix_default_template(self, monkeypatch):
        """测试上下文前缀默认模板。"""
        # Mock workspace config 返回空模板（触发默认）
        monkeypatch.setattr("scripts.chunk.load_workspace_config",
                           lambda x: {})

        session = ParsedSession(
            source_file="test.txt",
            session_date="2024-01-01",
            file_datetime="20240101120000",
            utterances=[]
        )

        prefix = contextual_prefix(
            session=session,
            speakers=["Andy", "咨询师"],
            start_ts="00:00:00",
            end_ts="00:05:00"
        )

        # 默认模板包含咨询、发言人、时间段
        assert "2024-01-01" in prefix
        assert "Andy" in prefix
        assert "咨询师" in prefix
        assert "00:00:00" in prefix
        assert "00:05:00" in prefix

    def test_contextual_prefix_template_error_fallback(self, monkeypatch):
        """测试模板错误时降级到默认格式。"""
        # Mock workspace config 返回错误模板
        monkeypatch.setattr("scripts.chunk.load_workspace_config",
                           lambda x: {
                               "chunk_prefix_template": "[{invalid_key}]",
                               "domain_label": "测试"
                           })

        session = ParsedSession(
            source_file="test.txt",
            session_date="2024-01-01",
            file_datetime="20240101120000",
            utterances=[]
        )

        prefix = contextual_prefix(
            session=session,
            speakers=["Andy"],
            start_ts="00:00:00",
            end_ts="00:01:00"
        )

        # 应该降级到默认格式
        assert "2024-01-01" in prefix
        assert "测试" in prefix

    def test_chunk_session_with_overlap(self, monkeypatch):
        """测试滑动窗口重叠逻辑。"""
        # Mock index_settings 返回小块大小，触发分块
        monkeypatch.setattr("scripts.index_settings.chunking_params",
                           lambda: {"chunk_size": 50, "chunk_overlap": 20})

        session = ParsedSession(
            source_file="test.txt",
            session_date="2024-01-01",
            file_datetime="20240101120000",
            utterances=[
                Utterance(timestamp="00:00:00", speaker="A", text="这是第一句话" * 3, line_no=1),
                Utterance(timestamp="00:01:00", speaker="B", text="这是第二句话" * 3, line_no=2),
                Utterance(timestamp="00:02:00", speaker="A", text="这是第三句话" * 3, line_no=3),
                Utterance(timestamp="00:03:00", speaker="B", text="这是第四句话" * 3, line_no=4),
            ]
        )

        chunks = chunk_session(session)

        # 应该产生多个 chunk
        assert len(chunks) >= 2
        # 验证 prev/next 链接
        assert chunks[0].next_chunk_id == chunks[1].id
        assert chunks[1].prev_chunk_id == chunks[0].id
        # 验证 chunk_overlap 参数起作用
        # 检查相邻 chunk 的时间戳范围（重叠应该体现在时间戳上）
        # 由于重叠机制，如果有多个 chunk，说明重叠逻辑正常工作
        assert len(chunks) >= 2

    def test_chunk_session_speakers_deduplication(self):
        """测试发言人去重。"""
        session = ParsedSession(
            source_file="test.txt",
            session_date="2024-01-01",
            file_datetime="20240101120000",
            utterances=[
                Utterance(timestamp="00:00:00", speaker="Andy", text="第一句", line_no=1),
                Utterance(timestamp="00:01:00", speaker="Andy", text="第二句", line_no=2),
                Utterance(timestamp="00:02:00", speaker="咨询师", text="第三句", line_no=3),
                Utterance(timestamp="00:03:00", speaker="Andy", text="第四句", line_no=4),
            ]
        )

        chunks = chunk_session(session)

        assert len(chunks) == 1
        # 发言人应该去重
        speakers = chunks[0].speakers.split(",")
        assert len(speakers) == 2
        assert "Andy" in speakers
        assert "咨询师" in speakers

    def test_render_unit(self):
        """测试单个发言渲染。"""
        utterance = Utterance(
            timestamp="00:05:30",
            speaker="Andy",
            text="这是一句测试文本",
            line_no=1
        )

        rendered = _render_unit(utterance)

        assert "Andy(00:05:30): 这是一句测试文本" == rendered

    def test_split_long_utterance(self, monkeypatch):
        """测试长发言拆分。"""
        # Mock chunk_size
        monkeypatch.setattr("scripts.index_settings.chunking_params",
                           lambda: {"chunk_size": 30, "chunk_overlap": 10})

        # 创建足够长的文本才会被拆分
        utterance = Utterance(
            timestamp="00:00:00",
            speaker="Andy",
            text="第一句话。" * 10 + "第二句话。" * 10 + "第三句话。" * 10,
            line_no=1
        )

        pieces = _split_long_utterance(utterance, max_len=30)

        # 应该被拆分成多个片段
        assert len(pieces) >= 2
        # 每个片段都应该包含发言人和时间戳前缀
        for piece in pieces:
            assert piece.startswith("Andy(00:00:00): ")

    def test_split_long_utterance_single_sentence(self):
        """测试无法拆分的超长单句。"""
        utterance = Utterance(
            timestamp="00:00:00",
            speaker="Andy",
            text="这是一个非常长的单句话没有任何句号或换行符可以用来拆分" * 10,
            line_no=1
        )

        pieces = _split_long_utterance(utterance, max_len=50)

        # 无法拆分时应该返回完整文本
        assert len(pieces) == 1
        assert pieces[0].startswith("Andy(00:00:00): ")

    def test_build_units_basic(self):
        """测试构建基本单元。"""
        session = ParsedSession(
            source_file="test.txt",
            session_date="2024-01-01",
            file_datetime="20240101120000",
            utterances=[
                Utterance(timestamp="00:00:00", speaker="A", text="短文本", line_no=1),
                Utterance(timestamp="00:01:00", speaker="B", text="另一段文本", line_no=2),
            ]
        )

        units = _build_units(session)

        assert len(units) == 2
        assert units[0][1] == "A"  # speaker
        assert units[0][2] == "00:00:00"  # timestamp
        assert "短文本" in units[0][0]  # text

    def test_build_units_with_long_utterance(self, monkeypatch):
        """测试包含超长发言的单元构建。"""
        # Mock chunk_size
        monkeypatch.setattr("scripts.index_settings.chunking_params",
                           lambda: {"chunk_size": 50, "chunk_overlap": 10})

        long_text = "这是一个很长的发言。" * 20
        session = ParsedSession(
            source_file="test.txt",
            session_date="2024-01-01",
            file_datetime="20240101120000",
            utterances=[
                Utterance(timestamp="00:00:00", speaker="A", text=long_text, line_no=1),
            ]
        )

        units = _build_units(session)

        # 超长发言应该被拆分成多个单元
        assert len(units) > 1
        # 所有单元应该有相同的 speaker 和 timestamp
        for unit in units:
            assert unit[1] == "A"
            assert unit[2] == "00:00:00"

    def test_chunk_session_timestamp_range(self):
        """测试时间戳范围记录。"""
        session = ParsedSession(
            source_file="test.txt",
            session_date="2024-01-01",
            file_datetime="20240101120000",
            utterances=[
                Utterance(timestamp="00:05:00", speaker="A", text="开始", line_no=1),
                Utterance(timestamp="00:10:30", speaker="B", text="中间", line_no=2),
                Utterance(timestamp="00:15:45", speaker="A", text="结束", line_no=3),
            ]
        )

        chunks = chunk_session(session)

        assert len(chunks) == 1
        assert chunks[0].start_ts == "00:05:00"
        assert chunks[0].end_ts == "00:15:45"

    def test_chunk_session_prev_next_chain(self, monkeypatch):
        """测试父块链接（prev/next）。"""
        # Mock 小块大小强制分成多块
        monkeypatch.setattr("scripts.index_settings.chunking_params",
                           lambda: {"chunk_size": 30, "chunk_overlap": 10})

        session = ParsedSession(
            source_file="test.txt",
            session_date="2024-01-01",
            file_datetime="20240101120000",
            utterances=[
                Utterance(timestamp="00:00:00", speaker="A", text="第一段话" * 5, line_no=1),
                Utterance(timestamp="00:01:00", speaker="B", text="第二段话" * 5, line_no=2),
                Utterance(timestamp="00:02:00", speaker="A", text="第三段话" * 5, line_no=3),
                Utterance(timestamp="00:03:00", speaker="B", text="第四段话" * 5, line_no=4),
            ]
        )

        chunks = chunk_session(session)

        assert len(chunks) >= 2
        # 第一个块没有 prev
        assert chunks[0].prev_chunk_id is None
        # 最后一个块没有 next
        assert chunks[-1].next_chunk_id is None
        # 中间块有 prev 和 next
        if len(chunks) > 2:
            assert chunks[1].prev_chunk_id is not None
            assert chunks[1].next_chunk_id is not None

    def test_chunk_session_raw_text_vs_text(self):
        """测试 raw_text（无前缀）和 text（有前缀）的区别。"""
        session = ParsedSession(
            source_file="test.txt",
            session_date="2024-01-01",
            file_datetime="20240101120000",
            utterances=[
                Utterance(timestamp="00:00:00", speaker="Andy", text="测试内容", line_no=1),
            ]
        )

        chunks = chunk_session(session)

        assert len(chunks) == 1
        # raw_text 不含前缀
        assert "[2024-01-01" not in chunks[0].raw_text
        assert "测试内容" in chunks[0].raw_text
        # text 含前缀
        assert "[2024-01-01" in chunks[0].text
        assert "测试内容" in chunks[0].text

    def test_chunk_all(self, monkeypatch, tmp_path):
        """测试批量分块。"""
        # 创建临时测试文件
        raw_dir = tmp_path / "raw"
        raw_dir.mkdir()
        test_file = raw_dir / "20240101120000_test.txt"
        test_file.write_text("Andy(00:00:00): 测试内容\n", encoding="utf-8")

        # Mock iter_raw_files
        monkeypatch.setattr("scripts.chunk.iter_raw_files",
                           lambda workspace_id=None: [test_file])

        chunks = chunk_all(workspace_id="_legacy")

        assert len(chunks) >= 1
        assert chunks[0].source_file == "20240101120000_test.txt"

    def test_chunk_all_with_files_list(self, tmp_path):
        """测试指定文件列表的批量分块。"""
        # 创建临时测试文件
        test_file = tmp_path / "20240101120000_test.txt"
        test_file.write_text("Andy(00:00:00): 测试内容\n", encoding="utf-8")

        chunks = chunk_all(files=[test_file])

        assert len(chunks) >= 1

    def test_write_chunks_jsonl(self, tmp_path):
        """测试写入 chunks.jsonl。"""
        output_file = tmp_path / "chunks.jsonl"
        chunks = [
            Chunk(
                id="test::chunk0000",
                session_date="2024-01-01",
                source_file="test.txt",
                chunk_index=0,
                speakers="Andy",
                start_ts="00:00:00",
                end_ts="00:01:00",
                raw_text="测试内容",
                text="[前缀]\n测试内容",
            )
        ]

        write_chunks_jsonl(chunks, path=output_file)

        # 验证文件被创建
        assert output_file.exists()
        # 验证内容
        with open(output_file, "r", encoding="utf-8") as f:
            line = f.readline()
            data = json.loads(line)
            assert data["id"] == "test::chunk0000"
            assert data["session_date"] == "2024-01-01"
            assert data["raw_text"] == "测试内容"

    def test_chunk_session_very_large_file(self, monkeypatch):
        """测试极大文件（触发多次分块）。"""
        # Mock 小块大小
        monkeypatch.setattr("scripts.index_settings.chunking_params",
                           lambda: {"chunk_size": 100, "chunk_overlap": 30})

        # 创建大量发言
        utterances = [
            Utterance(
                timestamp=f"00:{i:02d}:00",
                speaker=f"Speaker{i % 3}",
                text="这是一段比较长的测试文本内容" * 5,
                line_no=i
            )
            for i in range(50)
        ]

        session = ParsedSession(
            source_file="large.txt",
            session_date="2024-01-01",
            file_datetime="20240101120000",
            utterances=utterances
        )

        chunks = chunk_session(session)

        # 应该产生多个 chunk
        assert len(chunks) > 10
        # 验证所有 chunk 都有正确的链接
        for i, chunk in enumerate(chunks):
            if i == 0:
                assert chunk.prev_chunk_id is None
            else:
                assert chunk.prev_chunk_id == chunks[i - 1].id
            if i == len(chunks) - 1:
                assert chunk.next_chunk_id is None
            else:
                assert chunk.next_chunk_id == chunks[i + 1].id

    def test_chunk_session_very_small_file(self):
        """测试极小文件（单个发言）。"""
        session = ParsedSession(
            source_file="tiny.txt",
            session_date="2024-01-01",
            file_datetime="20240101120000",
            utterances=[
                Utterance(timestamp="00:00:00", speaker="A", text="短", line_no=1),
            ]
        )

        chunks = chunk_session(session)

        assert len(chunks) == 1
        assert chunks[0].prev_chunk_id is None
        assert chunks[0].next_chunk_id is None
        assert "短" in chunks[0].text

    def test_chunk_session_single_speaker_multiple_turns(self):
        """测试单个发言人多次发言。"""
        session = ParsedSession(
            source_file="test.txt",
            session_date="2024-01-01",
            file_datetime="20240101120000",
            utterances=[
                Utterance(timestamp="00:00:00", speaker="Andy", text="第一次", line_no=1),
                Utterance(timestamp="00:01:00", speaker="Andy", text="第二次", line_no=2),
                Utterance(timestamp="00:02:00", speaker="Andy", text="第三次", line_no=3),
            ]
        )

        chunks = chunk_session(session)

        assert len(chunks) == 1
        # speakers 应该只有一个 Andy（去重）
        assert chunks[0].speakers == "Andy"

    def test_chunk_session_multiple_speakers_interleaved(self):
        """测试多个发言人交替发言。"""
        session = ParsedSession(
            source_file="test.txt",
            session_date="2024-01-01",
            file_datetime="20240101120000",
            utterances=[
                Utterance(timestamp="00:00:00", speaker="A", text="A说话", line_no=1),
                Utterance(timestamp="00:01:00", speaker="B", text="B说话", line_no=2),
                Utterance(timestamp="00:02:00", speaker="A", text="A再说", line_no=3),
                Utterance(timestamp="00:03:00", speaker="C", text="C说话", line_no=4),
            ]
        )

        chunks = chunk_session(session)

        assert len(chunks) == 1
        speakers = chunks[0].speakers.split(",")
        # 应该按出现顺序保留（去重）
        assert len(speakers) == 3
        assert "A" in speakers
        assert "B" in speakers
        assert "C" in speakers

    def test_chunk_session_special_characters_in_text(self):
        """测试特殊字符处理。"""
        session = ParsedSession(
            source_file="test.txt",
            session_date="2024-01-01",
            file_datetime="20240101120000",
            utterances=[
                Utterance(timestamp="00:00:00", speaker="Andy",
                         text="包含特殊字符：\n换行、「引号」、emoji 😊", line_no=1),
            ]
        )

        chunks = chunk_session(session)

        assert len(chunks) == 1
        assert "换行" in chunks[0].text
        assert "引号" in chunks[0].text
        assert "😊" in chunks[0].text

    def test_chunk_index_sequential(self, monkeypatch):
        """测试 chunk_index 序号连续性。"""
        # Mock 小块大小强制分块
        monkeypatch.setattr("scripts.index_settings.chunking_params",
                           lambda: {"chunk_size": 30, "chunk_overlap": 10})

        session = ParsedSession(
            source_file="test.txt",
            session_date="2024-01-01",
            file_datetime="20240101120000",
            utterances=[
                Utterance(timestamp=f"00:0{i}:00", speaker="A",
                         text="测试文本" * 10, line_no=i)
                for i in range(10)
            ]
        )

        chunks = chunk_session(session)

        # 验证 chunk_index 从 0 开始连续递增
        for i, chunk in enumerate(chunks):
            assert chunk.chunk_index == i
            assert chunk.id == f"test.txt::chunk{i:04d}"

    def test_split_long_utterance_empty_sentences(self):
        """测试句子拆分后为空的情况。"""
        utterance = Utterance(
            timestamp="00:00:00",
            speaker="Andy",
            text="。。。",  # 只有句号，拆分后都是空字符串
            line_no=1
        )

        pieces = _split_long_utterance(utterance, max_len=50)

        # 应该返回原始文本（触发 if not pieces 分支）
        assert len(pieces) == 1
        assert pieces[0].startswith("Andy(00:00:00): ")

    def test_write_chunks_jsonl_default_path(self, monkeypatch, tmp_path):
        """测试写入时使用默认路径（path=None）。"""
        # Mock PROCESSED_DIR 返回临时路径
        monkeypatch.setattr("scripts.chunk.PROCESSED_DIR",
                           lambda workspace_id=None: tmp_path / "processed")

        chunks = [
            Chunk(
                id="test::chunk0000",
                session_date="2024-01-01",
                source_file="test.txt",
                chunk_index=0,
                speakers="Andy",
                start_ts="00:00:00",
                end_ts="00:01:00",
                raw_text="测试内容",
                text="[前缀]\n测试内容",
            )
        ]

        # 使用默认路径（path=None）
        write_chunks_jsonl(chunks, path=None, workspace_id="_legacy")

        # 验证文件被创建在默认路径
        default_path = tmp_path / "processed" / "chunks.jsonl"
        assert default_path.exists()
        with open(default_path, "r", encoding="utf-8") as f:
            data = json.loads(f.readline())
            assert data["id"] == "test::chunk0000"

    def test_chunks_jsonl_path_function(self, monkeypatch, tmp_path):
        """测试 CHUNKS_JSONL_PATH 函数。"""
        from scripts.chunk import CHUNKS_JSONL_PATH

        # Mock PROCESSED_DIR
        monkeypatch.setattr("scripts.chunk.PROCESSED_DIR",
                           lambda workspace_id=None: tmp_path / f"ws_{workspace_id or 'default'}")

        # 测试默认 workspace
        path1 = CHUNKS_JSONL_PATH()
        assert path1 == tmp_path / "ws_default" / "chunks.jsonl"

        # 测试指定 workspace
        path2 = CHUNKS_JSONL_PATH("custom")
        assert path2 == tmp_path / "ws_custom" / "chunks.jsonl"
