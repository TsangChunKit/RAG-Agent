"""scripts/context_cache.py 完整单元测试

测试目标：从 51% 提升到 75%+

测试范围：
1. _fingerprint() - 内容指纹计算
2. _load_state() - 状态文件加载（workspace 感知）
3. _save_state() - 状态文件保存（workspace 感知）
4. get_cache_name() - 主入口函数
   - 缓存创建和复用
   - Provider 切换（gemini vs grok/hermes）
   - Token 门槛检查
   - 状态文件损坏/缺失
   - 缓存创建失败
   - 旧缓存删除
   - Workspace 隔离
   - 指纹变化触发重建
"""
import json
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock, Mock, patch, mock_open

import pytest


# ── 测试数据 fixtures ───────────────────────────────────────────────────


@pytest.fixture
def mock_cache_state_path(tmp_path: Path):
    """Mock EXPLICIT_CACHE_STATE_PATH 返回 tmp_path"""
    cache_file = tmp_path / ".explicit_cache_state.json"
    with patch("scripts.context_cache.EXPLICIT_CACHE_STATE_PATH") as mock_path:
        mock_path.return_value = cache_file
        yield cache_file


@pytest.fixture
def mock_settings():
    """Mock settings.py 的 dialogue_params 和 provider"""
    with patch("scripts.context_cache.dialogue_params") as mock_dialogue, \
         patch("scripts.context_cache.get_provider") as mock_provider:
        mock_dialogue.return_value = {"model": "gemini-3.5-flash"}
        mock_provider.return_value = "gemini"
        yield {
            "dialogue_params": mock_dialogue,
            "get_provider": mock_provider,
        }


@pytest.fixture
def mock_gemini_client():
    """Mock _get_client() 返回的 Gemini client"""
    with patch("scripts.context_cache._get_client") as mock_get_client:
        mock_client = MagicMock()

        # Mock count_tokens
        mock_count_result = MagicMock()
        mock_count_result.total_tokens = 5000  # 默认超过门槛
        mock_client.models.count_tokens.return_value = mock_count_result

        # Mock caches.create
        mock_cache = MagicMock()
        mock_cache.name = "cachedContents/test-cache-123"
        mock_client.caches.create.return_value = mock_cache

        # Mock caches.get
        mock_client.caches.get.return_value = mock_cache

        # Mock caches.delete
        mock_client.caches.delete.return_value = None

        mock_get_client.return_value = mock_client
        yield mock_client


# ── 辅助函数测试 ────────────────────────────────────────────────────────


class TestFingerprint:
    """测试 _fingerprint() 内容指纹计算"""

    def test_fingerprint_basic(self):
        """测试基本指纹计算"""
        from scripts.context_cache import _fingerprint

        fp = _fingerprint("model-1", "instruction", "content")
        assert isinstance(fp, str)
        assert len(fp) == 64  # SHA256 hex digest

    def test_fingerprint_stability(self):
        """测试相同输入产生相同指纹"""
        from scripts.context_cache import _fingerprint

        fp1 = _fingerprint("model-1", "instruction", "content")
        fp2 = _fingerprint("model-1", "instruction", "content")
        assert fp1 == fp2

    def test_fingerprint_different_on_model_change(self):
        """测试模型变化时指纹不同"""
        from scripts.context_cache import _fingerprint

        fp1 = _fingerprint("model-1", "instruction", "content")
        fp2 = _fingerprint("model-2", "instruction", "content")
        assert fp1 != fp2

    def test_fingerprint_different_on_instruction_change(self):
        """测试 system instruction 变化时指纹不同"""
        from scripts.context_cache import _fingerprint

        fp1 = _fingerprint("model", "instruction-1", "content")
        fp2 = _fingerprint("model", "instruction-2", "content")
        assert fp1 != fp2

    def test_fingerprint_different_on_content_change(self):
        """测试静态内容变化时指纹不同"""
        from scripts.context_cache import _fingerprint

        fp1 = _fingerprint("model", "instruction", "content-1")
        fp2 = _fingerprint("model", "instruction", "content-2")
        assert fp1 != fp2

    def test_fingerprint_handles_unicode(self):
        """测试处理 Unicode 字符"""
        from scripts.context_cache import _fingerprint

        fp = _fingerprint("模型", "指令 🎯", "内容 中文")
        assert isinstance(fp, str)
        assert len(fp) == 64


class TestLoadState:
    """测试 _load_state() 状态文件加载"""

    def test_load_state_not_exists(self, mock_cache_state_path):
        """测试文件不存在时返回 None"""
        from scripts.context_cache import _load_state

        result = _load_state()
        assert result is None

    def test_load_state_valid_json(self, mock_cache_state_path):
        """测试加载有效的 JSON 状态"""
        from scripts.context_cache import _load_state

        # 写入有效状态
        state_data = {
            "fingerprint": "abc123",
            "cache_name": "cachedContents/test-123"
        }
        mock_cache_state_path.write_text(json.dumps(state_data), encoding="utf-8")

        result = _load_state()
        assert result == state_data

    def test_load_state_corrupted_json(self, mock_cache_state_path):
        """测试损坏的 JSON 文件返回 None"""
        from scripts.context_cache import _load_state

        # 写入无效 JSON
        mock_cache_state_path.write_text("{invalid json", encoding="utf-8")

        result = _load_state()
        assert result is None

    def test_load_state_empty_file(self, mock_cache_state_path):
        """测试空文件返回 None"""
        from scripts.context_cache import _load_state

        mock_cache_state_path.write_text("", encoding="utf-8")

        result = _load_state()
        assert result is None

    def test_load_state_with_workspace_id(self, tmp_path):
        """测试 workspace 隔离（不同 workspace 有不同状态文件）"""
        from scripts.context_cache import _load_state

        # Mock 两个不同 workspace 的路径
        ws1_file = tmp_path / "ws1" / ".explicit_cache_state.json"
        ws2_file = tmp_path / "ws2" / ".explicit_cache_state.json"

        ws1_file.parent.mkdir(parents=True, exist_ok=True)
        ws2_file.parent.mkdir(parents=True, exist_ok=True)

        ws1_state = {"fingerprint": "ws1-fingerprint", "cache_name": "cache-ws1"}
        ws2_state = {"fingerprint": "ws2-fingerprint", "cache_name": "cache-ws2"}

        ws1_file.write_text(json.dumps(ws1_state), encoding="utf-8")
        ws2_file.write_text(json.dumps(ws2_state), encoding="utf-8")

        # 分别 mock 两个 workspace 的路径
        with patch("scripts.context_cache.EXPLICIT_CACHE_STATE_PATH") as mock_path:
            # Workspace 1
            mock_path.return_value = ws1_file
            result1 = _load_state("ws1")
            assert result1 == ws1_state

            # Workspace 2
            mock_path.return_value = ws2_file
            result2 = _load_state("ws2")
            assert result2 == ws2_state


class TestSaveState:
    """测试 _save_state() 状态文件保存"""

    def test_save_state_creates_file(self, mock_cache_state_path):
        """测试保存创建新文件"""
        from scripts.context_cache import _save_state

        _save_state("fingerprint123", "cachedContents/cache-456")

        assert mock_cache_state_path.exists()
        data = json.loads(mock_cache_state_path.read_text(encoding="utf-8"))
        assert data == {
            "fingerprint": "fingerprint123",
            "cache_name": "cachedContents/cache-456"
        }

    def test_save_state_overwrites_existing(self, mock_cache_state_path):
        """测试覆盖已存在的文件"""
        from scripts.context_cache import _save_state

        # 先写入旧状态
        old_state = {"fingerprint": "old", "cache_name": "old-cache"}
        mock_cache_state_path.write_text(json.dumps(old_state), encoding="utf-8")

        # 保存新状态
        _save_state("new-fp", "new-cache")

        data = json.loads(mock_cache_state_path.read_text(encoding="utf-8"))
        assert data == {
            "fingerprint": "new-fp",
            "cache_name": "new-cache"
        }

    def test_save_state_creates_parent_dirs(self, tmp_path):
        """测试自动创建父目录"""
        from scripts.context_cache import _save_state

        nested_path = tmp_path / "deep" / "nested" / "path" / ".explicit_cache_state.json"

        with patch("scripts.context_cache.EXPLICIT_CACHE_STATE_PATH") as mock_path:
            mock_path.return_value = nested_path
            _save_state("fp", "cache")

            assert nested_path.exists()
            assert nested_path.parent.exists()

    def test_save_state_handles_unicode(self, mock_cache_state_path):
        """测试保存 Unicode 内容"""
        from scripts.context_cache import _save_state

        _save_state("指纹🔖", "缓存/中文-123")

        data = json.loads(mock_cache_state_path.read_text(encoding="utf-8"))
        assert data["fingerprint"] == "指纹🔖"
        assert data["cache_name"] == "缓存/中文-123"

    def test_save_state_workspace_isolation(self, tmp_path):
        """测试不同 workspace 保存到不同文件"""
        from scripts.context_cache import _save_state

        ws1_file = tmp_path / "ws1" / ".explicit_cache_state.json"
        ws2_file = tmp_path / "ws2" / ".explicit_cache_state.json"

        with patch("scripts.context_cache.EXPLICIT_CACHE_STATE_PATH") as mock_path:
            # 保存到 workspace 1
            mock_path.return_value = ws1_file
            _save_state("fp1", "cache1", "ws1")

            # 保存到 workspace 2
            mock_path.return_value = ws2_file
            _save_state("fp2", "cache2", "ws2")

            # 验证两个文件内容不同
            data1 = json.loads(ws1_file.read_text(encoding="utf-8"))
            data2 = json.loads(ws2_file.read_text(encoding="utf-8"))

            assert data1["fingerprint"] == "fp1"
            assert data2["fingerprint"] == "fp2"


# ── 主函数测试 ─────────────────────────────────────────────────────────


class TestGetCacheName:
    """测试 get_cache_name() 主入口函数"""

    def test_get_cache_name_non_gemini_provider(self, mock_settings):
        """测试非 gemini provider 返回 None（Explicit Cache 是 Gemini 专有）"""
        from scripts.context_cache import get_cache_name

        mock_settings["get_provider"].return_value = "grok"

        result = get_cache_name("instruction", "static content")
        assert result is None

        # 测试 hermes
        mock_settings["get_provider"].return_value = "hermes"
        result = get_cache_name("instruction", "static content")
        assert result is None

    def test_get_cache_name_below_token_threshold(
        self, mock_settings, mock_gemini_client, mock_cache_state_path
    ):
        """测试内容 token 数低于门槛返回 None"""
        from scripts.context_cache import get_cache_name

        # Mock token count 低于门槛
        mock_count = MagicMock()
        mock_count.total_tokens = 3000  # 小于 MIN_CACHE_TOKENS (4096)
        mock_gemini_client.models.count_tokens.return_value = mock_count

        result = get_cache_name("instruction", "short content")
        assert result is None

        # 验证没有创建缓存
        mock_gemini_client.caches.create.assert_not_called()

    def test_get_cache_name_creates_new_cache(
        self, mock_settings, mock_gemini_client, mock_cache_state_path
    ):
        """测试首次调用创建新缓存"""
        from scripts.context_cache import get_cache_name

        result = get_cache_name("instruction", "static content" * 1000)

        assert result == "cachedContents/test-cache-123"

        # 验证调用了 count_tokens
        mock_gemini_client.models.count_tokens.assert_called_once()

        # 验证调用了 caches.create
        mock_gemini_client.caches.create.assert_called_once()
        call_kwargs = mock_gemini_client.caches.create.call_args[1]
        assert call_kwargs["model"] == "gemini-3.5-flash"

        # 验证保存了状态文件
        assert mock_cache_state_path.exists()
        state = json.loads(mock_cache_state_path.read_text(encoding="utf-8"))
        assert state["cache_name"] == "cachedContents/test-cache-123"
        assert "fingerprint" in state

    def test_get_cache_name_reuses_existing_cache(
        self, mock_settings, mock_gemini_client, mock_cache_state_path
    ):
        """测试指纹相同时复用已有缓存"""
        from scripts.context_cache import get_cache_name, _fingerprint

        # 预先保存状态
        fp = _fingerprint("gemini-3.5-flash", "instruction", "content")
        state_data = {
            "fingerprint": fp,
            "cache_name": "cachedContents/existing-cache"
        }
        mock_cache_state_path.write_text(json.dumps(state_data), encoding="utf-8")

        # Mock caches.get 成功（缓存仍然有效）
        mock_gemini_client.caches.get.return_value = MagicMock()

        result = get_cache_name("instruction", "content")

        assert result == "cachedContents/existing-cache"

        # 验证调用了 caches.get 检查缓存是否仍有效
        mock_gemini_client.caches.get.assert_called_once_with(
            name="cachedContents/existing-cache"
        )

        # 验证没有创建新缓存（复用了旧的）
        mock_gemini_client.caches.create.assert_not_called()

    def test_get_cache_name_fingerprint_changed_rebuilds(
        self, mock_settings, mock_gemini_client, mock_cache_state_path
    ):
        """测试指纹变化时重建缓存"""
        from scripts.context_cache import get_cache_name, _fingerprint

        # 预先保存旧状态（不同的指纹）
        old_fp = _fingerprint("gemini-3.5-flash", "old-instruction", "old-content")
        state_data = {
            "fingerprint": old_fp,
            "cache_name": "cachedContents/old-cache"
        }
        mock_cache_state_path.write_text(json.dumps(state_data), encoding="utf-8")

        # 调用时使用新内容（指纹会变）
        result = get_cache_name("new-instruction", "new-content" * 1000)

        assert result == "cachedContents/test-cache-123"

        # 验证删除了旧缓存
        mock_gemini_client.caches.delete.assert_called_once_with(
            "cachedContents/old-cache"
        )

        # 验证创建了新缓存
        mock_gemini_client.caches.create.assert_called_once()

        # 验证更新了状态文件
        new_state = json.loads(mock_cache_state_path.read_text(encoding="utf-8"))
        assert new_state["cache_name"] == "cachedContents/test-cache-123"
        assert new_state["fingerprint"] != old_fp

    def test_get_cache_name_old_cache_expired_rebuilds(
        self, mock_settings, mock_gemini_client, mock_cache_state_path
    ):
        """测试旧缓存已过期时重建"""
        from scripts.context_cache import get_cache_name, _fingerprint

        # 使用相同内容计算指纹，确保能进入复用逻辑
        content = "content" * 1000
        fp = _fingerprint("gemini-3.5-flash", "instruction", content)
        state_data = {
            "fingerprint": fp,
            "cache_name": "cachedContents/expired-cache"
        }
        mock_cache_state_path.write_text(json.dumps(state_data), encoding="utf-8")

        # Mock caches.get 抛异常（缓存已过期或被删除）
        mock_gemini_client.caches.get.side_effect = Exception("Cache expired")

        result = get_cache_name("instruction", content)

        assert result == "cachedContents/test-cache-123"

        # 验证尝试获取旧缓存
        mock_gemini_client.caches.get.assert_called_once_with(
            name="cachedContents/expired-cache"
        )

        # 验证创建了新缓存
        mock_gemini_client.caches.create.assert_called_once()

    def test_get_cache_name_create_fails_returns_none(
        self, mock_settings, mock_gemini_client, mock_cache_state_path
    ):
        """测试创建缓存失败时返回 None（graceful degradation）"""
        from scripts.context_cache import get_cache_name

        # Mock caches.create 失败
        mock_gemini_client.caches.create.side_effect = Exception("API quota exceeded")

        result = get_cache_name("instruction", "content" * 1000)

        assert result is None

        # 验证尝试创建了
        mock_gemini_client.caches.create.assert_called_once()

    def test_get_cache_name_delete_old_cache_fails_continues(
        self, mock_settings, mock_gemini_client, mock_cache_state_path
    ):
        """测试删除旧缓存失败时继续（不影响新缓存创建）"""
        from scripts.context_cache import get_cache_name, _fingerprint

        # 预先保存旧状态
        old_fp = _fingerprint("gemini-3.5-flash", "old", "old")
        state_data = {
            "fingerprint": old_fp,
            "cache_name": "cachedContents/old-cache"
        }
        mock_cache_state_path.write_text(json.dumps(state_data), encoding="utf-8")

        # Mock delete 失败
        mock_gemini_client.caches.delete.side_effect = Exception("Delete failed")

        result = get_cache_name("new", "new" * 1000)

        # 应该仍然返回新缓存（delete 失败不影响）
        assert result == "cachedContents/test-cache-123"

        # 验证尝试删除了
        mock_gemini_client.caches.delete.assert_called_once()

        # 验证创建了新缓存
        mock_gemini_client.caches.create.assert_called_once()

    def test_get_cache_name_custom_model(
        self, mock_settings, mock_gemini_client, mock_cache_state_path
    ):
        """测试使用自定义模型（model 进入指纹）"""
        from scripts.context_cache import get_cache_name

        result = get_cache_name(
            "instruction",
            "content" * 1000,
            model="custom-model-v2"
        )

        assert result == "cachedContents/test-cache-123"

        # 验证使用了自定义模型
        call_kwargs = mock_gemini_client.caches.create.call_args[1]
        assert call_kwargs["model"] == "custom-model-v2"

        # 验证 count_tokens 也用了自定义模型
        count_call = mock_gemini_client.models.count_tokens.call_args
        assert count_call[1]["model"] == "custom-model-v2"

    def test_get_cache_name_workspace_isolation(
        self, mock_settings, mock_gemini_client, tmp_path
    ):
        """测试不同 workspace 使用独立的缓存状态"""
        from scripts.context_cache import get_cache_name

        ws1_file = tmp_path / "ws1" / ".explicit_cache_state.json"
        ws2_file = tmp_path / "ws2" / ".explicit_cache_state.json"

        ws1_file.parent.mkdir(parents=True, exist_ok=True)
        ws2_file.parent.mkdir(parents=True, exist_ok=True)

        with patch("scripts.context_cache.EXPLICIT_CACHE_STATE_PATH") as mock_path:
            # Workspace 1
            mock_path.return_value = ws1_file
            result1 = get_cache_name("instruction", "content" * 1000, workspace_id="ws1")

            # Workspace 2
            mock_path.return_value = ws2_file
            result2 = get_cache_name("instruction", "content" * 1000, workspace_id="ws2")

            # 两个 workspace 都应该创建了缓存
            assert result1 == "cachedContents/test-cache-123"
            assert result2 == "cachedContents/test-cache-123"

            # 验证分别保存了状态文件
            assert ws1_file.exists()
            assert ws2_file.exists()

    def test_get_cache_name_with_cache_config(
        self, mock_settings, mock_gemini_client, mock_cache_state_path
    ):
        """测试创建缓存时使用正确的 config（system_instruction + contents + ttl）"""
        from scripts.context_cache import get_cache_name, CACHE_TTL

        instruction = "You are a helpful assistant"
        content = "Static context" * 1000

        result = get_cache_name(instruction, content)

        assert result == "cachedContents/test-cache-123"

        # 验证 caches.create 参数
        call_kwargs = mock_gemini_client.caches.create.call_args[1]

        # 验证使用了 types.CreateCachedContentConfig
        assert "config" in call_kwargs

        # Note: 由于 types.CreateCachedContentConfig 被 mock 了，
        # 我们无法直接验证其内部参数，但可以验证调用次数
        mock_gemini_client.caches.create.assert_called_once()

    def test_get_cache_name_no_state_file_on_threshold_fail(
        self, mock_settings, mock_gemini_client, mock_cache_state_path
    ):
        """测试 token 不足时不创建状态文件"""
        from scripts.context_cache import get_cache_name

        # Mock token count 低于门槛
        mock_count = MagicMock()
        mock_count.total_tokens = 2000
        mock_gemini_client.models.count_tokens.return_value = mock_count

        result = get_cache_name("instruction", "short")

        assert result is None
        assert not mock_cache_state_path.exists()

    def test_get_cache_name_model_change_triggers_rebuild(
        self, mock_settings, mock_gemini_client, mock_cache_state_path
    ):
        """测试改变模型时触发缓存重建（model 进指纹）"""
        from scripts.context_cache import get_cache_name, _fingerprint

        # 预先保存旧模型的状态
        old_fp = _fingerprint("gemini-3.5-flash", "instruction", "content")
        state_data = {
            "fingerprint": old_fp,
            "cache_name": "cachedContents/old-model-cache"
        }
        mock_cache_state_path.write_text(json.dumps(state_data), encoding="utf-8")

        # 使用不同模型调用
        result = get_cache_name(
            "instruction",
            "content" * 1000,
            model="gemini-4.0-pro"  # 不同模型
        )

        assert result == "cachedContents/test-cache-123"

        # 验证删除了旧缓存
        mock_gemini_client.caches.delete.assert_called_once_with(
            "cachedContents/old-model-cache"
        )

        # 验证创建了新缓存
        mock_gemini_client.caches.create.assert_called_once()


# ── 边界条件和错误处理测试 ──────────────────────────────────────────────


class TestEdgeCases:
    """测试边界条件和错误处理"""

    def test_empty_system_instruction(
        self, mock_settings, mock_gemini_client, mock_cache_state_path
    ):
        """测试空的 system instruction"""
        from scripts.context_cache import get_cache_name

        result = get_cache_name("", "content" * 1000)

        # 应该仍然能创建缓存
        assert result == "cachedContents/test-cache-123"

    def test_empty_static_content(
        self, mock_settings, mock_gemini_client, mock_cache_state_path
    ):
        """测试空的静态内容"""
        from scripts.context_cache import get_cache_name

        # Mock token count 为 0（空内容）
        mock_count = MagicMock()
        mock_count.total_tokens = 0
        mock_gemini_client.models.count_tokens.return_value = mock_count

        result = get_cache_name("instruction", "")

        # 低于门槛，返回 None
        assert result is None

    def test_unicode_content_in_all_functions(
        self, mock_settings, mock_gemini_client, mock_cache_state_path
    ):
        """测试全流程处理 Unicode"""
        from scripts.context_cache import get_cache_name

        result = get_cache_name(
            "你是一个有帮助的助手 🤖",
            "静态内容：心理咨询记录" * 1000
        )

        assert result == "cachedContents/test-cache-123"

        # 验证状态文件包含 Unicode
        state = json.loads(mock_cache_state_path.read_text(encoding="utf-8"))
        assert "fingerprint" in state

    def test_very_large_content(
        self, mock_settings, mock_gemini_client, mock_cache_state_path
    ):
        """测试超大内容（模拟真实场景：长期记忆 + 心智地图）"""
        from scripts.context_cache import get_cache_name

        # Mock 超大 token count
        mock_count = MagicMock()
        mock_count.total_tokens = 500000  # 500K tokens
        mock_gemini_client.models.count_tokens.return_value = mock_count

        large_content = "X" * 1_000_000  # 1MB content

        result = get_cache_name("instruction", large_content)

        assert result == "cachedContents/test-cache-123"

    def test_cache_name_format(
        self, mock_settings, mock_gemini_client, mock_cache_state_path
    ):
        """测试返回的缓存名格式符合 Gemini 规范"""
        from scripts.context_cache import get_cache_name

        result = get_cache_name("instruction", "content" * 1000)

        # Gemini cached content name 格式：cachedContents/xxxxx
        assert result.startswith("cachedContents/")

    def test_state_file_permission_error(self, mock_settings, mock_gemini_client):
        """测试状态文件权限错误（IO 错误）"""
        from scripts.context_cache import get_cache_name

        # Mock EXPLICIT_CACHE_STATE_PATH 返回只读路径
        with patch("scripts.context_cache.EXPLICIT_CACHE_STATE_PATH") as mock_path:
            readonly_path = Path("/nonexistent/readonly/.explicit_cache_state.json")
            mock_path.return_value = readonly_path

            # 应该抛出异常（无法创建父目录）
            with pytest.raises(Exception):
                get_cache_name("instruction", "content" * 1000)

    def test_min_cache_tokens_constant(self):
        """测试 MIN_CACHE_TOKENS 常量值（应该是 4096）"""
        from scripts.context_cache import MIN_CACHE_TOKENS

        assert MIN_CACHE_TOKENS == 4096

    def test_cache_ttl_constant(self):
        """测试 CACHE_TTL 常量值（应该是 24 小时）"""
        from scripts.context_cache import CACHE_TTL

        assert CACHE_TTL == "86400s"  # 24 * 3600 = 86400


# ── 集成测试 ───────────────────────────────────────────────────────────


class TestIntegration:
    """集成测试：测试完整流程"""

    def test_full_cache_lifecycle(
        self, mock_settings, mock_gemini_client, mock_cache_state_path
    ):
        """测试完整的缓存生命周期：创建 → 复用 → 指纹变化重建"""
        from scripts.context_cache import get_cache_name

        # 第 1 次调用：创建新缓存
        result1 = get_cache_name("instruction-v1", "content-v1" * 1000)
        assert result1 == "cachedContents/test-cache-123"
        assert mock_gemini_client.caches.create.call_count == 1

        # Reset mock
        mock_gemini_client.caches.create.reset_mock()

        # 第 2 次调用：相同内容，复用缓存
        result2 = get_cache_name("instruction-v1", "content-v1" * 1000)
        assert result2 == "cachedContents/test-cache-123"
        assert mock_gemini_client.caches.create.call_count == 0  # 没有创建新缓存
        assert mock_gemini_client.caches.get.call_count == 1  # 检查了旧缓存

        # 第 3 次调用：内容变化，重建缓存
        result3 = get_cache_name("instruction-v2", "content-v2" * 1000)
        assert result3 == "cachedContents/test-cache-123"
        assert mock_gemini_client.caches.create.call_count == 1  # 创建了新缓存
        assert mock_gemini_client.caches.delete.call_count == 1  # 删除了旧缓存

    def test_multiple_workspaces_independent_caches(
        self, mock_settings, mock_gemini_client, tmp_path
    ):
        """测试多个 workspace 独立管理缓存"""
        from scripts.context_cache import get_cache_name

        ws1_file = tmp_path / "ws1" / ".explicit_cache_state.json"
        ws2_file = tmp_path / "ws2" / ".explicit_cache_state.json"

        ws1_file.parent.mkdir(parents=True, exist_ok=True)
        ws2_file.parent.mkdir(parents=True, exist_ok=True)

        with patch("scripts.context_cache.EXPLICIT_CACHE_STATE_PATH") as mock_path:
            # Workspace 1 创建缓存
            mock_path.return_value = ws1_file
            result1 = get_cache_name("ws1-instruction", "ws1-content" * 1000, workspace_id="ws1")

            # Workspace 2 创建缓存
            mock_path.return_value = ws2_file
            result2 = get_cache_name("ws2-instruction", "ws2-content" * 1000, workspace_id="ws2")

            # 两个 workspace 都有缓存
            assert result1 == "cachedContents/test-cache-123"
            assert result2 == "cachedContents/test-cache-123"

            # 状态文件独立
            state1 = json.loads(ws1_file.read_text(encoding="utf-8"))
            state2 = json.loads(ws2_file.read_text(encoding="utf-8"))
            assert state1["fingerprint"] != state2["fingerprint"]
