"""Gemini 运行时可调参数 + API key 的持久化，供 Streamlit「⚙️ Gemini 设置」UI 读写。

设计要点：
- 分成 dialogue（问答）和 summary（摘要/长期记忆/心智地图/对话记忆）两组，互不影响。
- 每次调用都从 GEMINI_SETTINGS_PATH 读最新值（文件很小、LLM 调用本就不频繁），所以在 UI 改完
  下一次问答/摘要立即生效，不用重启服务。
- 文件不存在或缺某个字段时，回退到 config.py 里的默认值——删掉这个文件 = 完全恢复默认。
- API key 优先用这个文件里的；没有则回退 .env / 环境变量（向后兼容旧的 .env 用法）。
  文件在 private.nosync/（gitignore + iCloud 不同步），和 .env 同级别的本地私密存储。
- provider（"gemini" / "grok"）是 LLM 后端的手动开关，见 scripts/llm.py。选 grok 时用 xai_api_key
  （回退环境变量 XAI_API_KEY），模型名在对话/摘要的「模型」框里填 grok 系列（如 grok-4）。
"""
import json
import os
from typing import Optional

from dotenv import load_dotenv

from config import (
    ENV_PATH,
    GEMINI_MAX_OUTPUT_TOKENS,
    GEMINI_MODEL,
    GEMINI_SETTINGS_PATH,
    GEMINI_SUMMARY_MAX_TOKENS,
    GEMINI_SUMMARY_MODEL,
    GEMINI_SUMMARY_TEMPERATURE,
    GEMINI_SUMMARY_THINKING_LEVEL,
    GEMINI_TEMPERATURE,
    GEMINI_THINKING_LEVEL,
    HERMES_API_KEY,
    HERMES_BASE_URL,
)

# 兼容旧的 .env 存 key 方式：导入时把 private.nosync/.env 读进环境变量，作为 API key 的回退来源。
load_dotenv(ENV_PATH)

# LLM 后端 provider：默认 gemini。grok = xAI（OpenAI 兼容端点）；hermes = 本地 Hermes Agent
# Gateway（OpenAI 兼容代理，转发到 xAI grok，见 scripts/llm.py）。grok / hermes 共用同一套调用代码。
VALID_PROVIDERS = ("gemini", "grok", "hermes")
DEFAULT_PROVIDER = "gemini"

SUMMARY_MAX_TASKS = {
    "text": "文本摘要类（每份摘要 / 长期记忆 / AI 对话记忆）",
    "chat_graph": "AI 对话记忆心智地图",
    "therapy_graph": "真实咨询心智地图",
}

_DEFAULT_DIALOGUE = {
    "model": GEMINI_MODEL,
    "thinking_level": GEMINI_THINKING_LEVEL,
    "temperature": GEMINI_TEMPERATURE,
    "max_output_tokens": GEMINI_MAX_OUTPUT_TOKENS,
}
_DEFAULT_SUMMARY = {
    "model": GEMINI_SUMMARY_MODEL,
    "thinking_level": GEMINI_SUMMARY_THINKING_LEVEL,
    "temperature": GEMINI_SUMMARY_TEMPERATURE,
    "max_output_tokens": dict(GEMINI_SUMMARY_MAX_TOKENS),
}


def _load_raw() -> dict:
    if GEMINI_SETTINGS_PATH.exists():
        try:
            return json.loads(GEMINI_SETTINGS_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {}


def _merge(default: dict, override: Optional[dict]) -> dict:
    """浅合并：override 里为 None/缺失的字段用 default 顶上。"""
    override = override or {}
    return {k: (override[k] if override.get(k) is not None else v) for k, v in default.items()}


def dialogue_params() -> dict:
    """问答用的参数：{model, thinking_level, temperature, max_output_tokens}。"""
    return _merge(_DEFAULT_DIALOGUE, _load_raw().get("dialogue"))


def summary_params() -> dict:
    """摘要类任务共享的参数：{model, thinking_level, temperature}（max_output_tokens 见下，按任务分档）。"""
    merged = _merge(
        {k: v for k, v in _DEFAULT_SUMMARY.items() if k != "max_output_tokens"},
        _load_raw().get("summary"),
    )
    return merged


def summary_max_tokens(task: str) -> int:
    """按任务档位取 summary 的 max_output_tokens（task ∈ SUMMARY_MAX_TASKS 的键）。"""
    raw = (_load_raw().get("summary") or {}).get("max_output_tokens") or {}
    return int(raw.get(task) or GEMINI_SUMMARY_MAX_TOKENS[task])


def provider() -> str:
    """当前 LLM 后端："gemini" 或 "grok"（非法/缺失回退 DEFAULT_PROVIDER）。"""
    p = _load_raw().get("provider")
    return p if p in VALID_PROVIDERS else DEFAULT_PROVIDER


def get_api_key() -> Optional[str]:
    return _load_raw().get("api_key") or os.environ.get("GEMINI_API_KEY")


def get_xai_key() -> Optional[str]:
    return _load_raw().get("xai_api_key") or os.environ.get("XAI_API_KEY")


def get_hermes_key() -> Optional[str]:
    # Hermes 代理自己夹 OAuth，key 任意；给个默认值，仍可被设置/.env 覆盖。
    return _load_raw().get("hermes_api_key") or os.environ.get("HERMES_API_KEY") or HERMES_API_KEY


def hermes_base_url() -> str:
    return _load_raw().get("hermes_base_url") or HERMES_BASE_URL


def load_for_ui() -> dict:
    """给设置 UI 用：返回当前生效值 + 各 api key 是否已设置（不回传 key 明文）。"""
    return {
        "provider": provider(),
        "dialogue": dialogue_params(),
        "summary": summary_params(),
        "summary_max_tokens": {t: summary_max_tokens(t) for t in SUMMARY_MAX_TASKS},
        "api_key_set": bool(get_api_key()),
        "xai_api_key_set": bool(get_xai_key()),
        "hermes_base_url": hermes_base_url(),
    }


def save(
    dialogue: dict,
    summary: dict,
    summary_max: dict,
    api_key: Optional[str] = None,
    provider: Optional[str] = None,
    xai_api_key: Optional[str] = None,
    hermes_api_key: Optional[str] = None,
    hermes_base_url: Optional[str] = None,
) -> None:
    """写回设置。api_key / xai_api_key / hermes_api_key 传空/None = 保留原有（不清空）；传非空 = 覆盖。
    hermes_base_url 传非空 = 覆盖。"""
    data = _load_raw()
    data["dialogue"] = dialogue
    data["summary"] = {**summary, "max_output_tokens": summary_max}
    if provider in VALID_PROVIDERS:
        data["provider"] = provider
    if api_key:
        data["api_key"] = api_key.strip()
    if xai_api_key:
        data["xai_api_key"] = xai_api_key.strip()
    if hermes_api_key:
        data["hermes_api_key"] = hermes_api_key.strip()
    if hermes_base_url:
        data["hermes_base_url"] = hermes_base_url.strip()
    GEMINI_SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    GEMINI_SETTINGS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def reset() -> None:
    """恢复默认参数（保留已设置的 API key、xAI key、provider 选择——这些是凭证/后端选择，不是可调参数）。"""
    raw = _load_raw()
    data = {}
    for k in ("api_key", "xai_api_key", "hermes_api_key", "hermes_base_url", "provider"):
        if raw.get(k):
            data[k] = raw[k]
    GEMINI_SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    GEMINI_SETTINGS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
