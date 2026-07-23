"""scripts/llm.py 完整单元测试

测试目标：从 33% (36/108) 提升到 60%+

测试范围：
1. ask_llm() - LLM 调用主入口函数
2. Provider 切换（Gemini/Grok/Hermes）
3. Explicit Cache 逻辑（仅 Gemini）
4. 参数覆盖（model/temperature/thinking_level/max_output_tokens）
5. response_schema（JSON 结构化输出）
6. 错误处理和重试
7. Client 惰性初始化和 key 变更重建
"""
from dataclasses import dataclass
from unittest.mock import MagicMock, patch, Mock

import pytest


# ── 测试数据 fixtures ───────────────────────────────────────────────────


@pytest.fixture
def mock_gemini_settings():
    """Mock settings.py 返回的各种配置值"""
    with patch("scripts.llm.dialogue_params") as mock_dialogue, \
         patch("scripts.llm.summary_params") as mock_summary, \
         patch("scripts.llm.get_api_key") as mock_key, \
         patch("scripts.llm.get_xai_key") as mock_xai_key, \
         patch("scripts.llm.get_hermes_key") as mock_hermes_key, \
         patch("scripts.llm.hermes_base_url") as mock_hermes_url, \
         patch("scripts.llm.get_provider") as mock_provider:

        mock_dialogue.return_value = {
            "model": "gemini-3.5-flash",
            "thinking_level": "low",
            "temperature": 0.7,
            "max_output_tokens": 8192,
        }
        mock_summary.return_value = {
            "model": "gemini-3.5-flash",
            "thinking_level": "minimal",
            "temperature": 0.3,
        }
        mock_key.return_value = "fake-gemini-key-" + "a" * 32
        mock_xai_key.return_value = "fake-xai-key-" + "x" * 32
        mock_hermes_key.return_value = "sk-unused"
        mock_hermes_url.return_value = "http://127.0.0.1:8645/v1"
        mock_provider.return_value = "gemini"  # 默认 gemini

        yield {
            "dialogue_params": mock_dialogue,
            "summary_params": mock_summary,
            "get_api_key": mock_key,
            "get_xai_key": mock_xai_key,
            "get_hermes_key": mock_hermes_key,
            "hermes_base_url": mock_hermes_url,
            "get_provider": mock_provider,
        }


@pytest.fixture
def mock_gemini_client():
    """Mock google.genai.Client 和 types"""
    with patch("scripts.llm.genai.Client") as mock_client_cls, \
         patch("scripts.llm.types") as mock_types:
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.text = "Gemini mocked response"
        mock_response.usage_metadata.prompt_token_count = 100
        mock_response.usage_metadata.candidates_token_count = 50
        mock_response.usage_metadata.thoughts_token_count = 10
        mock_response.usage_metadata.cached_content_token_count = 20
        mock_response.usage_metadata.total_token_count = 180

        mock_client.models.generate_content.return_value = mock_response
        mock_client_cls.return_value = mock_client

        # Mock types.ThinkingConfig 和 types.GenerateContentConfig
        mock_types.ThinkingConfig.return_value = MagicMock()
        mock_types.GenerateContentConfig.return_value = MagicMock()

        yield mock_client_cls


@pytest.fixture
def mock_openai_client():
    """Mock openai.OpenAI client

    Note: OpenAI 是在 _get_openai_client 内部惰性导入的（from openai import OpenAI），
    所以要 patch openai模块，而不是 scripts.llm.OpenAI
    """
    with patch("openai.OpenAI") as mock_openai_cls:
        mock_client = MagicMock()

        # Mock completion response
        mock_choice = MagicMock()
        mock_choice.message.content = "OpenAI mocked response"

        mock_usage = MagicMock()
        mock_usage.prompt_tokens = 100
        mock_usage.completion_tokens = 50
        mock_usage.total_tokens = 150

        # Mock reasoning_tokens (for Grok thinking)
        mock_details = MagicMock()
        mock_details.reasoning_tokens = 10
        mock_usage.completion_tokens_details = mock_details

        # Mock cached_tokens
        mock_prompt_details = MagicMock()
        mock_prompt_details.cached_tokens = 0
        mock_usage.prompt_tokens_details = mock_prompt_details

        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        mock_response.usage = mock_usage

        mock_client.chat.completions.create.return_value = mock_response
        mock_openai_cls.return_value = mock_client

        yield mock_openai_cls


# ── Gemini Provider 测试 ─────────────────────────────────────────────────


class TestGeminiProvider:
    """测试 Gemini provider 的调用逻辑"""

    def test_ask_gemini_basic(self, mock_gemini_settings, mock_gemini_client):
        """测试基本的 Gemini 调用"""
        from scripts.llm import ask_llm

        resp = ask_llm("Hello Gemini")

        assert resp.text == "Gemini mocked response"
        assert resp.usage_metadata.prompt_token_count == 100
        assert resp.usage_metadata.candidates_token_count == 50
        assert resp.usage_metadata.thoughts_token_count == 10
        assert resp.usage_metadata.cached_content_token_count == 20
        assert resp.usage_metadata.total_token_count == 180

        # 验证 client 调用参数
        mock_gemini_client.assert_called_once()
        mock_client = mock_gemini_client.return_value
        mock_client.models.generate_content.assert_called_once()

    def test_ask_gemini_with_profile_summary(self, mock_gemini_settings, mock_gemini_client):
        """测试使用 summary profile"""
        from scripts.llm import ask_llm

        resp = ask_llm("Summarize this", profile="summary", max_output_tokens=2048)

        assert resp.text == "Gemini mocked response"

        # 验证使用了 summary_params
        mock_gemini_settings["summary_params"].assert_called_once()

    def test_ask_gemini_with_system_instruction(self, mock_gemini_settings, mock_gemini_client):
        """测试传入 system_instruction"""
        from scripts.llm import ask_llm
        from scripts import llm

        ask_llm("Hello", system_instruction="You are a helpful assistant")

        # 验证 GenerateContentConfig 被正确调用（参数在 kwargs 里）
        mock_types = llm.types
        config_call_kwargs = mock_types.GenerateContentConfig.call_args[1]
        assert "system_instruction" in config_call_kwargs

    def test_ask_gemini_with_cached_content(self, mock_gemini_settings, mock_gemini_client):
        """测试 Explicit Cache（cached_content）"""
        from scripts.llm import ask_llm
        from scripts import llm

        ask_llm("Hello", cached_content="cache-resource-name")

        # 验证 GenerateContentConfig 被正确调用
        mock_types = llm.types
        config_call_kwargs = mock_types.GenerateContentConfig.call_args[1]

        # cached_content 存在时不应该有 system_instruction
        assert "cached_content" in config_call_kwargs
        assert "system_instruction" not in config_call_kwargs

    def test_ask_gemini_with_response_schema(self, mock_gemini_settings, mock_gemini_client):
        """测试 JSON 结构化输出（response_schema）"""
        from scripts.llm import ask_llm
        from scripts import llm

        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "age": {"type": "integer"},
            },
            "required": ["name", "age"],
        }

        ask_llm("Generate JSON", response_schema=schema)

        # 验证 GenerateContentConfig 被正确调用
        mock_types = llm.types
        config_call_kwargs = mock_types.GenerateContentConfig.call_args[1]

        assert "response_schema" in config_call_kwargs
        assert config_call_kwargs["response_mime_type"] == "application/json"

    def test_ask_gemini_parameter_override(self, mock_gemini_settings, mock_gemini_client):
        """测试 per-call 参数覆盖"""
        from scripts.llm import ask_llm
        from scripts import llm

        ask_llm(
            "Hello",
            model="gemini-2.0-flash",
            temperature=0.9,
            thinking_level="high",
            max_output_tokens=16384,
        )

        # 验证 GenerateContentConfig 被正确调用
        mock_types = llm.types
        config_call_kwargs = mock_types.GenerateContentConfig.call_args[1]

        # 验证覆盖生效
        assert config_call_kwargs["temperature"] == 0.9
        assert config_call_kwargs["max_output_tokens"] == 16384

        # 验证 model 参数传给了 generate_content
        mock_client = mock_gemini_client.return_value
        call_kwargs = mock_client.models.generate_content.call_args[1]
        assert call_kwargs["model"] == "gemini-2.0-flash"

    def test_ask_gemini_multiturns(self, mock_gemini_settings, mock_gemini_client):
        """测试多轮对话格式"""
        from scripts.llm import ask_llm

        contents = [
            {"role": "user", "parts": [{"text": "Hello"}]},
            {"role": "model", "parts": [{"text": "Hi there"}]},
            {"role": "user", "parts": [{"text": "How are you?"}]},
        ]

        resp = ask_llm(contents)

        assert resp.text == "Gemini mocked response"
        mock_client = mock_gemini_client.return_value
        mock_client.models.generate_content.assert_called_once()


# ── Client 初始化和 Key 变更测试 ───────────────────────────────────────


class TestClientManagement:
    """测试 client 惰性初始化和 key 变更重建"""

    def test_gemini_client_lazy_init(self, mock_gemini_settings, mock_gemini_client):
        """测试 Gemini client 惰性初始化"""
        from scripts import llm

        # 重置全局状态
        llm._client = None
        llm._client_key = None

        from scripts.llm import ask_llm

        ask_llm("First call")

        # 第一次调用应该创建 client
        assert mock_gemini_client.call_count == 1

        ask_llm("Second call")

        # 第二次调用应该复用 client（key 没变）
        assert mock_gemini_client.call_count == 1

    def test_gemini_client_recreate_on_key_change(self, mock_gemini_settings, mock_gemini_client):
        """测试 API key 变更时重建 client"""
        from scripts import llm

        # 重置全局状态
        llm._client = None
        llm._client_key = None

        from scripts.llm import ask_llm

        ask_llm("First call")
        first_call_count = mock_gemini_client.call_count

        # 改变 API key
        mock_gemini_settings["get_api_key"].return_value = "new-key-" + "b" * 32

        ask_llm("Second call")

        # key 变了应该重建 client
        assert mock_gemini_client.call_count == first_call_count + 1

    def test_gemini_missing_api_key_raises(self, mock_gemini_settings, mock_gemini_client):
        """测试缺少 API key 时抛出异常"""
        from scripts import llm

        # 重置全局状态
        llm._client = None
        llm._client_key = None

        mock_gemini_settings["get_api_key"].return_value = None

        from scripts.llm import ask_llm

        with pytest.raises(RuntimeError, match="未设置 Gemini API key"):
            ask_llm("Hello")


# ── OpenAI 兼容 Provider 测试（Grok/Hermes）────────────────────────────


class TestOpenAICompatibleProviders:
    """测试 Grok 和 Hermes provider"""

    def test_ask_grok_basic(self, mock_gemini_settings, mock_openai_client):
        """测试 Grok provider 基本调用"""
        from scripts import llm

        # 重置全局状态
        llm._openai_clients = {}

        mock_gemini_settings["get_provider"].return_value = "grok"
        mock_gemini_settings["dialogue_params"].return_value["model"] = "grok-4.5"

        from scripts.llm import ask_llm

        resp = ask_llm("Hello Grok")

        assert resp.text == "OpenAI mocked response"
        assert resp.usage_metadata.prompt_token_count == 100
        assert resp.usage_metadata.candidates_token_count == 50
        assert resp.usage_metadata.thoughts_token_count == 10
        assert resp.usage_metadata.total_token_count == 150

        # 验证使用了 xAI base_url
        mock_openai_client.assert_called_once()
        call_kwargs = mock_openai_client.call_args[1]
        assert call_kwargs["base_url"] == "https://api.x.ai/v1"

    def test_ask_hermes_basic(self, mock_gemini_settings, mock_openai_client):
        """测试 Hermes provider 基本调用"""
        from scripts import llm

        # 重置全局状态
        llm._openai_clients = {}

        mock_gemini_settings["get_provider"].return_value = "hermes"
        mock_gemini_settings["dialogue_params"].return_value["model"] = "grok-4.5"

        from scripts.llm import ask_llm

        resp = ask_llm("Hello Hermes")

        assert resp.text == "OpenAI mocked response"

        # 验证使用了 Hermes base_url
        mock_openai_client.assert_called_once()
        call_kwargs = mock_openai_client.call_args[1]
        assert call_kwargs["base_url"] == "http://127.0.0.1:8645/v1"

    def test_openai_provider_missing_key_raises(self, mock_gemini_settings, mock_openai_client):
        """测试 OpenAI 兼容 provider 缺少 key 时抛出异常"""
        from scripts import llm

        # 重置全局状态
        llm._openai_clients = {}

        mock_gemini_settings["get_provider"].return_value = "grok"
        mock_gemini_settings["get_xai_key"].return_value = None

        from scripts.llm import ask_llm

        with pytest.raises(RuntimeError, match="未设置.*API key"):
            ask_llm("Hello")

    def test_openai_client_recreate_on_key_change(self, mock_gemini_settings, mock_openai_client):
        """测试 OpenAI 客户端在 key 变更时重建"""
        from scripts import llm

        # 重置全局状态
        llm._openai_clients = {}

        mock_gemini_settings["get_provider"].return_value = "grok"
        mock_gemini_settings["dialogue_params"].return_value["model"] = "grok-4"

        from scripts.llm import ask_llm

        ask_llm("First call")
        first_call_count = mock_openai_client.call_count

        # 改变 API key
        mock_gemini_settings["get_xai_key"].return_value = "new-xai-key"

        ask_llm("Second call")

        # key 变了应该重建 client
        assert mock_openai_client.call_count == first_call_count + 1

    def test_openai_client_recreate_on_base_url_change(self, mock_gemini_settings, mock_openai_client):
        """测试 Hermes base_url 变更时重建 client"""
        from scripts import llm

        # 重置全局状态
        llm._openai_clients = {}

        mock_gemini_settings["get_provider"].return_value = "hermes"

        from scripts.llm import ask_llm

        ask_llm("First call")
        first_call_count = mock_openai_client.call_count

        # 改变 base_url
        mock_gemini_settings["hermes_base_url"].return_value = "http://new-host:9999/v1"

        ask_llm("Second call")

        # base_url 变了应该重建 client
        assert mock_openai_client.call_count == first_call_count + 1


# ── thinking_level 映射测试 ───────────────────────────────────────────


class TestThinkingLevel:
    """测试 thinking_level → reasoning_effort 映射"""

    def test_thinking_level_to_reasoning_effort(self, mock_gemini_settings, mock_openai_client):
        """测试 thinking_level 映射到 reasoning_effort"""
        from scripts import llm

        # 重置全局状态
        llm._openai_clients = {}

        mock_gemini_settings["get_provider"].return_value = "grok"
        mock_gemini_settings["dialogue_params"].return_value["model"] = "grok-4.5"

        from scripts.llm import ask_llm

        # minimal → low
        ask_llm("Test", thinking_level="minimal")
        mock_client = mock_openai_client.return_value
        call_kwargs = mock_client.chat.completions.create.call_args[1]
        assert call_kwargs["extra_body"]["reasoning_effort"] == "low"

        # low → low
        ask_llm("Test", thinking_level="low")
        call_kwargs = mock_client.chat.completions.create.call_args[1]
        assert call_kwargs["extra_body"]["reasoning_effort"] == "low"

        # medium → medium
        ask_llm("Test", thinking_level="medium")
        call_kwargs = mock_client.chat.completions.create.call_args[1]
        assert call_kwargs["extra_body"]["reasoning_effort"] == "medium"

        # high → high
        ask_llm("Test", thinking_level="high")
        call_kwargs = mock_client.chat.completions.create.call_args[1]
        assert call_kwargs["extra_body"]["reasoning_effort"] == "high"

    def test_reasoning_effort_not_supported_retry(self, mock_gemini_settings, mock_openai_client):
        """测试不支持 reasoning_effort 时自动去掉重试"""
        from scripts import llm

        # 重置全局状态
        llm._openai_clients = {}

        mock_gemini_settings["get_provider"].return_value = "grok"
        mock_gemini_settings["dialogue_params"].return_value["model"] = "grok-4"

        from scripts.llm import ask_llm

        # Mock 第一次调用失败，第二次成功
        mock_client = mock_openai_client.return_value
        mock_client.chat.completions.create.side_effect = [
            Exception("reasoning_effort is not supported"),
            mock_client.chat.completions.create.return_value,
        ]

        resp = ask_llm("Test", thinking_level="high")

        # 应该重试成功
        assert resp.text == "OpenAI mocked response"
        assert mock_client.chat.completions.create.call_count == 2

        # 第二次调用应该没有 extra_body
        second_call_kwargs = mock_client.chat.completions.create.call_args_list[1][1]
        assert "extra_body" not in second_call_kwargs or second_call_kwargs.get("extra_body") is None

    def test_openai_other_exception_reraise(self, mock_gemini_settings, mock_openai_client):
        """测试非 reasoning_effort 相关的异常会重新抛出"""
        from scripts import llm

        # 重置全局状态
        llm._openai_clients = {}

        mock_gemini_settings["get_provider"].return_value = "grok"

        from scripts.llm import ask_llm

        # Mock 调用失败（非 reasoning_effort 错误）
        mock_client = mock_openai_client.return_value
        mock_client.chat.completions.create.side_effect = Exception("Network error")

        # 应该重新抛出异常
        with pytest.raises(Exception, match="Network error"):
            ask_llm("Test")

        # 不应该重试（只调用一次）
        assert mock_client.chat.completions.create.call_count == 1


# ── response_schema 和 JSON 输出测试 ─────────────────────────────────


class TestStructuredOutput:
    """测试 JSON 结构化输出（response_schema）"""

    def test_openai_response_schema_strictify(self, mock_gemini_settings, mock_openai_client):
        """测试 OpenAI strict schema 转换（添加 additionalProperties: false）"""
        from scripts import llm

        # 重置全局状态
        llm._openai_clients = {}

        mock_gemini_settings["get_provider"].return_value = "grok"

        from scripts.llm import ask_llm

        schema = {
            "type": "object",
            "properties": {
                "summary": {"type": "string"},
                "keywords": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
            "required": ["summary", "keywords"],
        }

        ask_llm("Generate JSON", response_schema=schema)

        mock_client = mock_openai_client.return_value
        call_kwargs = mock_client.chat.completions.create.call_args[1]

        # 验证 response_format 存在
        assert "response_format" in call_kwargs
        response_format = call_kwargs["response_format"]
        assert response_format["type"] == "json_schema"

        # 验证添加了 additionalProperties: false
        strict_schema = response_format["json_schema"]["schema"]
        assert strict_schema["additionalProperties"] is False
        assert response_format["json_schema"]["strict"] is True

    def test_openai_nested_schema_strictify(self, mock_gemini_settings, mock_openai_client):
        """测试嵌套对象的 strict 转换"""
        from scripts import llm

        # 重置全局状态
        llm._openai_clients = {}

        mock_gemini_settings["get_provider"].return_value = "grok"

        from scripts.llm import ask_llm

        schema = {
            "type": "object",
            "properties": {
                "user": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "age": {"type": "integer"},
                    },
                    "required": ["name", "age"],
                },
            },
            "required": ["user"],
        }

        ask_llm("Generate JSON", response_schema=schema)

        mock_client = mock_openai_client.return_value
        call_kwargs = mock_client.chat.completions.create.call_args[1]

        strict_schema = call_kwargs["response_format"]["json_schema"]["schema"]

        # 顶层和嵌套对象都应该有 additionalProperties: false
        assert strict_schema["additionalProperties"] is False
        assert strict_schema["properties"]["user"]["additionalProperties"] is False


# ── messages 格式转换测试 ────────────────────────────────────────────


class TestMessageConversion:
    """测试 Gemini 格式 → OpenAI messages 转换"""

    def test_to_messages_single_string(self, mock_gemini_settings, mock_openai_client):
        """测试单轮字符串转换"""
        from scripts import llm

        # 重置全局状态
        llm._openai_clients = {}

        mock_gemini_settings["get_provider"].return_value = "grok"

        from scripts.llm import ask_llm

        ask_llm("Hello", system_instruction="You are helpful")

        mock_client = mock_openai_client.return_value
        call_kwargs = mock_client.chat.completions.create.call_args[1]

        messages = call_kwargs["messages"]
        assert len(messages) == 2
        assert messages[0] == {"role": "system", "content": "You are helpful"}
        assert messages[1] == {"role": "user", "content": "Hello"}

    def test_to_messages_multiturns(self, mock_gemini_settings, mock_openai_client):
        """测试多轮对话转换"""
        from scripts import llm

        # 重置全局状态
        llm._openai_clients = {}

        mock_gemini_settings["get_provider"].return_value = "grok"

        from scripts.llm import ask_llm

        contents = [
            {"role": "user", "parts": [{"text": "Hello"}]},
            {"role": "model", "parts": [{"text": "Hi there"}]},
            {"role": "user", "parts": [{"text": "How are you?"}]},
        ]

        ask_llm(contents)

        mock_client = mock_openai_client.return_value
        call_kwargs = mock_client.chat.completions.create.call_args[1]

        messages = call_kwargs["messages"]
        assert len(messages) == 3
        assert messages[0] == {"role": "user", "content": "Hello"}
        assert messages[1] == {"role": "assistant", "content": "Hi there"}
        assert messages[2] == {"role": "user", "content": "How are you?"}

    def test_to_messages_multipart_concatenation(self, mock_gemini_settings, mock_openai_client):
        """测试多 part 文本拼接"""
        from scripts import llm

        # 重置全局状态
        llm._openai_clients = {}

        mock_gemini_settings["get_provider"].return_value = "grok"

        from scripts.llm import ask_llm

        contents = [
            {"role": "user", "parts": [
                {"text": "Part 1. "},
                {"text": "Part 2. "},
                {"text": "Part 3."},
            ]},
        ]

        ask_llm(contents)

        mock_client = mock_openai_client.return_value
        call_kwargs = mock_client.chat.completions.create.call_args[1]

        messages = call_kwargs["messages"]
        assert messages[0]["content"] == "Part 1. Part 2. Part 3."


# ── 完整流程集成测试 ─────────────────────────────────────────────────


class TestEndToEnd:
    """端到端流程测试"""

    def test_dialogue_to_summary_profile_switch(self, mock_gemini_settings, mock_gemini_client):
        """测试 dialogue/summary profile 切换"""
        from scripts.llm import ask_llm

        # dialogue profile
        ask_llm("Question", profile="dialogue")
        mock_gemini_settings["dialogue_params"].assert_called()

        # summary profile
        ask_llm("Summarize", profile="summary", max_output_tokens=2048)
        mock_gemini_settings["summary_params"].assert_called()

    def test_provider_switch_gemini_to_grok(
        self, mock_gemini_settings, mock_gemini_client, mock_openai_client
    ):
        """测试 provider 从 Gemini 切换到 Grok"""
        from scripts import llm

        # 重置全局状态
        llm._client = None
        llm._client_key = None
        llm._openai_clients = {}

        from scripts.llm import ask_llm

        # 先用 Gemini
        mock_gemini_settings["get_provider"].return_value = "gemini"
        resp1 = ask_llm("Hello Gemini")
        assert resp1.text == "Gemini mocked response"
        assert mock_gemini_client.call_count == 1

        # 切换到 Grok
        mock_gemini_settings["get_provider"].return_value = "grok"
        resp2 = ask_llm("Hello Grok")
        assert resp2.text == "OpenAI mocked response"
        assert mock_openai_client.call_count == 1

    def test_main_script_execution(self, mock_gemini_settings, mock_gemini_client, capsys):
        """测试 __main__ 脚本执行"""
        from scripts import llm

        # 重置全局状态
        llm._client = None
        llm._client_key = None

        # 模拟 if __name__ == "__main__": 逻辑
        from scripts.llm import ask_llm, dialogue_params, get_provider

        resp = ask_llm("hello")
        params = dialogue_params()
        provider = get_provider()

        # 验证输出可以正常生成（实际 __main__ 会 print）
        assert resp.text == "Gemini mocked response"
        assert params["model"] == "gemini-3.5-flash"
        assert provider == "gemini"
