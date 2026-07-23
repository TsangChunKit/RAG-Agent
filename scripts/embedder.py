"""BGE-M3 加载与编码：输出稠密（语义）向量。
from typing import Optional
本地运行，不出网。use_fp16=True 已验证速度快、质量损失极小；切勿用 Q4 权重。
注：hybrid 检索的关键词侧走 LanceDB FTS，不用 BGE-M3 的稀疏向量，所以这里 return_sparse=False，
省掉多余的稀疏计算（ingest 更快、更省内存）。
"""
from FlagEmbedding import BGEM3FlagModel

from scripts import index_settings

_model: Optional[BGEM3FlagModel] = None


def _get_model() -> BGEM3FlagModel:
    global _model
    if _model is None:
        # 模型加载时读一次「⚙️ 索引设置」里的 embedding 参数（可在 UI 改）。模型是进程内单例，
        # 所以 model/device/batch 改动要重启服务、下次加载才生效——这点在 UI 里有明确提示。
        p = index_settings.embedding_params()
        _model = BGEM3FlagModel(
            p["model"],
            use_fp16=p["use_fp16"],
            devices=p["device"],
            batch_size=p["batch_size"],
        )
    return _model


def embed(texts: list[str]) -> dict:
    """返回 dict，dense_vecs: np.ndarray (N, 1024)。稀疏向量已关闭（见模块 docstring）。"""
    return _get_model().encode(
        texts,
        return_dense=True,
        return_sparse=False,
        return_colbert_vecs=False,
    )


def embed_one(text: str):
    return embed([text])["dense_vecs"][0]


if __name__ == "__main__":
    out = embed(["我想聊聊换工作的恐惧", "亲密关系里的小摩擦"])
    print("dense shape:", out["dense_vecs"].shape)
