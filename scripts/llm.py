"""LLM 问答封装：所有对 LLM 的调用都走这里，通过 provider 开关在 Gemini / Grok(xAI) 之间切换。
from typing import Optional

运行参数（模型/thinking level/温度/max tokens）、provider 选择、以及两个 provider 的 API key
都从 scripts/settings.py 读取，后者从 private.nosync/gemini_settings.json 读最新值，可在 Streamlit
「⚙️ Gemini 设置」里改，改完下一次调用即生效、无需重启。profile 决定用哪一组参数：
  - profile="dialogue"（默认）：问答用的参数
  - profile="summary"：摘要/长期记忆/心智地图/对话记忆这类批量提炼任务用的（更便宜的模型等）
per-call 传入的 model/temperature/thinking_level/max_output_tokens 优先级最高，会覆盖 profile 值
（摘要脚本用它把 max_output_tokens 按任务档位传进来，见 scripts/settings.summary_max_tokens）。

两个 provider 的返回对象被归一化成同一个形状（.text + .usage_metadata.{prompt,candidates,
thoughts,cached,total}_token_count），所以调用方（ask.py/summarize.py/... ）不用关心用的是哪个后端。

Gemini 专有的 Explicit Caching（cached_content）只在 provider=gemini 时有意义；provider 为
OpenAI 兼容后端（grok / hermes）时 scripts/context_cache.get_cache_name() 会直接返回 None，
调用方自动退回内联 system_instruction。

OpenAI 兼容后端（grok = xAI 直连；hermes = 本地 Hermes Agent Gateway 代理，转发到 xAI grok、
自己夹 OAuth）共用同一套 _ask_openai_compatible()，只是 base_url / key 来源不同（见 _OPENAI_PROVIDERS）。
"""
from dataclasses import dataclass

from google import genai
from google.genai import types

from scripts.settings import (
    dialogue_params,
    get_api_key,
    get_hermes_key,
    get_xai_key,
    hermes_base_url,
    provider as get_provider,
    summary_params,
)

XAI_BASE_URL = "https://api.x.ai/v1"

# OpenAI 兼容后端注册表：provider 名 → 拿 (base_url, key, 人类可读名, 环境变量名) 的函数。
# 要再加一个 OpenAI 兼容网关，只需在这里加一行 + 在 settings.VALID_PROVIDERS 里加名字。
_OPENAI_PROVIDERS = {
    "grok": lambda: (XAI_BASE_URL, get_xai_key(), "xAI", "XAI_API_KEY"),
    "hermes": lambda: (hermes_base_url(), get_hermes_key(), "Hermes Agent Gateway", "HERMES_API_KEY"),
}

_client = None
_client_key = None
_openai_clients = {}  # provider 名 → (client, base_url, key)，key/base_url 变了会重建


def _get_client() -> genai.Client:
    """惰性创建 Gemini client；如果 API key 在 UI 里被改了，下一次调用会用新 key 重建。"""
    global _client, _client_key
    key = get_api_key()
    if not key:
        raise RuntimeError(
            "未设置 Gemini API key。请在 Streamlit 侧边栏「⚙️ Gemini 设置」里填写，"
            "或写进 private.nosync/.env（GEMINI_API_KEY=...）。"
        )
    if _client is None or key != _client_key:
        _client = genai.Client(api_key=key)
        _client_key = key
    return _client


def _get_openai_client(provider_name: str):
    """惰性创建某个 OpenAI 兼容后端（grok / hermes）的 client；base_url 或 key 变了会重建。"""
    base_url, key, label, env_name = _OPENAI_PROVIDERS[provider_name]()
    if not key:
        raise RuntimeError(
            f"未设置 {label} API key。请在 Streamlit 侧边栏「⚙️ Gemini 设置」里填写，"
            f"或写进 private.nosync/.env（{env_name}=...）。"
        )
    cached = _openai_clients.get(provider_name)
    if cached is None or cached[1] != base_url or cached[2] != key:
        from openai import OpenAI  # 惰性 import：只在真的用到 OpenAI 兼容后端时才需要装 openai

        _openai_clients[provider_name] = (OpenAI(api_key=key, base_url=base_url), base_url, key)
    return _openai_clients[provider_name][0]


# ---- 返回对象归一化：让 Grok 的响应长得和 Gemini 的一样（调用方只用 .text / .usage_metadata）----
@dataclass
class _Usage:
    prompt_token_count: int = 0
    candidates_token_count: int = 0
    thoughts_token_count: int = 0
    cached_content_token_count: int = 0
    total_token_count: int = 0


@dataclass
class _Response:
    text: str
    usage_metadata: _Usage


def ask_llm(
    contents,
    *,
    profile: str = "dialogue",
    system_instruction: Optional[str] = None,
    response_schema: Optional[dict] = None,
    max_output_tokens: Optional[int] = None,
    model: Optional[str] = None,
    temperature: Optional[float] = None,
    thinking_level: Optional[str] = None,
    cached_content: Optional[str] = None,
):
    """contents 可以是字符串（单轮），也可以是 [{"role": "user"/"model", "parts": [{"text": ...}]}] 列表（多轮）。

    profile：选 "dialogue" 或 "summary" 这组默认参数。
    response_schema：传入则强制 JSON 结构化输出（summarize/build_graph 用）。
    cached_content：仅 Gemini 有意义（Explicit Cache 资源名）；provider=grok 时调用方传进来的恒为
    None（见 context_cache.get_cache_name）。传入时 system_instruction 已烘焙进缓存，不能再重复传。
    model/temperature/thinking_level/max_output_tokens：per-call 覆盖，None 表示用 profile 的值。

    返回对象两个 provider 统一暴露 .text 和 .usage_metadata（.{prompt,candidates,thoughts,
    cached,total}_token_count）。
    """
    params = summary_params() if profile == "summary" else dialogue_params()
    model = model or params["model"]
    temperature = temperature if temperature is not None else params["temperature"]
    thinking_level = thinking_level or params["thinking_level"]
    # dialogue 的 params 里带 max_output_tokens；summary 的 max 按任务档位由调用方显式传入。
    max_output_tokens = max_output_tokens or params.get("max_output_tokens") or 4096

    prov = get_provider()
    if prov in _OPENAI_PROVIDERS:
        return _ask_openai_compatible(
            prov,
            contents,
            system_instruction=system_instruction,
            response_schema=response_schema,
            max_output_tokens=max_output_tokens,
            model=model,
            temperature=temperature,
            thinking_level=thinking_level,
        )

    return _ask_gemini(
        contents,
        system_instruction=system_instruction,
        response_schema=response_schema,
        max_output_tokens=max_output_tokens,
        model=model,
        temperature=temperature,
        thinking_level=thinking_level,
        cached_content=cached_content,
    )


def _ask_gemini(
    contents,
    *,
    system_instruction,
    response_schema,
    max_output_tokens,
    model,
    temperature,
    thinking_level,
    cached_content,
):
    config_kwargs = dict(
        temperature=temperature,
        max_output_tokens=max_output_tokens,
        thinking_config=types.ThinkingConfig(thinking_level=thinking_level),
    )
    if cached_content:
        config_kwargs["cached_content"] = cached_content
    elif system_instruction:
        config_kwargs["system_instruction"] = system_instruction
    if response_schema is not None:
        config_kwargs["response_schema"] = response_schema
        config_kwargs["response_mime_type"] = "application/json"

    return _get_client().models.generate_content(
        model=model,
        contents=contents,
        config=types.GenerateContentConfig(**config_kwargs),
    )


# thinking_level（Gemini 概念）→ OpenAI 兼容后端的 reasoning_effort（low/medium/high）。
# minimal→low，其余原样透传。grok-4.5（经 Hermes 代理）支持 low/medium/high 且默认 high、不能关；
# 少数模型（如 grok-4）不接受 reasoning_effort，_ask_openai_compatible 里对这种情况自动去掉重试兜底。
_THINKING_TO_EFFORT = {"minimal": "low", "low": "low", "medium": "medium", "high": "high"}


def _strictify(schema):
    """把普通 JSON Schema 递归转成 OpenAI/xAI structured-output 的 strict 形式：
    每个 object 加 additionalProperties=false。本项目的 schema 已把所有属性列进 required，
    所以只需补这一项。"""
    if isinstance(schema, dict):
        out = {k: _strictify(v) for k, v in schema.items()}
        if out.get("type") == "object":
            out.setdefault("additionalProperties", False)
        return out
    if isinstance(schema, list):
        return [_strictify(v) for v in schema]
    return schema


def _to_messages(contents, system_instruction):
    """把 ask_llm 的 contents（字符串或 Gemini 风格的多轮列表）转成 OpenAI chat messages。"""
    messages = []
    if system_instruction:
        messages.append({"role": "system", "content": system_instruction})
    if isinstance(contents, str):
        messages.append({"role": "user", "content": contents})
        return messages
    for turn in contents:
        role = "assistant" if turn.get("role") in ("model", "assistant") else "user"
        text = "".join(part.get("text", "") for part in turn.get("parts", []))
        messages.append({"role": role, "content": text})
    return messages


def _ask_openai_compatible(
    provider_name,
    contents,
    *,
    system_instruction,
    response_schema,
    max_output_tokens,
    model,
    temperature,
    thinking_level,
):
    client = _get_openai_client(provider_name)
    messages = _to_messages(contents, system_instruction)

    kwargs = dict(
        model=model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_output_tokens,
    )
    if response_schema is not None:
        kwargs["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": "response",
                "schema": _strictify(response_schema),
                "strict": True,
            },
        }
    # reasoning_effort 放进 extra_body（部分代理/SDK 版本不认顶层参数，extra_body 最稳）。
    effort = _THINKING_TO_EFFORT.get(thinking_level)
    extra_body = {"reasoning_effort": effort} if effort else None

    try:
        resp = client.chat.completions.create(extra_body=extra_body, **kwargs) if extra_body \
            else client.chat.completions.create(**kwargs)
    except Exception as e:  # noqa: BLE001
        # 少数模型（如 grok-4）不接受 reasoning_effort，会报 400；自动去掉重试一次。
        if extra_body and "reasoning_effort" in str(e):
            resp = client.chat.completions.create(**kwargs)
        else:
            raise

    text = resp.choices[0].message.content or ""
    u = resp.usage
    details = getattr(u, "completion_tokens_details", None)
    reasoning = getattr(details, "reasoning_tokens", 0) or 0 if details else 0
    prompt_details = getattr(u, "prompt_tokens_details", None)
    cached = getattr(prompt_details, "cached_tokens", 0) or 0 if prompt_details else 0
    usage = _Usage(
        prompt_token_count=u.prompt_tokens or 0,
        candidates_token_count=u.completion_tokens or 0,
        thoughts_token_count=reasoning,
        cached_content_token_count=cached,
        total_token_count=u.total_tokens or 0,
    )
    return _Response(text=text, usage_metadata=usage)


if __name__ == "__main__":
    resp = ask_llm("hello")
    params = dialogue_params()
    print("provider:", get_provider(), "| 对话模型:", params["model"], "| thinking:", params["thinking_level"])
    print("回复:", resp.text)
    print("usage:", resp.usage_metadata)
