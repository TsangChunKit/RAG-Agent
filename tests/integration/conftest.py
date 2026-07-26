"""集成测试专用 fixtures。

集成测试和单元测试的区别在这里：单元测试可以把邻居全 mock 掉只验一个函数，集成测试要真的把
parse → chunk → ingest → retrieve 串起来跑。串起来跑就有两个东西不能用真的：

1. **BGE-M3 向量**：真模型要下载 2GB + 每次加载几秒，而且跑在 mps 上。
2. **LLM**：出网、要钱、不确定。

但也不能随便 mock 成随机值——`resolve_graph()` 靠余弦相似度 ≥ 0.80 决定两个节点要不要合并，
随机向量下"同名概念该合并"这种断言会随机通过/失败（flaky）。所以这里给的是**确定性**替身：
同一段文本永远得到同一个向量，不同文本近正交。
"""
import zlib
from unittest.mock import MagicMock

import numpy as np
import pytest

from config import EMBEDDING_DIM


def _vec_for(text: str) -> np.ndarray:
    """把文本映射成一个确定的单位向量。

    用 crc32(text) 当种子：同文本 → 同向量（余弦 1.0，必然 ≥ MERGE_SIM_THRESHOLD 0.80），
    不同文本 → 1024 维高斯向量，期望余弦 ≈ 0（|cos| 基本 < 0.1，必然 < 0.80）。
    于是"该合并的合并、不该合并的不合并"变成可断言的事实，而不是概率。
    """
    rng = np.random.default_rng(zlib.crc32(text.encode("utf-8")))
    v = rng.standard_normal(EMBEDDING_DIM)
    return (v / np.linalg.norm(v)).astype(np.float32)


@pytest.fixture
def deterministic_embed(monkeypatch):
    """替换 embedding，同文本同向量。

    必须同时打**源模块**和**已绑定的调用方**：`scripts/ingest.py` 是
    `from scripts.embedder import embed`，名字在 import 时就复制进 ingest 的 namespace 了，
    只 patch `scripts.embedder.embed` 对它毫无作用（这正是历史上"mock 了 embedder"的集成测试
    仍然去加载真模型的原因）。`ask.py` / `graph_utils.py` 里的 embed 是**函数内**局部导入，
    运行时才查 scripts.embedder，所以打源模块就够。

    Returns:
        {"embed": callable, "embed_one": callable, "vec_for": callable} —— vec_for 给测试自己
        算期望向量用。
    """
    def fake_embed(texts):
        return {"dense_vecs": np.stack([_vec_for(t) for t in texts])}

    def fake_embed_one(text):
        return _vec_for(text)

    monkeypatch.setattr("scripts.embedder.embed", fake_embed)
    monkeypatch.setattr("scripts.embedder.embed_one", fake_embed_one)
    # ingest.py / ask.py 的 module-level 绑定
    monkeypatch.setattr("scripts.ingest.embed", fake_embed)
    monkeypatch.setattr("scripts.ask.embed_one", fake_embed_one)
    return {"embed": fake_embed, "embed_one": fake_embed_one, "vec_for": _vec_for}


@pytest.fixture
def fake_resp():
    """构造一个长得像 LLM 响应的对象的工厂。

    真实的 ask_llm() 返回 google.genai / openai 的响应对象，调用方只用两个东西：
    `.text` 和 `.usage_metadata.*_token_count`。所以替身只需要长出这两处。
    usage_metadata 用 MagicMock 会让 `int(...)` 拿到 MagicMock 而不是数字，
    所以这里显式赋整数——ask.py 会拿它们做加法。

    Returns:
        make(text: str) -> MagicMock
    """
    def make(text: str = "Mocked LLM response"):
        resp = MagicMock()
        resp.text = text
        usage = MagicMock()
        usage.prompt_token_count = 100
        usage.candidates_token_count = 50
        usage.thoughts_token_count = 0
        usage.cached_content_token_count = 0
        usage.total_token_count = 150
        resp.usage_metadata = usage
        return resp

    return make


@pytest.fixture
def no_reranker(isolate_data_root):
    """关掉 cross-encoder 精排 + 相关性阈值。

    reranker 是真模型（bge-reranker-v2-m3，本地 mps），集成测试里不该加载它：慢，而且
    deterministic_embed 造出来的假向量在 reranker 眼里毫无意义，分数会低于 min_score 被过滤掉，
    于是"检索能召回"的断言变成在测 reranker 的心情。

    走的是正式配置通路（写 index_settings.json），而不是 patch 内部函数——这样测的就是
    "用户在 UI 里关掉 reranker 之后的真实行为"。
    """
    from scripts import index_settings

    index_settings.save(
        retrieval=index_settings.retrieval_params(),
        chunking=index_settings.chunking_params(),
        embedding=index_settings.embedding_params(),
        fts=index_settings.fts_params(),
        reranker={**index_settings.reranker_params(),
                  "use_reranker": False, "min_score": 0},
        graph_evidence=index_settings.graph_evidence_params(),
    )
    return index_settings.reranker_params()
