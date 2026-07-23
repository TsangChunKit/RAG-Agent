"""索引运行时可调参数的持久化，供 Streamlit「⚙️ 索引设置」UI 读写。

和 scripts/settings.py（Gemini 参数）是同一套设计，只是管的是"索引/检索"这侧的参数：
- retrieval（检索）：top_k、父块窗口扩展——纯查询期参数，改完下一次问答立即生效，无需重建。
- chunking（分块）：chunk_size、chunk_overlap——只影响"之后新入库"的记录；要对全部历史生效
  需重建向量库（UI 里有「全量重建」按钮）。
- embedding（本地 BGE-M3）：model / device / batch_size / use_fp16——模型在进程内缓存为单例，
  改 model/device 需重启服务才生效（下次 _get_model() 加载时读到新值）。
- fts（关键词检索分词）：base_tokenizer / ngram——建索引时才用到，改完需重建索引生效。
- reranker（cross-encoder 精排）：use_reranker / rerank_top_k / final_top_k 是纯查询期后处理，
  改完下一次问答立即生效、无需重建；model / device / use_fp16 同 embedding——模型进程内缓存为
  单例，改完需重启服务才生效（下次 _get_reranker() 加载时读到新值）。
- graph_evidence（心智地图证据片段）：max_dates / fragments_per_date / window_expand /
  include_summary——命中锚点后沿图收集的证据日期，用定向片段+摘要代替整份逐字稿，纯查询期后
  处理，改完下一次问答立即生效、无需重建。

每次调用都从 INDEX_SETTINGS_PATH 读最新值（文件很小），所以检索类参数改完立即生效。
文件不存在或缺某字段时，回退到 config.py 里的默认常量——删掉这个文件 = 完全恢复默认。

注意：索引全程本地运行（分块纯 Python、BGE-M3 在本机 MPS 上算向量、LanceDB 是本地文件），
建索引/检索都不出网；唯一出网的是问答/摘要时调 Gemini。这些参数都不涉及任何云端调用。
"""
import json

from config import (
    CHUNK_OVERLAP_CHARS,
    CHUNK_SIZE_CHARS,
    CHUNK_WINDOW_EXPAND,
    EMBEDDING_BATCH_SIZE,
    EMBEDDING_DEVICE,
    EMBEDDING_MODEL_NAME,
    EMBEDDING_USE_FP16,
    FINAL_TOP_K,
    FTS_BASE_TOKENIZER,
    FTS_NGRAM_MAX_LENGTH,
    FTS_NGRAM_MIN_LENGTH,
    GRAPH_EVIDENCE_FRAGMENTS_PER_DATE,
    GRAPH_EVIDENCE_INCLUDE_SUMMARY,
    GRAPH_EVIDENCE_MAX_DATES,
    GRAPH_EVIDENCE_WINDOW_EXPAND,
    INDEX_SETTINGS_PATH,
    RERANKER_DEVICE,
    RERANKER_MODEL_NAME,
    RERANKER_TOP_K,
    RERANKER_USE_FP16,
    RETRIEVAL_TOP_K,
    USE_RERANKER,
)

_DEFAULT_RETRIEVAL = {
    "top_k": RETRIEVAL_TOP_K,
    "window_expand": CHUNK_WINDOW_EXPAND,
}
_DEFAULT_CHUNKING = {
    "chunk_size": CHUNK_SIZE_CHARS,
    "chunk_overlap": CHUNK_OVERLAP_CHARS,
}
_DEFAULT_EMBEDDING = {
    "model": EMBEDDING_MODEL_NAME,
    "device": EMBEDDING_DEVICE,
    "batch_size": EMBEDDING_BATCH_SIZE,
    "use_fp16": EMBEDDING_USE_FP16,
}
_DEFAULT_FTS = {
    "base_tokenizer": FTS_BASE_TOKENIZER,
    "ngram_min": FTS_NGRAM_MIN_LENGTH,
    "ngram_max": FTS_NGRAM_MAX_LENGTH,
}
_DEFAULT_RERANKER = {
    "use_reranker": USE_RERANKER,       # A/B 开关：关掉退回纯 hybrid（查询期，立即生效）
    "rerank_top_k": RERANKER_TOP_K,     # 开 rerank 时 hybrid 先取的候选数（查询期，立即生效）
    "final_top_k": FINAL_TOP_K,         # rerank 后最终保留、进入父块扩展的数量（查询期，立即生效）
    "model": RERANKER_MODEL_NAME,       # 换模型需重启（进程内单例缓存）
    "device": RERANKER_DEVICE,          # 改设备需重启
    "use_fp16": RERANKER_USE_FP16,      # 改精度需重启
}
_DEFAULT_GRAPH_EVIDENCE = {
    "max_dates": GRAPH_EVIDENCE_MAX_DATES,                  # 最多取几个证据日（0=关闭；查询期，立即生效）
    "fragments_per_date": GRAPH_EVIDENCE_FRAGMENTS_PER_DATE,  # 每个证据日捞几段（查询期，立即生效）
    "window_expand": GRAPH_EVIDENCE_WINDOW_EXPAND,          # 证据片段专属父块扩展（查询期，立即生效）
    "include_summary": GRAPH_EVIDENCE_INCLUDE_SUMMARY,      # 是否附整场结构化摘要（查询期，立即生效）
}


def _load_raw() -> dict:
    if INDEX_SETTINGS_PATH.exists():
        try:
            return json.loads(INDEX_SETTINGS_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {}


def _merge(default: dict, override: dict | None) -> dict:
    """浅合并：override 里为 None/缺失的字段用 default 顶上（布尔 False 会保留，不当作缺失）。"""
    override = override or {}
    return {k: (override[k] if override.get(k) is not None else v) for k, v in default.items()}


def retrieval_params() -> dict:
    """查询期参数：{top_k, window_expand}。改完下一次问答立即生效。"""
    return _merge(_DEFAULT_RETRIEVAL, _load_raw().get("retrieval"))


def chunking_params() -> dict:
    """分块参数：{chunk_size, chunk_overlap}。只影响之后新入库的记录。"""
    return _merge(_DEFAULT_CHUNKING, _load_raw().get("chunking"))


def embedding_params() -> dict:
    """本地 BGE-M3 参数：{model, device, batch_size, use_fp16}。model/device 改动需重启。"""
    return _merge(_DEFAULT_EMBEDDING, _load_raw().get("embedding"))


def fts_params() -> dict:
    """FTS 关键词分词参数：{base_tokenizer, ngram_min, ngram_max}。改完需重建索引。"""
    return _merge(_DEFAULT_FTS, _load_raw().get("fts"))


def reranker_params() -> dict:
    """Reranker 精排参数：{use_reranker, rerank_top_k, final_top_k, model, device, use_fp16}。
    前三个（开关/候选数/保留数）是查询期后处理，改完下一次问答立即生效；后三个（model/device/
    use_fp16）因模型进程内缓存为单例，改完需重启服务生效。"""
    return _merge(_DEFAULT_RERANKER, _load_raw().get("reranker"))


def graph_evidence_params() -> dict:
    """心智地图证据片段参数：{max_dates, fragments_per_date, window_expand, include_summary}。
    命中锚点后沿图收集的证据日期，用定向片段+摘要代替整份逐字稿。纯查询期后处理，改完立即生效。"""
    return _merge(_DEFAULT_GRAPH_EVIDENCE, _load_raw().get("graph_evidence"))


def load_for_ui() -> dict:
    """给设置 UI 用：返回六组当前生效值。"""
    return {
        "retrieval": retrieval_params(),
        "chunking": chunking_params(),
        "embedding": embedding_params(),
        "fts": fts_params(),
        "reranker": reranker_params(),
        "graph_evidence": graph_evidence_params(),
    }


def save(retrieval: dict, chunking: dict, embedding: dict, fts: dict, reranker: dict, graph_evidence: dict) -> None:
    """写回六组设置。"""
    data = _load_raw()
    data["retrieval"] = retrieval
    data["chunking"] = chunking
    data["embedding"] = embedding
    data["fts"] = fts
    data["reranker"] = reranker
    data["graph_evidence"] = graph_evidence
    INDEX_SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    INDEX_SETTINGS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def reset() -> None:
    """恢复索引默认参数（直接删掉设置文件即可，全部回退到 config.py 常量）。"""
    if INDEX_SETTINGS_PATH.exists():
        INDEX_SETTINGS_PATH.unlink()
