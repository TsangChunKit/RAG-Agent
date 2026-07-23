"""静态大块内容（system instruction + 长期记忆 + 心智地图）的 Explicit Caching 管理。

这三样内容变动很少（system instruction 只在设置里手动编辑才变、长期记忆只在 M4/M5 跑完才变、
心智地图只在手动"重新生成"才变），适合长期复用一个显式缓存对象，比每次都指望隐式缓存
"猜对前缀"更确定，也不受隐式缓存那几秒传播延迟、以及 9K-17K token"死区"的影响（已在
2026-07 联网核对：gemini-3.5-flash 显式缓存门槛是 4096 token）。

刻意不缓存的：AI 对话记忆（chat_memory，后台看门狗每 30 分钟空闲就可能更新一次）、
检索片段、完整逐字稿、当前问题——这些天然每次都不同或变动太频繁，缓存了也没意义，
还会增加"内容一变就要重建缓存"的 churn。

用一个内容指纹（hash）+ 本地状态文件判断要不要重建缓存：内容没变就复用，变了就
创建新的并删掉旧的（避免旧缓存继续计费）。如果静态内容总长度不够 4096 token 门槛，
或创建失败（网络问题/配额等），get_cache_name() 返回 None，调用方应退回旧的内联方式。

支持 workspace：每个 workspace 有独立的缓存状态文件。
"""
import hashlib
import json
from typing import Optional

from google.genai import types

from config import EXPLICIT_CACHE_STATE_PATH
from scripts.llm import _get_client
from scripts.settings import dialogue_params, provider as get_provider

CACHE_TTL = "86400s"  # 24 小时；内容没变就一直复用，变了会在下次调用时自动检测重建
MIN_CACHE_TOKENS = 4096  # 已联网核对（ai.google.dev/gemini-api/docs/generate-content/caching）


def _fingerprint(model: str, system_instruction: str, static_content: str) -> str:
    return hashlib.sha256(f"{model}\n{system_instruction}\n{static_content}".encode("utf-8")).hexdigest()


def _load_state(workspace_id: Optional[str] = None) -> Optional[dict]:
    """加载缓存状态（workspace 感知）。"""
    cache_state_path = EXPLICIT_CACHE_STATE_PATH(workspace_id)
    if not cache_state_path.exists():
        return None
    try:
        return json.loads(cache_state_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _save_state(fingerprint: str, cache_name: str, workspace_id: Optional[str] = None) -> None:
    """保存缓存状态（workspace 感知）。"""
    cache_state_path = EXPLICIT_CACHE_STATE_PATH(workspace_id)
    cache_state_path.parent.mkdir(parents=True, exist_ok=True)
    cache_state_path.write_text(
        json.dumps({"fingerprint": fingerprint, "cache_name": cache_name}, ensure_ascii=False), encoding="utf-8"
    )


def get_cache_name(system_instruction: str, static_content: str, model: Optional[str] = None, workspace_id: Optional[str] = None) -> Optional[str]:
    """返回可用的显式缓存资源名；静态内容不够门槛，或创建失败时返回 None（调用方应退回内联方式）。
    model 默认用当前"对话"模型——缓存必须和 generate_content 用的模型一致；模型进了指纹，
    所以在 UI 里改了对话模型，指纹会变、缓存会用新模型自动重建。

    workspace_id: workspace ID（None = 当前 workspace），每个 workspace 有独立的缓存状态。
    """
    # Explicit Caching 是 Gemini 专有能力；用 Grok 时没有对应资源，直接退回内联方式。
    if get_provider() != "gemini":
        return None
    model = model or dialogue_params()["model"]
    client = _get_client()
    fingerprint = _fingerprint(model, system_instruction, static_content)
    state = _load_state(workspace_id)

    if state and state.get("fingerprint") == fingerprint:
        try:
            client.caches.get(name=state["cache_name"])
            return state["cache_name"]
        except Exception:
            pass  # 缓存可能已经过期或被删了，走下面重新创建

    count = client.models.count_tokens(model=model, contents=static_content).total_tokens
    if count < MIN_CACHE_TOKENS:
        return None

    try:
        cache = client.caches.create(
            model=model,
            config=types.CreateCachedContentConfig(
                display_name="ai_therapist_static_context",
                system_instruction=system_instruction,
                contents=[static_content],
                ttl=CACHE_TTL,
            ),
        )
    except Exception as e:  # noqa: BLE001
        print(f"[context_cache] 创建显式缓存失败，退回内联方式: {e}")
        return None

    if state and state.get("cache_name"):
        try:
            client.caches.delete(state["cache_name"])
        except Exception:  # noqa: BLE001
            pass

    _save_state(fingerprint, cache.name, workspace_id)
    return cache.name
