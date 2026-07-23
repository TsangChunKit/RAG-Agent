"""测试文本分块功能。"""
from scripts.chunk import chunk_session, contextual_prefix
from scripts.parse import ParsedSession, Utterance


class TestChunking:
    """分块逻辑测试。"""

    def test_chunk_session_basic(self, isolated_workspace):
        """测试基本分块功能。"""
        # 创建测试 session
        session = ParsedSession(
            source_file="20240101120000_test.txt",
            session_date="2024-01-01",
            file_datetime="20240101120000",
            utterances=[
                Utterance(timestamp="00:00:15", speaker="Andy", text="第一句话"),
                Utterance(timestamp="00:01:00", speaker="咨询师", text="第二句话"),
                Utterance(timestamp="00:02:00", speaker="Andy", text="第三句话"),
            ]
        )

        # 分块
        chunks = chunk_session(session, workspace_id="_legacy")

        # 验证
        assert len(chunks) >= 1
        assert chunks[0].source_file == "20240101120000_test.txt"
        assert chunks[0].session_date == "2024-01-01"
        assert "第一句话" in chunks[0].text

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
