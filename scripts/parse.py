"""解析文档 txt：从文件名取日期，正则解析发言人/时间戳/文本。

真实数据里发现的边界情况（53 份实测，非纯假设）：
  1. 标准格式：`发言人(HH:MM:SS): 文本`（绝大多数文件、绝大多数行）。
  2. 少数文件是"时间轴摘要"格式：`MM:SS 第三人称叙述文本`，完全没有发言人标签
     （例：20260329140106-...-时间轴文本-1.txt）。这类行归为伪发言人 "摘要"。
  3. 个别文件里嵌入了非对话内容（如分享的视频文本），前面有形如 "视频内容:" 的短标签行，
     后续若干段落属于该标签而非说话人。这类短标签行（以冒号结尾、长度较短）被视为新的
     伪发言人小节；其后未匹配的行追加为该小节的延续文本。
  4. 文件首行有时是标题（如"2026年6月27日 记录"），在还没有任何发言人之前出现，直接跳过。
  5. 个别 AI 生成的免责声明（如"（注：文档部分内容可能由 AI 生成）"）直接跳过，不计入内容。

不硬编码发言人名字——发言人完全由正则捕获得到。
"""
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from config import FILENAME_DATETIME_RE, RAW_DIR, TRANSCRIPT_LINE_RE

TRANSCRIPT_RE = re.compile(TRANSCRIPT_LINE_RE)
TIMELINE_RE = re.compile(r"^(\d{1,2}:\d{2})\s+(\S.*)$")
FNAME_RE = re.compile(FILENAME_DATETIME_RE)
LABEL_LINE_RE = re.compile(r"^[^\s:：]{1,12}[:：]\s*$")  # 如 "视频内容:"
SKIP_LINE_RE = re.compile(r"^[（(]?注[:：]")  # 如 "（注：文档部分内容可能由 AI 生成）"


@dataclass
class Utterance:
    speaker: str
    timestamp: str  # HH:MM:SS，伪发言人小节沿用上一条真实时间戳
    text: str
    line_no: int


@dataclass
class ParsedSession:
    source_file: str
    session_date: str  # YYYY-MM-DD，来自文件名
    file_datetime: str  # 文件名原始 14 位数字（生成时间，仅记录）
    utterances: list[Utterance] = field(default_factory=list)


def parse_filename_date(filename: str) -> tuple[str, str]:
    """文件名前 14 位数字：前 8 位 = YYYYMMDD 咨询日期；返回 (YYYY-MM-DD, 原始14位)。"""
    m = FNAME_RE.match(filename)
    if not m:
        raise ValueError(f"文件名不含 14 位日期前缀，无法确定咨询日期: {filename}")
    raw = m.group(1)
    session_date = f"{raw[0:4]}-{raw[4:6]}-{raw[6:8]}"
    return session_date, raw


def parse_transcript(path: Path) -> ParsedSession:
    session_date, file_dt = parse_filename_date(path.name)
    session = ParsedSession(source_file=path.name, session_date=session_date, file_datetime=file_dt)

    raw_text = path.read_text(encoding="utf-8", errors="replace")
    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]

    for i, line in enumerate(lines):
        m = TRANSCRIPT_RE.match(line)
        if m:
            speaker, ts, body = m.group(1).strip(), m.group(2), m.group(3).strip()
            session.utterances.append(Utterance(speaker, ts, body, i))
            continue

        if SKIP_LINE_RE.match(line):
            continue

        m_timeline = TIMELINE_RE.match(line)
        if m_timeline:
            mmss, body = m_timeline.group(1), m_timeline.group(2)
            ts = mmss if mmss.count(":") == 2 else f"00:{mmss.zfill(5)}"
            session.utterances.append(Utterance("摘要", ts, body, i))
            continue

        if not session.utterances:
            # 尚无任何发言人，且两种已知格式都不匹配：多半是标题行，跳过
            continue

        if LABEL_LINE_RE.match(line):
            prev_ts = session.utterances[-1].timestamp
            label = line.rstrip(":：").strip()
            session.utterances.append(Utterance(label, prev_ts, "", i))
            continue

        # 既非新的发言/摘要/标签行，视为上一条的延续段落（多行发言或嵌入的长段文本）
        prev = session.utterances[-1]
        prev.text = f"{prev.text}\n{line}" if prev.text else line

    return session


def iter_raw_files(workspace_id: Optional[str] = None):
    """迭代 raw 文件（workspace 感知）。"""
    return sorted(RAW_DIR(workspace_id).glob("*.txt"))


def render_full_text(session: ParsedSession) -> str:
    """把一份 session 渲染成完整逐字稿文本（speaker(ts): text 逐行），供摘要/按日期问答等场景复用。"""
    return "\n".join(f"{u.speaker}({u.timestamp}): {u.text}" for u in session.utterances)


def find_files_for_date(date_str: str, workspace_id: Optional[str] = None) -> list[Path]:
    """找出文件名日期等于 date_str（YYYY-MM-DD）的原始文件（workspace 感知）。"""
    matches = []
    for f in iter_raw_files(workspace_id):
        session_date, _ = parse_filename_date(f.name)
        if session_date == date_str:
            matches.append(f)
    return matches


if __name__ == "__main__":
    import sys

    files = iter_raw_files()
    if len(sys.argv) > 1:
        files = [Path(a) for a in sys.argv[1:]]

    for f in files:
        session = parse_transcript(f)
        print(f"== {session.source_file} | 日期 {session.session_date} | {len(session.utterances)} 条发言 ==")
        for u in session.utterances[:3]:
            preview = u.text[:40].replace("\n", " ")
            print(f"  [{u.timestamp}] {u.speaker}: {preview}")
