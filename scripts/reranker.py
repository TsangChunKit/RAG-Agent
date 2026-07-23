"""bge-reranker-v2-m3 cross-encoder 精排：hybrid（dense + FTS + RRF）取候选后，用它对
(query, passage) 逐对打分，取分数最高的若干条作为最终结果，再交给 ask.py 做父块扩展。

实现说明：config 里配的模型就是 BAAI/bge-reranker-v2-m3，但**没有用 FlagEmbedding.FlagReranker**
——本机 transformers 5.x 与 FlagEmbedding 1.4.0 的 reranker 不兼容（它内部调用已被移除的
tokenizer.prepare_for_model()，会抛 AttributeError）。改用 sentence-transformers 的 CrossEncoder
加载同一个模型，在 transformers 5 上稳定可用、行为等价（cross-encoder 对 (query, passage) 打分）。
分数用 sigmoid 归一化到 0–1，方便后续使用/阈值。

和 embedder.py 一样是本地单例（跑 mps，可选 fp16），不出网。任何一步失败都会 fallback 回原本的
hybrid 排序，保证检索不因为 reranker 而中断（见 rerank_candidates 的 except 分支）。
"""
from __future__ import annotations

import pandas as pd
import torch
from sentence_transformers import CrossEncoder

from scripts import index_settings

_reranker: CrossEncoder | None = None


def _get_reranker() -> CrossEncoder:
    global _reranker
    if _reranker is None:
        # 模型加载时读一次「⚙️ 索引设置」里的 reranker 参数（可在 UI 改）。和 embedder 一样是进程内
        # 单例，所以 model/device/use_fp16 改动要重启服务、下次加载才生效——这点在 UI 里有明确提示。
        # 开关 / rerank_top_k / final_top_k 是查询期参数，不经过这里，改完立即生效。
        p = index_settings.reranker_params()
        model_kwargs = {"torch_dtype": torch.float16} if p["use_fp16"] else {}
        _reranker = CrossEncoder(p["model"], device=p["device"], model_kwargs=model_kwargs)
    return _reranker


def _passage(row: pd.Series) -> str:
    """passage 优先用带上下文前缀的 text 列；缺失时回退 raw_text。"""
    return str(row.get("text") or row.get("raw_text") or "")


def rerank_candidates(query: str, hits: pd.DataFrame, top_k: int) -> pd.DataFrame:
    """对 hybrid 候选做 cross-encoder 精排，返回按 rerank 分数降序、截断到 top_k 的 DataFrame。
    列结构和输入一致（额外多一列 rerank_score，已 sigmoid 到 0–1），失败时回退为原顺序的前 top_k 条。"""
    if hits is None or len(hits) == 0:
        return hits

    pairs = [[query, _passage(row)] for _, row in hits.iterrows()]
    try:
        raw = _get_reranker().predict(pairs, convert_to_numpy=True)  # cross-encoder 原始 logits
        scores = torch.sigmoid(torch.as_tensor(raw)).tolist()        # 归一化到 0–1
        if not isinstance(scores, list):
            scores = [scores]
        if len(scores) != len(hits):
            raise ValueError(f"分数数量 {len(scores)} 与候选数 {len(hits)} 不一致")
    except Exception as e:  # noqa: BLE001
        print(f"[reranker] 精排失败，回退到 hybrid 排序：{e}", flush=True)
        return hits.head(top_k).reset_index(drop=True)

    ranked = hits.copy()
    ranked["rerank_score"] = scores
    return ranked.sort_values("rerank_score", ascending=False).head(top_k).reset_index(drop=True)
