"""向量化 + 入 LanceDB：读取 chunk.py 产出的 chunks.jsonl，用 BGE-M3 算稠密向量，
写入 LanceDB 表，并在 text 列建 FTS 索引（关键词侧，jieba/default 中文分词，见 config.py 里的说明）。
规模小时暴力搜索即可，不建 ANN 向量索引（数据量大了再加 vector 列的 create_index()）。
"""
import json
from typing import Optional

import lancedb
from lancedb.index import FTS
from tqdm import tqdm

from config import (
    DB_DIR,
    LANCEDB_TABLE_NAME,
)
from scripts import index_settings
from scripts.chunk import CHUNKS_JSONL_PATH
from scripts.embedder import embed


def load_chunks(path=None, workspace_id: Optional[str] = None) -> list[dict]:
    """加载 chunks（workspace 感知）。"""
    if path is None:
        path = CHUNKS_JSONL_PATH(workspace_id)
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f]


def build_rows(chunks: list[dict]) -> list[dict]:
    embed_batch_size = index_settings.embedding_params()["batch_size"]
    rows: list[dict] = []
    for i in tqdm(range(0, len(chunks), embed_batch_size), desc="embedding"):
        batch = chunks[i : i + embed_batch_size]
        out = embed([c["text"] for c in batch])
        dense = out["dense_vecs"]
        for c, vec in zip(batch, dense):
            rows.append(
                {
                    "id": c["id"],
                    "text": c["text"],
                    "raw_text": c["raw_text"],
                    "vector": vec.tolist(),
                    "session_date": c["session_date"],
                    "speaker": c["speakers"],
                    "source_file": c["source_file"],
                    "chunk_index": c["chunk_index"],
                    "start_ts": c["start_ts"],
                    "end_ts": c["end_ts"],
                    "prev_chunk_id": c["prev_chunk_id"] or "",
                    "next_chunk_id": c["next_chunk_id"] or "",
                }
            )
    return rows


def ingest(chunks: Optional[list[dict]] = None, mode: str = "overwrite", workspace_id: Optional[str] = None):
    """向量化并写入 LanceDB（workspace 感知）。

    Args:
        chunks: chunk 列表（None = 从 chunks.jsonl 加载）
        mode: 'overwrite' 全量重建；'append' 增量追加
        workspace_id: workspace ID（None = 当前 workspace）
    """
    chunks = chunks if chunks is not None else load_chunks(workspace_id=workspace_id)
    rows = build_rows(chunks)

    db = lancedb.connect(str(DB_DIR(workspace_id)))
    if mode == "append" and LANCEDB_TABLE_NAME in db.table_names():
        table = db.open_table(LANCEDB_TABLE_NAME)
        table.add(rows)
    else:
        table = db.create_table(LANCEDB_TABLE_NAME, data=rows, mode="overwrite")

    fts = index_settings.fts_params()
    table.create_index(
        "text",
        config=FTS(
            base_tokenizer=fts["base_tokenizer"],
            ngram_min_length=fts["ngram_min"],
            ngram_max_length=fts["ngram_max"],
            remove_stop_words=False,
            stem=False,
            ascii_folding=False,
        ),
        replace=True,
    )
    return table


if __name__ == "__main__":
    from scripts.index_records import append_change_record

    table = ingest()
    append_change_record("full_rebuild", "(全部)", n_chunks=table.count_rows(), note="命令行 python -m scripts.ingest")
    print(f"已写入 LanceDB 表 '{LANCEDB_TABLE_NAME}'，共 {table.count_rows()} 行")
