"""上下文化分块：把逐字稿切成 300–500 字的块，块间重叠 50–80 字，
并为每块加轻量上下文前缀（日期/发言人/时间段），记录 prev/next chunk id 供父块扩展使用。

分块单元是"发言"（dialogue turn），而不是任意字符位置——这样切分边界总落在语义完整的
发言之间，不会把一句话切成两半。只有单条发言本身就超长（如整段分享视频的文本）时，
才对其内部按句子边界（。！？\\n）做二次递归切分。

注意：这一步不调用任何 LLM/网络（M1 要求"先跑通"）。这里的上下文前缀只包含结构化元数据
（日期、发言人、时间段），不含语义主题总结——语义主题来自 M4 的结构化摘要，届时
ingest.py 可选择性地把摘要里的 topics 也拼进前缀，见 ingest.py 里的说明。
"""
import json
import re
from dataclasses import asdict, dataclass

from config import PROCESSED_DIR
from scripts import index_settings
from scripts.parse import ParsedSession, Utterance, iter_raw_files, parse_transcript

SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？\n])")

CHUNKS_JSONL_PATH = PROCESSED_DIR / "chunks.jsonl"


@dataclass
class Chunk:
    id: str
    session_date: str
    source_file: str
    chunk_index: int
    speakers: str  # 逗号分隔的去重发言人
    start_ts: str
    end_ts: str
    raw_text: str  # 原始拼接文本（不含上下文前缀）
    text: str  # raw_text 前加上下文化前缀，供 embedding + FTS 使用
    prev_chunk_id: str | None = None
    next_chunk_id: str | None = None


def _render_unit(u: Utterance) -> str:
    return f"{u.speaker}({u.timestamp}): {u.text}"


def _split_long_utterance(u: Utterance, max_len: int) -> list[str]:
    """单条发言超长时按句子边界递归切分，每片仍带说话人/时间戳前缀。"""
    sentences = [s for s in SENTENCE_SPLIT_RE.split(u.text) if s.strip()]
    pieces: list[str] = []
    buf = ""
    for s in sentences:
        if buf and len(buf) + len(s) > max_len:
            pieces.append(buf)
            buf = s
        else:
            buf += s
    if buf:
        pieces.append(buf)
    if not pieces:
        pieces = [u.text]
    return [f"{u.speaker}({u.timestamp}): {p}" for p in pieces]


def _build_units(session: ParsedSession) -> list[tuple[str, str, str]]:
    """把每条发言渲染成 (渲染文本, speaker, timestamp) 最小单元；超长发言预先拆成多个单元。"""
    chunk_size = index_settings.chunking_params()["chunk_size"]
    units: list[tuple[str, str, str]] = []
    for u in session.utterances:
        line = _render_unit(u)
        if len(line) <= chunk_size * 1.5:
            units.append((line, u.speaker, u.timestamp))
        else:
            for piece in _split_long_utterance(u, chunk_size):
                units.append((piece, u.speaker, u.timestamp))
    return units


def contextual_prefix(session: ParsedSession, speakers: list[str], start_ts: str, end_ts: str) -> str:
    speaker_str = "、".join(speakers)
    return f"[{session.session_date} 咨询｜发言人：{speaker_str}｜时间段：{start_ts}–{end_ts}]"


def chunk_session(session: ParsedSession) -> list[Chunk]:
    # 分块大小/重叠取「⚙️ 索引设置」当前值（可在 UI 改；只影响之后新入库的记录）。
    cfg = index_settings.chunking_params()
    chunk_size = cfg["chunk_size"]
    chunk_overlap = cfg["chunk_overlap"]

    units = _build_units(session)
    if not units:
        return []

    chunks: list[Chunk] = []
    n = len(units)
    i = 0
    chunk_idx = 0

    while i < n:
        cur_lines: list[str] = []
        cur_speakers: list[str] = []
        cur_len = 0
        start_ts = units[i][2]
        end_ts = start_ts
        j = i
        while j < n:
            line, spk, ts = units[j]
            added_len = len(line) + 1
            if cur_lines and cur_len + added_len > chunk_size:
                break
            cur_lines.append(line)
            if spk not in cur_speakers:
                cur_speakers.append(spk)
            cur_len += added_len
            end_ts = ts
            j += 1

        raw_text = "\n".join(cur_lines)
        prefix = contextual_prefix(session, cur_speakers, start_ts, end_ts)
        chunk_id = f"{session.source_file}::chunk{chunk_idx:04d}"
        chunks.append(
            Chunk(
                id=chunk_id,
                session_date=session.session_date,
                source_file=session.source_file,
                chunk_index=chunk_idx,
                speakers=",".join(cur_speakers),
                start_ts=start_ts,
                end_ts=end_ts,
                raw_text=raw_text,
                text=f"{prefix}\n{raw_text}",
            )
        )
        chunk_idx += 1

        if j >= n:
            break

        # 从 j 往回数，凑够 chunk_overlap 字符作为下一块的起点，实现块间重叠
        overlap_len = 0
        k = j
        while k > i and overlap_len < chunk_overlap:
            k -= 1
            overlap_len += len(units[k][0]) + 1
        i = max(k, i + 1)  # 保证下一轮起点前进，避免死循环

    for idx, c in enumerate(chunks):
        c.prev_chunk_id = chunks[idx - 1].id if idx > 0 else None
        c.next_chunk_id = chunks[idx + 1].id if idx < len(chunks) - 1 else None

    return chunks


def chunk_all(files=None) -> list[Chunk]:
    files = files or iter_raw_files()
    all_chunks: list[Chunk] = []
    for f in files:
        session = parse_transcript(f)
        all_chunks.extend(chunk_session(session))
    return all_chunks


def write_chunks_jsonl(chunks: list[Chunk], path=CHUNKS_JSONL_PATH):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for c in chunks:
            f.write(json.dumps(asdict(c), ensure_ascii=False) + "\n")


if __name__ == "__main__":
    import sys

    files = [__import__("pathlib").Path(a) for a in sys.argv[1:]] if len(sys.argv) > 1 else None
    chunks = chunk_all(files)
    print(f"共生成 {len(chunks)} 个 chunk（来自 {len(files) if files else len(iter_raw_files())} 份文件）")

    lens = [len(c.raw_text) for c in chunks]
    if lens:
        print(f"chunk 长度：min={min(lens)} max={max(lens)} avg={sum(lens)/len(lens):.0f}")

    for c in chunks[:3]:
        print("----")
        print(c.text)

    if files is None:
        write_chunks_jsonl(chunks)
        print(f"已写入 {CHUNKS_JSONL_PATH}")
