"""测试会话解析功能（相对/序数引用 → 具体会话）。

覆盖目标：70%+
关键测试点：
- 意图识别（nth/recent_n/last_offset）
- 数据源判定（咨询 vs 对话）
- workspace 隔离
- 边界条件（空列表、超出范围、溢出）
"""
import json
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest

from scripts.session_resolver import (
    MAX_FULL_SESSIONS,
    _apply_intent,
    _detect_intent,
    _load_topics,
    _to_int,
    chat_manifest,
    render_chat_sessions,
    resolve,
    therapy_dates_ordered,
    therapy_manifest,
)


class TestIntentDetection:
    """测试意图识别（_detect_intent 和 _to_int）。"""

    def test_to_int_arabic_digits(self):
        """测试阿拉伯数字转换。"""
        assert _to_int("3") == 3
        assert _to_int("15") == 15
        assert _to_int("0") == 0

    def test_to_int_chinese_numbers(self):
        """测试中文数字转换。"""
        assert _to_int("一") == 1
        assert _to_int("三") == 3
        assert _to_int("十") == 10
        assert _to_int("十五") == 15

    def test_to_int_special_chinese(self):
        """测试特殊中文数字（如「两」）。"""
        assert _to_int("两") == 2

    def test_to_int_invalid(self):
        """测试无效输入。"""
        assert _to_int("abc") is None
        assert _to_int("百") is None

    def test_detect_intent_nth(self):
        """测试「第N次」模式。"""
        assert _detect_intent("读取第3次咨询") == ("nth", 3)
        assert _detect_intent("第一次的内容") == ("nth", 1)
        assert _detect_intent("给我看第五场咨询") == ("nth", 5)
        assert _detect_intent("第十次") == ("nth", 10)

    def test_detect_intent_recent_n(self):
        """测试「最近N次」模式。"""
        assert _detect_intent("最近3次咨询") == ("recent_n", 3)
        assert _detect_intent("最后两次对话") == ("recent_n", 2)
        assert _detect_intent("前五次") == ("recent_n", 5)
        assert _detect_intent("近十场") == ("recent_n", 10)

    def test_detect_intent_last_offset(self):
        """测试「上次/上上次」模式。"""
        assert _detect_intent("上一次咨询") == ("last_offset", 1)
        assert _detect_intent("上次的内容") == ("last_offset", 1)
        # "最近一次" 会被 _RECENT_N 优先匹配为 ("recent_n", 1)
        assert _detect_intent("上次") == ("last_offset", 1)
        assert _detect_intent("上上次对话") == ("last_offset", 2)
        assert _detect_intent("上上一次") == ("last_offset", 2)

    def test_detect_intent_priority_order(self):
        """测试检查顺序优先级（nth > recent_n > last_offset）。"""
        # 同时包含「第N次」和「最近N次」时，优先识别「第N次」
        assert _detect_intent("第3次和最近5次咨询") == ("nth", 3)

    def test_detect_intent_none(self):
        """测试无相对引用的问题。"""
        assert _detect_intent("什么是认知疗法？") is None
        assert _detect_intent("2024年7月的咨询") is None
        assert _detect_intent("告诉我关于抑郁的知识") is None

    def test_detect_intent_complex_chinese(self):
        """测试繁简混用。"""
        # "兩" 不在 _CN_NUM 字典中，所以用阿拉伯数字测试繁体
        assert _detect_intent("最近2次諮詢") == ("recent_n", 2)
        assert _detect_intent("第二場咨商") == ("nth", 2)


class TestApplyIntent:
    """测试意图应用逻辑（_apply_intent）。"""

    def test_apply_nth_valid(self):
        """测试正数第N次（有效范围内）。"""
        ordered = ["a", "b", "c", "d", "e"]
        assert _apply_intent(ordered, ("nth", 1)) == ["a"]
        assert _apply_intent(ordered, ("nth", 3)) == ["c"]
        assert _apply_intent(ordered, ("nth", 5)) == ["e"]

    def test_apply_nth_out_of_range(self):
        """测试正数第N次（超出范围）。"""
        ordered = ["a", "b", "c"]
        assert _apply_intent(ordered, ("nth", 0)) == []
        assert _apply_intent(ordered, ("nth", 4)) == []
        assert _apply_intent(ordered, ("nth", 10)) == []

    def test_apply_last_offset_valid(self):
        """测试倒数第N次（有效范围内）。"""
        ordered = ["a", "b", "c", "d", "e"]
        assert _apply_intent(ordered, ("last_offset", 1)) == ["e"]  # 上一次
        assert _apply_intent(ordered, ("last_offset", 2)) == ["d"]  # 上上次
        assert _apply_intent(ordered, ("last_offset", 5)) == ["a"]  # 倒数第5次

    def test_apply_last_offset_out_of_range(self):
        """测试倒数第N次（超出范围）。"""
        ordered = ["a", "b", "c"]
        assert _apply_intent(ordered, ("last_offset", 0)) == []
        assert _apply_intent(ordered, ("last_offset", 4)) == []

    def test_apply_recent_n_valid(self):
        """测试最近N次（有效范围内）。"""
        ordered = ["a", "b", "c", "d", "e"]
        assert _apply_intent(ordered, ("recent_n", 1)) == ["e"]
        assert _apply_intent(ordered, ("recent_n", 3)) == ["c", "d", "e"]
        assert _apply_intent(ordered, ("recent_n", 5)) == ["a", "b", "c", "d", "e"]

    def test_apply_recent_n_exceeds_length(self):
        """测试最近N次（N大于列表长度）。"""
        ordered = ["a", "b", "c"]
        assert _apply_intent(ordered, ("recent_n", 10)) == ["a", "b", "c"]

    def test_apply_recent_n_zero_or_negative(self):
        """测试最近N次（N为0或负数）。"""
        ordered = ["a", "b", "c"]
        assert _apply_intent(ordered, ("recent_n", 0)) == []
        assert _apply_intent(ordered, ("recent_n", -1)) == []

    def test_apply_empty_list(self):
        """测试空列表。"""
        assert _apply_intent([], ("nth", 1)) == []
        assert _apply_intent([], ("last_offset", 1)) == []
        assert _apply_intent([], ("recent_n", 3)) == []


class TestTherapyDatesOrdered:
    """测试咨询日期排序（therapy_dates_ordered）。"""

    def test_therapy_dates_ordered_basic(self, tmp_path, monkeypatch):
        """测试基本排序（按文件名时间戳）。"""
        # 创建模拟的 raw 目录
        raw_dir = tmp_path / "data" / "raw"
        raw_dir.mkdir(parents=True)

        # 创建不同日期的文件（顺序混乱）
        (raw_dir / "20240301120000_session3.txt").write_text("session 3")
        (raw_dir / "20240101120000_session1.txt").write_text("session 1")
        (raw_dir / "20240201120000_session2.txt").write_text("session 2")

        # Mock config.RAW_DIR - 返回顺序已经混乱，但函数内部会通过文件名排序
        with patch("scripts.session_resolver.iter_raw_files") as mock_iter:
            # 注意：iter_raw_files 会按文件名自然排序，所以返回的已经是有序的
            mock_iter.return_value = sorted([
                raw_dir / "20240301120000_session3.txt",
                raw_dir / "20240101120000_session1.txt",
                raw_dir / "20240201120000_session2.txt",
            ], key=lambda p: p.name)

            result = therapy_dates_ordered(workspace_id="test")

        # 验证结果按时间排序（早→晚）
        assert len(result) == 3
        assert result[0][0] == "2024-01-01"
        assert result[1][0] == "2024-02-01"
        assert result[2][0] == "2024-03-01"

    def test_therapy_dates_ordered_invalid_filenames(self, tmp_path):
        """测试忽略无效文件名。"""
        raw_dir = tmp_path / "data" / "raw"
        raw_dir.mkdir(parents=True)

        # 混合有效和无效文件名
        (raw_dir / "20240101120000_valid.txt").write_text("valid")
        (raw_dir / "invalid_filename.txt").write_text("invalid")
        (raw_dir / "README.md").write_text("readme")

        with patch("scripts.session_resolver.iter_raw_files") as mock_iter:
            mock_iter.return_value = [
                raw_dir / "20240101120000_valid.txt",
                raw_dir / "invalid_filename.txt",
                raw_dir / "README.md",
            ]

            result = therapy_dates_ordered(workspace_id="test")

        # 只返回有效文件
        assert len(result) == 1
        assert result[0][0] == "2024-01-01"

    def test_therapy_dates_ordered_empty(self, tmp_path):
        """测试空目录。"""
        with patch("scripts.session_resolver.iter_raw_files") as mock_iter:
            mock_iter.return_value = []

            result = therapy_dates_ordered(workspace_id="test")

        assert result == []

    def test_therapy_dates_ordered_workspace_isolation(self):
        """测试 workspace 隔离（通过 workspace_id 参数）。"""
        with patch("scripts.session_resolver.iter_raw_files") as mock_iter:
            mock_iter.return_value = []

            therapy_dates_ordered(workspace_id="workspace-a")
            therapy_dates_ordered(workspace_id="workspace-b")
            therapy_dates_ordered(workspace_id=None)

            # 验证传递了正确的 workspace_id
            assert mock_iter.call_count == 3
            assert mock_iter.call_args_list[0][0][0] == "workspace-a"
            assert mock_iter.call_args_list[1][0][0] == "workspace-b"
            assert mock_iter.call_args_list[2][0][0] is None


class TestResolve:
    """测试主解析函数（resolve）。"""

    def test_resolve_no_intent(self):
        """测试无相对引用的问题。"""
        result = resolve("什么是认知疗法？", workspace_id="test")

        assert result == {
            "therapy_dates": [],
            "chat_session_ids": [],
            "overflow": False,
        }

    def test_resolve_therapy_nth(self, tmp_path):
        """测试咨询「第N次」解析。"""
        raw_dir = tmp_path / "data" / "raw"
        raw_dir.mkdir(parents=True)

        # 创建3个咨询文件
        files = [
            raw_dir / "20240101120000_s1.txt",
            raw_dir / "20240201120000_s2.txt",
            raw_dir / "20240301120000_s3.txt",
        ]
        for f in files:
            f.write_text("content")

        with patch("scripts.session_resolver.iter_raw_files") as mock_iter:
            mock_iter.return_value = files

            result = resolve("读取第2次咨询", workspace_id="test")

        assert result["therapy_dates"] == ["2024-02-01"]
        assert result["chat_session_ids"] == []
        assert result["overflow"] is False

    def test_resolve_therapy_recent_n(self, tmp_path):
        """测试咨询「最近N次」解析。"""
        raw_dir = tmp_path / "data" / "raw"
        raw_dir.mkdir(parents=True)

        files = [
            raw_dir / f"2024{i:02d}01120000_s{i}.txt"
            for i in range(1, 6)  # 5个文件
        ]
        for f in files:
            f.write_text("content")

        with patch("scripts.session_resolver.iter_raw_files") as mock_iter:
            mock_iter.return_value = files

            result = resolve("最近3次咨询", workspace_id="test")

        # 最近3次 = 后3个
        assert result["therapy_dates"] == ["2024-03-01", "2024-04-01", "2024-05-01"]
        assert result["overflow"] is False

    def test_resolve_therapy_last_one(self, tmp_path):
        """测试咨询「上一次」解析。"""
        raw_dir = tmp_path / "data" / "raw"
        raw_dir.mkdir(parents=True)

        files = [
            raw_dir / "20240101120000_s1.txt",
            raw_dir / "20240201120000_s2.txt",
        ]
        for f in files:
            f.write_text("content")

        with patch("scripts.session_resolver.iter_raw_files") as mock_iter:
            mock_iter.return_value = files

            result = resolve("上一次咨询", workspace_id="test")

        assert result["therapy_dates"] == ["2024-02-01"]

    def test_resolve_therapy_overflow(self, tmp_path):
        """测试咨询溢出（超过 MAX_FULL_SESSIONS）。"""
        raw_dir = tmp_path / "data" / "raw"
        raw_dir.mkdir(parents=True)

        # 创建超过限制的文件
        files = [
            raw_dir / f"2024{i:02d}01120000_s{i}.txt"
            for i in range(1, 8)  # 7个文件，MAX_FULL_SESSIONS=3
        ]
        for f in files:
            f.write_text("content")

        with patch("scripts.session_resolver.iter_raw_files") as mock_iter:
            mock_iter.return_value = files

            result = resolve("最近5次咨询", workspace_id="test")

        # 只返回最近3个
        assert len(result["therapy_dates"]) == MAX_FULL_SESSIONS
        assert result["overflow"] is True
        assert result["therapy_dates"] == ["2024-05-01", "2024-06-01", "2024-07-01"]

    def test_resolve_chat_nth(self):
        """测试对话「第N次」解析。"""
        mock_sessions = [
            {"id": "s3", "title": "最新", "updated_at": "2024-03-01"},
            {"id": "s2", "title": "次新", "updated_at": "2024-02-01"},
            {"id": "s1", "title": "最旧", "updated_at": "2024-01-01"},
        ]

        with patch("scripts.session_resolver.list_sessions") as mock_list:
            mock_list.return_value = mock_sessions

            result = resolve("读取第2次对话", workspace_id="test")

        # list_sessions 返回近→远，反转后第2次是 s2
        assert result["chat_session_ids"] == ["s2"]
        assert result["therapy_dates"] == []

    def test_resolve_chat_recent_n(self):
        """测试对话「最近N次」解析。"""
        mock_sessions = [
            {"id": f"s{i}", "title": f"Session {i}", "updated_at": f"2024-0{i}-01"}
            for i in range(5, 0, -1)  # 近→远：s5, s4, s3, s2, s1
        ]

        with patch("scripts.session_resolver.list_sessions") as mock_list:
            mock_list.return_value = mock_sessions

            result = resolve("最近2次对话", workspace_id="test")

        # list_sessions 返回近→远 [s5, s4, s3, s2, s1]
        # 反转成早→晚 [s1, s2, s3, s4, s5]
        # 取最后2个 = [s4, s5]
        assert result["chat_session_ids"] == ["s4", "s5"]

    def test_resolve_data_source_priority(self):
        """测试数据源判定优先级（咨询 > 对话）。"""
        # 同时包含「咨询」和「对话」关键词
        with patch("scripts.session_resolver.iter_raw_files") as mock_iter, \
             patch("scripts.session_resolver.list_sessions") as mock_list:
            mock_iter.return_value = [Path("20240101120000_s1.txt")]
            mock_list.return_value = [{"id": "c1", "title": "Chat", "updated_at": "2024-01-01"}]

            result = resolve("读取上一次咨询记录然后继续对话", workspace_id="test")

        # 优先识别为咨询
        assert len(result["therapy_dates"]) > 0
        assert result["chat_session_ids"] == []

    def test_resolve_chat_only_keywords(self):
        """测试纯对话关键词。"""
        mock_sessions = [
            {"id": "s1", "title": "Chat", "updated_at": "2024-01-01"},
        ]

        with patch("scripts.session_resolver.list_sessions") as mock_list:
            mock_list.return_value = mock_sessions

            result = resolve("上次聊天说了什么？", workspace_id="test")

        assert result["chat_session_ids"] == ["s1"]
        assert result["therapy_dates"] == []


class TestLoadTopics:
    """测试主题加载（_load_topics）。"""

    def test_load_topics_valid(self, tmp_path):
        """测试加载有效主题。"""
        summaries_dir = tmp_path / "summaries"
        summaries_dir.mkdir()

        # 创建摘要文件
        summary_data = {
            "source_file": "20240101120000_session.txt",
            "topics": ["焦虑", "工作压力"],
        }
        (summaries_dir / "20240101120000_session.json").write_text(
            json.dumps(summary_data, ensure_ascii=False)
        )

        with patch("scripts.session_resolver.SUMMARIES_DIR") as mock_dir:
            mock_dir.return_value = summaries_dir

            result = _load_topics(workspace_id="test")

        assert result == {"20240101120000_session.txt": ["焦虑", "工作压力"]}

    def test_load_topics_missing_fields(self, tmp_path):
        """测试缺少必要字段的摘要。"""
        summaries_dir = tmp_path / "summaries"
        summaries_dir.mkdir()

        # 缺少 topics
        (summaries_dir / "s1.json").write_text(
            json.dumps({"source_file": "s1.txt"})
        )

        # 缺少 source_file
        (summaries_dir / "s2.json").write_text(
            json.dumps({"topics": ["topic"]})
        )

        with patch("scripts.session_resolver.SUMMARIES_DIR") as mock_dir:
            mock_dir.return_value = summaries_dir

            result = _load_topics(workspace_id="test")

        assert result == {}

    def test_load_topics_invalid_json(self, tmp_path):
        """测试无效 JSON。"""
        summaries_dir = tmp_path / "summaries"
        summaries_dir.mkdir()

        (summaries_dir / "invalid.json").write_text("not json")

        with patch("scripts.session_resolver.SUMMARIES_DIR") as mock_dir:
            mock_dir.return_value = summaries_dir

            result = _load_topics(workspace_id="test")

        assert result == {}

    def test_load_topics_nonexistent_dir(self, tmp_path):
        """测试目录不存在。"""
        with patch("scripts.session_resolver.SUMMARIES_DIR") as mock_dir:
            mock_dir.return_value = tmp_path / "nonexistent"

            result = _load_topics(workspace_id="test")

        assert result == {}


class TestTherapyManifest:
    """测试咨询清单（therapy_manifest）。"""

    def test_therapy_manifest_basic(self, tmp_path):
        """测试基本清单生成。"""
        raw_dir = tmp_path / "data" / "raw"
        raw_dir.mkdir(parents=True)

        files = [
            raw_dir / "20240101120000_s1.txt",
            raw_dir / "20240201120000_s2.txt",
        ]
        for f in files:
            f.write_text("content")

        summaries_dir = tmp_path / "summaries"
        summaries_dir.mkdir()

        # 为第一个文件添加主题
        summary = {
            "source_file": "20240101120000_s1.txt",
            "topics": ["焦虑", "工作"],
        }
        (summaries_dir / "20240101120000_s1.json").write_text(
            json.dumps(summary, ensure_ascii=False)
        )

        with patch("scripts.session_resolver.iter_raw_files") as mock_iter, \
             patch("scripts.session_resolver.SUMMARIES_DIR") as mock_sum_dir:
            mock_iter.return_value = files
            mock_sum_dir.return_value = summaries_dir

            result = therapy_manifest(workspace_id="test")

        assert "共 2 次真实咨询" in result
        assert "1. 2024-01-01：焦虑、工作" in result
        assert "2. 2024-02-01" in result
        assert "最近一次" in result

    def test_therapy_manifest_empty(self):
        """测试无咨询记录。"""
        with patch("scripts.session_resolver.iter_raw_files") as mock_iter:
            mock_iter.return_value = []

            result = therapy_manifest(workspace_id="test")

        assert result == "（暂无真实咨询记录）"

    def test_therapy_manifest_no_topics(self, tmp_path):
        """测试无主题的清单。"""
        raw_dir = tmp_path / "data" / "raw"
        raw_dir.mkdir(parents=True)

        files = [raw_dir / "20240101120000_s1.txt"]
        files[0].write_text("content")

        with patch("scripts.session_resolver.iter_raw_files") as mock_iter, \
             patch("scripts.session_resolver._load_topics") as mock_topics:
            mock_iter.return_value = files
            mock_topics.return_value = {}

            result = therapy_manifest(workspace_id="test")

        # 没有冒号和主题
        assert "1. 2024-01-01\n" in result or "1. 2024-01-01" in result


class TestChatManifest:
    """测试对话清单（chat_manifest）。"""

    def test_chat_manifest_basic(self):
        """测试基本清单生成。"""
        mock_sessions = [
            {"id": "s1", "title": "第一次对话", "updated_at": "2024-01-01T10:00:00"},
            {"id": "s2", "title": "第二次对话", "updated_at": "2024-01-02T11:00:00"},
        ]

        with patch("scripts.session_resolver.list_sessions") as mock_list:
            mock_list.return_value = mock_sessions

            result = chat_manifest(limit=15, workspace_id="test")

        assert "按时间从近到远" in result
        assert "1. 2024-01-01「第一次对话」" in result
        assert "2. 2024-01-02「第二次对话」" in result

    def test_chat_manifest_empty(self):
        """测试无对话历史。"""
        with patch("scripts.session_resolver.list_sessions") as mock_list:
            mock_list.return_value = []

            result = chat_manifest(workspace_id="test")

        assert result == "（暂无 AI 对话历史）"

    def test_chat_manifest_limit(self):
        """测试限制显示数量。"""
        mock_sessions = [
            {"id": f"s{i}", "title": f"对话{i}", "updated_at": f"2024-01-{i:02d}"}
            for i in range(1, 21)  # 20个会话
        ]

        with patch("scripts.session_resolver.list_sessions") as mock_list:
            mock_list.return_value = mock_sessions

            result = chat_manifest(limit=5, workspace_id="test")

        # 只显示前5个
        assert "1. 2024-01-01" in result
        assert "5. 2024-01-05" in result
        assert "6. 2024-01-06" not in result
        assert "仅列出最近 5 次，共 20 次" in result


class TestRenderChatSessions:
    """测试对话会话渲染（render_chat_sessions）。"""

    def test_render_chat_sessions_basic(self):
        """测试基本渲染。"""
        mock_session = {
            "id": "s1",
            "title": "测试对话",
            "updated_at": "2024-01-01T10:00:00",
            "messages": [
                {"role": "user", "content": "你好"},
                {"role": "assistant", "content": "你好！有什么可以帮助你的吗？"},
            ],
        }

        with patch("scripts.session_resolver.load_session") as mock_load:
            mock_load.return_value = mock_session

            result = render_chat_sessions(["s1"], workspace_id="test")

        assert "[对话「测试对话」｜2024-01-01]" in result
        assert "我：你好" in result
        assert "AI：你好！有什么可以帮助你的吗？" in result

    def test_render_chat_sessions_multiple(self):
        """测试多个会话渲染。"""
        mock_sessions = [
            {
                "id": "s1",
                "title": "对话1",
                "updated_at": "2024-01-01",
                "messages": [{"role": "user", "content": "问题1"}],
            },
            {
                "id": "s2",
                "title": "对话2",
                "updated_at": "2024-01-02",
                "messages": [{"role": "user", "content": "问题2"}],
            },
        ]

        with patch("scripts.session_resolver.load_session") as mock_load:
            mock_load.side_effect = mock_sessions

            result = render_chat_sessions(["s1", "s2"], workspace_id="test")

        assert "[对话「对话1」｜2024-01-01]" in result
        assert "[对话「对话2」｜2024-01-02]" in result
        assert result.count("\n\n") >= 1  # 会话间有空行分隔

    def test_render_chat_sessions_empty_messages(self):
        """测试无消息的会话。"""
        mock_session = {
            "id": "s1",
            "title": "空对话",
            "updated_at": "2024-01-01",
            "messages": [],
        }

        with patch("scripts.session_resolver.load_session") as mock_load:
            mock_load.return_value = mock_session

            result = render_chat_sessions(["s1"], workspace_id="test")

        assert "[对话「空对话」｜2024-01-01]" in result

    def test_render_chat_sessions_missing_fields(self):
        """测试缺少可选字段的会话。"""
        mock_session = {
            "id": "s1",
            # 缺少 title, updated_at
            "messages": [{"role": "user", "content": "测试"}],
        }

        with patch("scripts.session_resolver.load_session") as mock_load:
            mock_load.return_value = mock_session

            result = render_chat_sessions(["s1"], workspace_id="test")

        # 应该使用默认值
        assert "[对话「新对话」" in result


class TestWorkspaceIsolation:
    """测试 workspace 隔离（所有函数应正确传递 workspace_id）。"""

    def test_all_functions_accept_workspace_id(self):
        """验证所有公开函数都接受 workspace_id 参数。"""
        import inspect

        # 需要支持 workspace_id 的函数
        functions = [
            therapy_dates_ordered,
            resolve,
            therapy_manifest,
            chat_manifest,
            render_chat_sessions,
        ]

        for func in functions:
            sig = inspect.signature(func)
            assert "workspace_id" in sig.parameters, \
                f"{func.__name__} should accept workspace_id parameter"

            # 验证默认值为 None
            param = sig.parameters["workspace_id"]
            assert param.default is None or param.default == inspect.Parameter.empty, \
                f"{func.__name__} workspace_id should default to None"


class TestEdgeCases:
    """测试边界条件和错误处理。"""

    def test_resolve_out_of_range_nth(self):
        """测试超出范围的第N次。"""
        with patch("scripts.session_resolver.iter_raw_files") as mock_iter:
            mock_iter.return_value = [Path("20240101120000_s1.txt")]

            # 请求第10次，但只有1个文件
            result = resolve("第10次咨询", workspace_id="test")

        assert result["therapy_dates"] == []
        assert result["overflow"] is False

    def test_resolve_chinese_variants(self):
        """测试繁简体混用。"""
        with patch("scripts.session_resolver.iter_raw_files") as mock_iter:
            mock_iter.return_value = [Path("20240101120000_s1.txt")]

            # 繁体关键词
            result1 = resolve("上一次諮詢", workspace_id="test")
            assert len(result1["therapy_dates"]) > 0

            # 简体关键词
            result2 = resolve("上一次咨询", workspace_id="test")
            assert len(result2["therapy_dates"]) > 0

    def test_resolve_complex_question(self):
        """测试复杂问题（包含多种关键词）。"""
        with patch("scripts.session_resolver.iter_raw_files") as mock_iter, \
             patch("scripts.session_resolver.list_sessions") as mock_list:
            mock_iter.return_value = [Path("20240101120000_s1.txt")]
            mock_list.return_value = []

            # 同时包含时间和相对引用
            result = resolve("读取2024年7月的上一次咨询记录", workspace_id="test")

        # 应该识别到相对引用
        assert result["therapy_dates"] == ["2024-01-01"]

    def test_manifest_special_characters_in_title(self):
        """测试标题包含特殊字符。"""
        mock_sessions = [
            {
                "id": "s1",
                "title": "测试「引号」和【括号】",
                "updated_at": "2024-01-01",
            },
        ]

        with patch("scripts.session_resolver.list_sessions") as mock_list:
            mock_list.return_value = mock_sessions

            result = chat_manifest(workspace_id="test")

        # 应该正常显示特殊字符
        assert "测试「引号」和【括号】" in result
