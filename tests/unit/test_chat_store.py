"""测试聊天会话本地持久化功能。

测试策略：
- 使用 tmp_path fixture 隔离文件系统操作
- Mock config.CHAT_SESSIONS_DIR 返回临时目录
- 验证 JSON 序列化/反序列化正确性
- 验证 workspace 隔离逻辑
- 覆盖边缘情况：空数据、不存在的会话、损坏的 JSON
"""
from typing import Optional
import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from scripts.chat_store import (
    new_session_id,
    list_sessions,
    load_session,
    save_session,
    delete_session,
    make_title,
    _session_path,
)


class TestNewSessionId:
    """测试会话 ID 生成。"""

    def test_new_session_id_format(self):
        """测试生成的 ID 格式正确（12 字符 hex）。"""
        session_id = new_session_id()

        assert isinstance(session_id, str)
        assert len(session_id) == 12
        # 验证是合法的 hex 字符串
        int(session_id, 16)  # 如果不是 hex 会抛异常

    def test_new_session_id_uniqueness(self):
        """测试连续生成的 ID 不重复。"""
        ids = [new_session_id() for _ in range(100)]

        assert len(set(ids)) == 100  # 所有 ID 都不同


class TestSessionPath:
    """测试会话文件路径生成。"""

    def test_session_path_default_workspace(self, tmp_path):
        """测试默认 workspace 的路径。"""
        mock_sessions_dir = tmp_path / "sessions"
        mock_sessions_dir.mkdir()

        with patch("scripts.chat_store.CHAT_SESSIONS_DIR", return_value=mock_sessions_dir):
            path = _session_path("abc123")

            assert path == mock_sessions_dir / "abc123.json"

    def test_session_path_with_workspace_id(self, tmp_path):
        """测试指定 workspace 的路径。"""
        mock_sessions_dir = tmp_path / "workspace1_sessions"
        mock_sessions_dir.mkdir()

        with patch("scripts.chat_store.CHAT_SESSIONS_DIR", return_value=mock_sessions_dir):
            path = _session_path("abc123", workspace_id="workspace1")

            assert path == mock_sessions_dir / "abc123.json"


class TestListSessions:
    """测试会话列表功能。"""

    def test_list_sessions_empty_directory(self, tmp_path):
        """测试空目录返回空列表。"""
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()

        with patch("scripts.chat_store.CHAT_SESSIONS_DIR", return_value=sessions_dir):
            sessions = list_sessions()

            assert sessions == []

    def test_list_sessions_nonexistent_directory(self, tmp_path):
        """测试不存在的目录返回空列表（不抛异常）。"""
        sessions_dir = tmp_path / "nonexistent"

        with patch("scripts.chat_store.CHAT_SESSIONS_DIR", return_value=sessions_dir):
            sessions = list_sessions()

            assert sessions == []

    def test_list_sessions_single_session(self, tmp_path):
        """测试列出单个会话。"""
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()

        # 创建测试会话文件
        session_data = {
            "id": "abc123",
            "title": "测试会话",
            "created_at": "2024-01-01T00:00:00+00:00",
            "updated_at": "2024-01-02T12:00:00+00:00",
            "messages": [{"role": "user", "content": "Hello"}],
        }
        (sessions_dir / "abc123.json").write_text(
            json.dumps(session_data, ensure_ascii=False), encoding="utf-8"
        )

        with patch("scripts.chat_store.CHAT_SESSIONS_DIR", return_value=sessions_dir):
            sessions = list_sessions()

            assert len(sessions) == 1
            assert sessions[0]["id"] == "abc123"
            assert sessions[0]["title"] == "测试会话"
            assert sessions[0]["updated_at"] == "2024-01-02T12:00:00+00:00"
            # 验证不包含 messages（节省内存）
            assert "messages" not in sessions[0]

    def test_list_sessions_multiple_sorted_by_updated_at(self, tmp_path):
        """测试多个会话按更新时间倒序排列。"""
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()

        # 创建三个会话，不同更新时间
        sessions_data = [
            {
                "id": "session1",
                "title": "旧会话",
                "updated_at": "2024-01-01T10:00:00+00:00",
            },
            {
                "id": "session2",
                "title": "最新会话",
                "updated_at": "2024-01-03T15:00:00+00:00",
            },
            {
                "id": "session3",
                "title": "中等会话",
                "updated_at": "2024-01-02T12:00:00+00:00",
            },
        ]

        for data in sessions_data:
            (sessions_dir / f"{data['id']}.json").write_text(
                json.dumps(data, ensure_ascii=False), encoding="utf-8"
            )

        with patch("scripts.chat_store.CHAT_SESSIONS_DIR", return_value=sessions_dir):
            sessions = list_sessions()

            assert len(sessions) == 3
            # 验证倒序：最新的在前
            assert sessions[0]["id"] == "session2"
            assert sessions[1]["id"] == "session3"
            assert sessions[2]["id"] == "session1"

    def test_list_sessions_default_title_when_missing(self, tmp_path):
        """测试缺少 title 时使用默认值。"""
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()

        # 创建没有 title 的会话
        session_data = {
            "id": "abc123",
            "updated_at": "2024-01-01T00:00:00+00:00",
        }
        (sessions_dir / "abc123.json").write_text(
            json.dumps(session_data, ensure_ascii=False), encoding="utf-8"
        )

        with patch("scripts.chat_store.CHAT_SESSIONS_DIR", return_value=sessions_dir):
            sessions = list_sessions()

            assert sessions[0]["title"] == "新对话"

    def test_list_sessions_skip_invalid_json(self, tmp_path):
        """测试跳过损坏的 JSON 文件（不影响其他会话）。"""
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()

        # 创建一个有效会话
        valid_session = {
            "id": "valid",
            "title": "有效会话",
            "updated_at": "2024-01-01T00:00:00+00:00",
        }
        (sessions_dir / "valid.json").write_text(
            json.dumps(valid_session, ensure_ascii=False), encoding="utf-8"
        )

        # 创建损坏的 JSON
        (sessions_dir / "broken.json").write_text("{ invalid json", encoding="utf-8")

        with patch("scripts.chat_store.CHAT_SESSIONS_DIR", return_value=sessions_dir):
            sessions = list_sessions()

            # 只返回有效的会话
            assert len(sessions) == 1
            assert sessions[0]["id"] == "valid"

    def test_list_sessions_skip_missing_id_field(self, tmp_path):
        """测试跳过缺少 id 字段的会话文件。"""
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()

        # 创建缺少 id 的会话
        invalid_session = {
            "title": "无效会话",
            "updated_at": "2024-01-01T00:00:00+00:00",
        }
        (sessions_dir / "invalid.json").write_text(
            json.dumps(invalid_session, ensure_ascii=False), encoding="utf-8"
        )

        with patch("scripts.chat_store.CHAT_SESSIONS_DIR", return_value=sessions_dir):
            sessions = list_sessions()

            assert sessions == []

    def test_list_sessions_with_workspace_id(self, tmp_path):
        """测试 workspace 隔离：不同 workspace 的会话独立。"""
        workspace1_dir = tmp_path / "workspace1"
        workspace1_dir.mkdir()

        session_data = {
            "id": "ws1_session",
            "title": "Workspace 1 会话",
            "updated_at": "2024-01-01T00:00:00+00:00",
        }
        (workspace1_dir / "ws1_session.json").write_text(
            json.dumps(session_data, ensure_ascii=False), encoding="utf-8"
        )

        with patch("scripts.chat_store.CHAT_SESSIONS_DIR", return_value=workspace1_dir):
            sessions = list_sessions(workspace_id="workspace1")

            assert len(sessions) == 1
            assert sessions[0]["id"] == "ws1_session"


class TestLoadSession:
    """测试会话加载功能。"""

    def test_load_session_existing(self, tmp_path):
        """测试加载已存在的会话。"""
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()

        session_data = {
            "id": "abc123",
            "title": "测试会话",
            "created_at": "2024-01-01T00:00:00+00:00",
            "updated_at": "2024-01-02T12:00:00+00:00",
            "messages": [
                {"role": "user", "content": "你好"},
                {"role": "assistant", "content": "你好！有什么可以帮助你的？"},
            ],
        }
        (sessions_dir / "abc123.json").write_text(
            json.dumps(session_data, ensure_ascii=False), encoding="utf-8"
        )

        with patch("scripts.chat_store.CHAT_SESSIONS_DIR", return_value=sessions_dir):
            session = load_session("abc123")

            assert session["id"] == "abc123"
            assert session["title"] == "测试会话"
            assert len(session["messages"]) == 2
            assert session["messages"][0]["content"] == "你好"

    def test_load_session_nonexistent(self, tmp_path):
        """测试加载不存在的会话返回默认结构。"""
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()

        with patch("scripts.chat_store.CHAT_SESSIONS_DIR", return_value=sessions_dir):
            session = load_session("nonexistent")

            assert session["id"] == "nonexistent"
            assert session["title"] == "新对话"
            assert session["messages"] == []

    def test_load_session_with_workspace_id(self, tmp_path):
        """测试从指定 workspace 加载会话。"""
        workspace_dir = tmp_path / "workspace1"
        workspace_dir.mkdir()

        session_data = {
            "id": "ws_session",
            "title": "工作空间会话",
            "messages": [],
        }
        (workspace_dir / "ws_session.json").write_text(
            json.dumps(session_data, ensure_ascii=False), encoding="utf-8"
        )

        with patch("scripts.chat_store.CHAT_SESSIONS_DIR", return_value=workspace_dir):
            session = load_session("ws_session", workspace_id="workspace1")

            assert session["title"] == "工作空间会话"

    def test_load_session_unicode_content(self, tmp_path):
        """测试加载包含 Unicode 字符的会话（中文/emoji）。"""
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()

        session_data = {
            "id": "unicode",
            "title": "中文标题 😊",
            "messages": [{"role": "user", "content": "测试 Unicode: 你好 🌟"}],
        }
        (sessions_dir / "unicode.json").write_text(
            json.dumps(session_data, ensure_ascii=False), encoding="utf-8"
        )

        with patch("scripts.chat_store.CHAT_SESSIONS_DIR", return_value=sessions_dir):
            session = load_session("unicode")

            assert session["title"] == "中文标题 😊"
            assert "你好 🌟" in session["messages"][0]["content"]


class TestSaveSession:
    """测试会话保存功能。"""

    def test_save_session_new(self, tmp_path):
        """测试保存新会话。"""
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()

        messages = [
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "你好！"},
        ]

        with patch("scripts.chat_store.CHAT_SESSIONS_DIR", return_value=sessions_dir):
            save_session("new123", "新会话", messages)

            # 验证文件存在
            session_file = sessions_dir / "new123.json"
            assert session_file.exists()

            # 验证内容
            saved_data = json.loads(session_file.read_text(encoding="utf-8"))
            assert saved_data["id"] == "new123"
            assert saved_data["title"] == "新会话"
            assert len(saved_data["messages"]) == 2
            assert "created_at" in saved_data
            assert "updated_at" in saved_data

    def test_save_session_overwrite_existing(self, tmp_path):
        """测试覆盖已存在的会话。"""
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()

        # 创建旧会话
        old_data = {
            "id": "abc123",
            "title": "旧标题",
            "created_at": "2024-01-01T00:00:00+00:00",
            "updated_at": "2024-01-01T00:00:00+00:00",
            "messages": [{"role": "user", "content": "旧消息"}],
        }
        (sessions_dir / "abc123.json").write_text(
            json.dumps(old_data, ensure_ascii=False), encoding="utf-8"
        )

        # 保存新内容
        new_messages = [
            {"role": "user", "content": "新消息 1"},
            {"role": "assistant", "content": "新回复"},
        ]

        with patch("scripts.chat_store.CHAT_SESSIONS_DIR", return_value=sessions_dir):
            save_session("abc123", "新标题", new_messages)

            # 验证覆盖成功
            saved_data = json.loads((sessions_dir / "abc123.json").read_text(encoding="utf-8"))
            assert saved_data["title"] == "新标题"
            assert len(saved_data["messages"]) == 2
            assert saved_data["messages"][0]["content"] == "新消息 1"
            # updated_at 应该更新
            assert saved_data["updated_at"] > old_data["updated_at"]

    def test_save_session_with_created_at(self, tmp_path):
        """测试保存时提供 created_at 参数。"""
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()

        custom_created_at = "2023-12-01T10:00:00+00:00"

        with patch("scripts.chat_store.CHAT_SESSIONS_DIR", return_value=sessions_dir):
            save_session(
                "custom",
                "自定义创建时间",
                [],
                created_at=custom_created_at,
            )

            saved_data = json.loads((sessions_dir / "custom.json").read_text(encoding="utf-8"))
            assert saved_data["created_at"] == custom_created_at

    def test_save_session_without_created_at(self, tmp_path):
        """测试保存时不提供 created_at 使用当前时间。"""
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()

        with patch("scripts.chat_store.CHAT_SESSIONS_DIR", return_value=sessions_dir):
            save_session("auto", "自动时间", [])

            saved_data = json.loads((sessions_dir / "auto.json").read_text(encoding="utf-8"))
            # 验证是 ISO 格式的时间字符串
            datetime.fromisoformat(saved_data["created_at"])
            datetime.fromisoformat(saved_data["updated_at"])

    def test_save_session_creates_directory_if_not_exists(self, tmp_path):
        """测试目录不存在时自动创建。"""
        sessions_dir = tmp_path / "new_sessions"
        # 不预先创建目录

        with patch("scripts.chat_store.CHAT_SESSIONS_DIR", return_value=sessions_dir):
            save_session("auto_create", "自动创建目录", [])

            # 验证目录和文件都被创建
            assert sessions_dir.exists()
            assert (sessions_dir / "auto_create.json").exists()

    def test_save_session_with_workspace_id(self, tmp_path):
        """测试保存到指定 workspace。"""
        workspace_dir = tmp_path / "workspace2"
        workspace_dir.mkdir()

        with patch("scripts.chat_store.CHAT_SESSIONS_DIR", return_value=workspace_dir):
            save_session("ws2_session", "工作空间 2", [], workspace_id="workspace2")

            assert (workspace_dir / "ws2_session.json").exists()

    def test_save_session_unicode_content(self, tmp_path):
        """测试保存包含 Unicode 字符的会话。"""
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()

        messages = [
            {"role": "user", "content": "中文消息 🎉"},
            {"role": "assistant", "content": "中文回复 ✨"},
        ]

        with patch("scripts.chat_store.CHAT_SESSIONS_DIR", return_value=sessions_dir):
            save_session("unicode", "中文标题 😊", messages)

            # 验证 ensure_ascii=False 生效（可读的中文，不是 \uXXXX）
            raw_content = (sessions_dir / "unicode.json").read_text(encoding="utf-8")
            assert "中文消息 🎉" in raw_content
            assert "\\u" not in raw_content  # 不是转义的 Unicode

    def test_save_session_json_structure(self, tmp_path):
        """测试保存的 JSON 结构完整性。"""
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()

        with patch("scripts.chat_store.CHAT_SESSIONS_DIR", return_value=sessions_dir):
            save_session("structure", "结构测试", [{"role": "user", "content": "test"}])

            saved_data = json.loads((sessions_dir / "structure.json").read_text(encoding="utf-8"))

            # 验证所有必需字段
            assert "id" in saved_data
            assert "title" in saved_data
            assert "created_at" in saved_data
            assert "updated_at" in saved_data
            assert "messages" in saved_data
            assert isinstance(saved_data["messages"], list)


class TestDeleteSession:
    """测试会话删除功能。"""

    def test_delete_session_existing(self, tmp_path):
        """测试删除已存在的会话。"""
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()

        # 创建会话
        session_data = {"id": "to_delete", "title": "待删除", "messages": []}
        (sessions_dir / "to_delete.json").write_text(
            json.dumps(session_data, ensure_ascii=False), encoding="utf-8"
        )

        with patch("scripts.chat_store.CHAT_SESSIONS_DIR", return_value=sessions_dir):
            # 验证文件存在
            assert (sessions_dir / "to_delete.json").exists()

            # 删除
            delete_session("to_delete")

            # 验证文件不存在
            assert not (sessions_dir / "to_delete.json").exists()

    def test_delete_session_nonexistent(self, tmp_path):
        """测试删除不存在的会话不抛异常。"""
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()

        with patch("scripts.chat_store.CHAT_SESSIONS_DIR", return_value=sessions_dir):
            # 不应该抛异常
            delete_session("nonexistent")

    def test_delete_session_with_workspace_id(self, tmp_path):
        """测试从指定 workspace 删除会话。"""
        workspace_dir = tmp_path / "workspace3"
        workspace_dir.mkdir()

        # 创建会话
        (workspace_dir / "ws3_session.json").write_text(
            json.dumps({"id": "ws3_session"}), encoding="utf-8"
        )

        with patch("scripts.chat_store.CHAT_SESSIONS_DIR", return_value=workspace_dir):
            delete_session("ws3_session", workspace_id="workspace3")

            assert not (workspace_dir / "ws3_session.json").exists()


class TestMakeTitle:
    """测试标题生成功能。"""

    def test_make_title_short_message(self):
        """测试短消息（不需要截断）。"""
        message = "这是一个短消息"
        title = make_title(message)

        assert title == "这是一个短消息"
        assert "…" not in title

    def test_make_title_long_message_truncated(self):
        """测试长消息被截断到 24 字符。"""
        message = "这是一个非常非常长的消息，超过了 24 个字符的限制，应该被截断"
        title = make_title(message)

        assert len(title) == 25  # 24 字符 + "…"
        assert title.endswith("…")
        assert title.startswith("这是一个非常非常长的消息，超过了 24 个字符")

    def test_make_title_with_newlines(self):
        """测试包含换行符的消息（换行替换为空格）。"""
        message = "第一行\n第二行\n第三行"
        title = make_title(message)

        assert "\n" not in title
        assert "第一行 第二行 第三行" == title

    def test_make_title_with_leading_trailing_whitespace(self):
        """测试去除首尾空白。"""
        message = "  \n  有空白的消息  \n  "
        title = make_title(message)

        assert title == "有空白的消息"

    def test_make_title_empty_string(self):
        """测试空字符串。"""
        message = ""
        title = make_title(message)

        assert title == ""

    def test_make_title_whitespace_only(self):
        """测试只有空白字符。"""
        message = "   \n  \n  "
        title = make_title(message)

        assert title == ""

    def test_make_title_exactly_24_chars(self):
        """测试恰好 24 字符（不添加省略号）。"""
        message = "a" * 24  # 恰好 24 个字符
        title = make_title(message)

        assert len(title) == 24
        assert "…" not in title

    def test_make_title_25_chars(self):
        """测试 25 字符（截断 + 省略号）。"""
        message = "a" * 25  # 25 个字符
        title = make_title(message)

        assert len(title) == 25  # 24 + "…"
        assert title.endswith("…")

    def test_make_title_unicode_emoji(self):
        """测试包含 emoji 的消息。"""
        message = "你好 😊 这是一条消息"
        title = make_title(message)

        assert "你好 😊 这是一条消息" == title

    def test_make_title_mixed_newlines_and_spaces(self):
        """测试混合换行和多个空格。"""
        message = "第一段\n\n第二段    多个空格\n第三段"
        title = make_title(message)

        assert "\n" not in title
        # 多个空格被保留（replace 只替换换行）
        assert "第一段  第二段    多个空格 第三段" == title


class TestIntegration:
    """集成测试：测试完整工作流。"""

    def test_full_workflow_create_list_load_update_delete(self, tmp_path):
        """测试完整生命周期：创建 → 列表 → 加载 → 更新 → 删除。"""
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()

        with patch("scripts.chat_store.CHAT_SESSIONS_DIR", return_value=sessions_dir):
            # 1. 创建新会话
            session_id = new_session_id()
            messages = [{"role": "user", "content": "你好"}]
            save_session(session_id, "测试会话", messages)

            # 2. 列表中应该能看到
            sessions = list_sessions()
            assert len(sessions) == 1
            assert sessions[0]["id"] == session_id

            # 3. 加载会话
            loaded = load_session(session_id)
            assert loaded["id"] == session_id
            assert len(loaded["messages"]) == 1

            # 4. 更新会话（添加新消息）
            messages.append({"role": "assistant", "content": "你好！"})
            save_session(session_id, "测试会话", messages)

            # 5. 再次加载验证更新
            loaded_updated = load_session(session_id)
            assert len(loaded_updated["messages"]) == 2

            # 6. 删除会话
            delete_session(session_id)

            # 7. 列表应该为空
            sessions_after_delete = list_sessions()
            assert len(sessions_after_delete) == 0

    def test_multiple_workspaces_isolation(self, tmp_path):
        """测试多个 workspace 之间的隔离。"""
        ws1_dir = tmp_path / "workspace1"
        ws2_dir = tmp_path / "workspace2"
        ws1_dir.mkdir()
        ws2_dir.mkdir()

        # Workspace 1: 创建会话
        with patch("scripts.chat_store.CHAT_SESSIONS_DIR", return_value=ws1_dir):
            save_session("ws1_id", "WS1 会话", [], workspace_id="ws1")

        # Workspace 2: 创建会话
        with patch("scripts.chat_store.CHAT_SESSIONS_DIR", return_value=ws2_dir):
            save_session("ws2_id", "WS2 会话", [], workspace_id="ws2")

        # 验证隔离：WS1 只看到自己的会话
        with patch("scripts.chat_store.CHAT_SESSIONS_DIR", return_value=ws1_dir):
            ws1_sessions = list_sessions(workspace_id="ws1")
            assert len(ws1_sessions) == 1
            assert ws1_sessions[0]["id"] == "ws1_id"

        # 验证隔离：WS2 只看到自己的会话
        with patch("scripts.chat_store.CHAT_SESSIONS_DIR", return_value=ws2_dir):
            ws2_sessions = list_sessions(workspace_id="ws2")
            assert len(ws2_sessions) == 1
            assert ws2_sessions[0]["id"] == "ws2_id"
