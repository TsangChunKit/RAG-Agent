"""通用书籍/文档入库适配器：把 PDF / DOCX / TXT 书籍转成现有逐字稿流水线能吃的
「合成逐字稿」，复用 parse/chunk/ingest_new 的全部逻辑，不改动它们一行。

背景：现有入库流水线（parse.py / chunk.py / ingest_new.py）是围绕"逐字稿"设计的——
文件名要求 14 位日期前缀（config.FILENAME_DATETIME_RE），每行要求
`发言人(HH:MM:SS): 文本` 格式（config.TRANSCRIPT_LINE_RE）。书籍类文档（如八字/紫微
古籍）既没有真实日期也没有对话结构，直接扔进 raw/ 会在 parse 阶段就 ValueError，
或被解析得面目全非。

适配思路（via negativa：新增一层薄适配，不碰任何已测试过的核心代码）：本模块只做
"提取 + 清洗 + 转换"，产出的仍是一份符合现有格式约定的 .txt，写进 workspace 的
raw/ 后就是普通逐字稿，复用 scripts.parse / scripts.chunk / scripts.ingest 原样
分块入库（跳过摘要/长期记忆——见下方说明）。出问题只需删掉对应 workspace，完全
不影响其他 workspace。

支持格式：
  - .pdf（需要文字层；扫描件会在提取阶段报错，请先用 OCR 转成有文字层的 PDF）
  - .docx
  - .txt

⚠️ **不复用 ingest_new.ingest_pending()**：它入库后无条件调用 summarize_session()
（scripts/summarize.py），而那个 prompt 是**心理咨询专用**的硬编码——system instruction
写死"你是一位心理咨询记录整理助手"，schema 要求 emotional_tone / psychological_themes
等字段。对书籍类内容（如八字/紫微古籍）跑这套 prompt 只会产出胡编的"情绪基调"，
而且是把整份提取文本（本项目古籍单份可达 20 万+字符）一次性塞进单次 LLM 调用，
纯属浪费。本模块因此只做 parse → chunk → 向量化入库这一段（复用 scripts.ingest /
scripts.chunk / scripts.parse，逻辑与 ingest_new_file() 的对应部分一致），跳过
摘要 / 长期记忆更新——这些书不是"咨询逐字稿"，不需要那套心理咨询语境的记忆机制。
"""
import json
import re
from dataclasses import asdict
from pathlib import Path
from typing import Dict, List, Optional

import pypdf
from docx import Document as DocxDocument

from config import RAW_DIR
from scripts.chunk import CHUNKS_JSONL_PATH, chunk_session
from scripts.index_records import append_change_record
from scripts.ingest import ingest
from scripts.parse import parse_transcript

# 合成逐字稿里统一用这个"发言人"名字 + 固定时间戳，配合 TRANSCRIPT_LINE_RE 的格式约定
# （书籍没有真实说话人/时间轴，workspace 的 chunk_prefix_template 也不应展示这两项）。
_BOOK_SPEAKER = "原文"
_BOOK_TS = "00:00:00"

# 书籍没有真实日期，用固定哨兵日期打头 + 6 位序号拼时间部分，满足
# FILENAME_DATETIME_RE（14 位数字前缀）且同批文件互不冲突、按 seq 排序稳定。
_SENTINEL_DATE = "19000101"

# 扫描件判定阈值：页均提取到的非空白字符数低于此值，视为"无文字层"。
# 实测有文字层的书页均 1000+ 字符，扫描件（如河洛理數）页均 0；留出充分余量。
_SCANNED_PAGE_CHAR_THRESHOLD = 20

# 网页抓取来源（如 ctext.org 浏览器"打印为 PDF" / 合并 txt）常见的页眉/页脚噪音：
# 抓取时间戳（常和当页运行标题黏在同一行）、纯页码、检索框、整段都是站点页脚的行。
# re.match 从行首匹配；多条用 | 并列。PDF 提取常把部分汉字变成康熙部首兼容字
# （支→⽀、文→⽂、子→⼦），相关分支同时接受两种字形。
_NOISE_LINE_RE = re.compile(
    r"^\d{1,2}/\d{1,2}/\d{2,4},\s*\d{1,2}:\d{2}\s*[AP]M"  # 3/25/26, 9:31 AM（后面不管跟了什么都丢）
    r"|^\d+$"                                              # 纯页码
    r"|^在\s*[\"“].*[\"”]\s*中檢索：?$"                       # 在 "序" 中檢索：
    r"|網站的設計與内容"                                    # 版权页脚长文
    r"|喜歡我們的網站"                                      # 单独成段的引导语
    r"|^請[支⽀]持我們的發展。?$"                            # 赞助提示
    r"|\(c\)版權"                                          # (c)版權2006-2026。
    r"|如果您想引用本網站"                                   # 引用说明
    r"|站的鏈接"                                           # 被切段的引用说明尾部
    r"|嚴禁使[用⽤]自動下載"                                 # 反爬声明
    r"|沪\s*ICP"                                           # ICP 备案
    r"|若有任何意見或建議"                                   # 反馈提示
    r"|來源網址"                                           # 合并 txt 篇首
    r"|={3,}.*篇.*={3,}"                                   # ========== 第 N 篇 ==========
    r"|\[查看正[文⽂]\]"                                    # wiki 工具栏
    r"|中國哲學書電[子⼦]化計劃"                              # 站点品牌
    r"|ctext\.org"                                         # 残留 URL / 域名（子串剥离后仍整段是噪音时）
)

# 黏在正文首尾/中间的噪音子串：不能整行丢，只能挖掉子串、保留正文。
# 不锚定 ^/$：reflow 后噪音可能出现在段落中间。顺序：先长/具体，再短/宽。
# URL 只用 ASCII 路径字符，避免 \S* 把后面中文正文一起吞掉；页码分数 \d+/\d+ 可选。
# 空格可选：部分 PDF 把拉丁字母拆成 "R N :  c t p : ..."。
_CTEXT_URL_RE = re.compile(
    r"https?://(?:www\.)?ctext\.org[A-Za-z0-9_./?=&%+\-]*(?:\s*\d+/\d+)?"
)
_GLUED_NOISE_RES = [
    _CTEXT_URL_RE,
    # URN + 可选「喜歡我們的網站」（含字母间被插入空格的变体）
    re.compile(
        r"U\s*R\s*N\s*:\s*c\s*t\s*p\s*:\s*w\s*s\s*[\d\s]*"
        r"(?:\s*喜歡我們的網站[？?]?)?"
    ),
    re.compile(r"喜歡我們的網站[？?]?"),
    re.compile(r"請[支⽀]持我們的發展。?"),
    re.compile(r"\(c\)版權\d{4}-\d{4}。?"),
    re.compile(r"網站的設計與内容[^。\n]*。?"),
    re.compile(r"如果您想引用本網站上的内容[^。\n]*。?"),
    re.compile(r"請注意：嚴禁使[用⽤]自動下載軟体[^。\n]*。?"),
    re.compile(r"站的鏈接："),
    re.compile(r"來源網址:\s*"),
    re.compile(r"={3,}\s*第\s*\d+\s*篇\s*={3,}"),
    re.compile(
        r"\[查看正[文⽂]\]\s*\[修改\]\s*\[查看歷史\]"
        r"中國哲學書電[子⼦]化計劃\s*"
        r"維基\s*維基\s*(?:->\s*[^《\n]+)*"
    ),
    # 工具栏/品牌被先一步剥掉后，可能只剩面包屑路径
    re.compile(r"維基\s*維基\s*(?:->\s*[^《\n]+)*"),
    re.compile(r"中國哲學書電[子⼦]化計劃\s*"),
    re.compile(r"沪\s*ICP\s*备[^\n。]*。?"),
    re.compile(r"若有任何意見或建議，請在此提出。?"),
]

# 兼容旧名字（测试 / 外部引用）
_GLUED_PREFIX_RE = _CTEXT_URL_RE
_GLUED_SUFFIX_RE = _GLUED_NOISE_RES[1]

# 段落重新流式聚合的目标长度（字符）。部分 PDF（尤其竖排古籍，如滴天髓）逐字符
# 换行，若直接按源文件的行切"发言"，每个"发言"只有 1 个字却要重复背一整条
# "原文(00:00:00): " 前缀（16 字符），整份文件会被前缀噪音淹没。见 _reflow_to_paragraphs。
_PARAGRAPH_TARGET_LEN = 200
_SENTENCE_END_CHARS = "。！？"

# 判定「逐字拆行」：非空行中单字符行占比达到此阈值时，视为 char-per-line 源
#（竖排 PDF / 部分页脚噪音的典型形态）。这种输入下不能按「整行」做去重/页码过滤，
# 否则会吃掉连续重复字（https 的 tt、中文叠字）和 URN 里的数字。
_CHAR_PER_LINE_RATIO = 0.8
_CHAR_PER_LINE_MIN_LINES = 20


def extract_pdf_text(path: Path) -> str:
    """用 pypdf 逐页提取文字层文本，页间用换行分隔。

    Raises:
        ValueError: 页均文字量过低（典型扫描件特征），需要先做 OCR 才能提取。
    """
    reader = pypdf.PdfReader(str(path))
    pages_text = [page.extract_text() or "" for page in reader.pages]
    n_pages = len(pages_text)
    total_chars = sum(len(t.strip()) for t in pages_text)

    if n_pages == 0 or total_chars / n_pages < _SCANNED_PAGE_CHAR_THRESHOLD:
        avg = total_chars / n_pages if n_pages else 0
        raise ValueError(
            f"{path.name}: 疑似扫描件（无文字层，页均约 {avg:.0f} 字符），"
            "需要先做 OCR 才能提取，暂时跳过"
        )
    return "\n".join(pages_text)


def extract_docx_text(path: Path) -> str:
    """用 python-docx 提取正文段落文本，段落间用换行分隔（跳过空段落）。"""
    doc = DocxDocument(str(path))
    paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
    return "\n".join(paragraphs)


def extract_txt_text(path: Path) -> str:
    """直接按 UTF-8 读取（容错替换非法字节，和 parse.py 读逐字稿的方式一致）。"""
    return path.read_text(encoding="utf-8", errors="replace")


_EXTRACTORS = {
    ".pdf": extract_pdf_text,
    ".docx": extract_docx_text,
    ".txt": extract_txt_text,
}


def extract_text(path: Path) -> str:
    """按文件后缀分发到对应提取器。

    Raises:
        ValueError: 不支持的文件类型；或（PDF）疑似扫描件无文字层。
    """
    extractor = _EXTRACTORS.get(path.suffix.lower())
    if extractor is None:
        raise ValueError(f"不支持的文件类型: {path.suffix}（{path.name}）")
    return extractor(path)


def _is_char_per_line_text(text: str) -> bool:
    """判断提取文本是否主要是「一行一字」（竖排 PDF / 拆散的页脚噪音）。

    样本太短时不做判定（返回 False），走常规按行清洗路径。
    """
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if len(lines) < _CHAR_PER_LINE_MIN_LINES:
        return False
    short = sum(1 for ln in lines if len(ln) <= 1)
    return (short / len(lines)) >= _CHAR_PER_LINE_RATIO


def clean_book_text(text: str) -> str:
    """去掉网页抓取常见的页眉/页脚噪音（独立成行的 + 黏在正文首尾的）+ 连续重复的
    运行页眉/页脚 + 空行。

    保留原有分段（一行一段落），供 convert_book_to_transcript() 把每段渲染成一条
    合成"发言"。

    若输入主要是逐字拆行：只去空行，不做整行去重/页码过滤——那些规则会误伤单字行
    （连续重复字、URN 数字）。噪音留给 reflow 之后的 _strip_glued_noise / 段落过滤。
    """
    if _is_char_per_line_text(text):
        # 保留空白行对应的单个空格（否则 "212352 1/23" 会黏成 "2123521/23"），
        # 其余非空行原样保留单字，交给 reflow 拼接。
        parts: List[str] = []
        for ln in text.splitlines():
            stripped = ln.strip()
            if stripped:
                parts.append(stripped)
            elif ln:
                parts.append(" ")
        return "\n".join(parts)

    cleaned: List[str] = []
    prev: Optional[str] = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        # 先挖掉黏在正文首尾的噪音子串（不是独立一行，不能整行丢），再判断剩余内容
        line = _strip_glued_noise(line)
        if not line:
            continue
        if _NOISE_LINE_RE.match(line):
            continue
        if line == prev:  # 连续重复（如每页都印一次的书名/章节名）
            continue
        cleaned.append(line)
        prev = line
    return "\n".join(cleaned)


def _reflow_to_paragraphs(text: str, target_len: int = _PARAGRAPH_TARGET_LEN) -> List[str]:
    """把清洗后的文本重新按句子边界流式聚合成大小适中的段落。

    不能直接按源文件的换行切"发言"：换行方式完全由源格式/排版决定——docx 和网页
    抓取的 PDF 通常一行一段（可直接用），但竖排古籍 PDF 常被 pypdf 逐字符拆成一行。
    这里统一走一条流程：不管源文件断行方式如何，把行依次拼接进缓冲区，遇到句末
    标点（。！？）就收段，缓冲区过长（target_len）也强制收段——这样无论输入是
    "一行一段" 还是 "一行一字"，产出的段落大小都稳定、不会被重复的行前缀噪音淹没。

    Args:
        text: clean_book_text() 处理过的多行文本
        target_len: 单段目标长度上限（字符数）

    Returns:
        段落列表（去掉了空段落）
    """
    paragraphs: List[str] = []
    buf = ""
    for line in text.splitlines():
        buf += line
        if buf and (buf[-1] in _SENTENCE_END_CHARS or len(buf) >= target_len):
            paragraphs.append(buf)
            buf = ""
    if buf:
        paragraphs.append(buf)
    return paragraphs


def _strip_glued_noise(text: str) -> str:
    """挖掉黏在正文里的 ctext/wiki 噪音子串（保留正文）。

    在 clean_book_text() 的按行路径、以及 _reflow_to_paragraphs() 之后的段落路径
    都会调用：部分噪音本身是逐字符拆行的，按单行匹配抓不到；reflow 拼回连续子串
    后才能被这里的模式识别。模式列表见模块级 _GLUED_NOISE_RES。
    """
    for pat in _GLUED_NOISE_RES:
        text = pat.sub("", text)
    return text.strip()


def convert_book_to_transcript(
    path: Path,
    workspace_id: str,
    seq: int = 0,
    title: Optional[str] = None,
) -> Path:
    """把一本书转换成「合成逐字稿」，写进 workspace 的 raw/，返回写入的路径。

    产出格式满足 parse.py 的两条硬约束：
      1. 文件名以 14 位数字打头——用固定哨兵日期 19000101 + 6 位 seq 拼时间部分。
      2. 每行匹配 `发言人(HH:MM:SS): 文本`——统一"发言人"设为 "原文"、时间戳固定
         00:00:00（书籍没有时间轴）。

    Args:
        path: 源文件（.pdf / .docx / .txt）
        workspace_id: 目标 workspace
        seq: 同批转换里的序号，用于拼时间戳后 6 位、保证同批文件名互不冲突
        title: 显示用书名，默认取源文件去后缀的文件名

    Returns:
        写入的合成逐字稿路径（workspace 的 raw/ 下）

    Raises:
        ValueError: 提取后没有可用文本（内容全为空白）
    """
    title = title or path.stem
    cleaned = clean_book_text(extract_text(path))
    # 先 reflow 再二次清洗：部分噪音（如 ctext.org 页脚）在源文件里是逐字拆行的，
    # clean_book_text() 按单行匹配抓不到；只有 _reflow_to_paragraphs() 拼回连续子串后
    # 才能被 _strip_glued_noise / _NOISE_LINE_RE 识别。也可能 cleaned 非空但过滤后
    # paragraphs 变空，所以空文本检查放在段落过滤之后。
    paragraphs = _reflow_to_paragraphs(cleaned)
    paragraphs = [_strip_glued_noise(p) for p in paragraphs]
    paragraphs = [p for p in paragraphs if p and not _NOISE_LINE_RE.match(p)]
    if not paragraphs:
        raise ValueError(f"{path.name}: 提取后没有可用文本")

    body = "\n".join(f"{_BOOK_SPEAKER}({_BOOK_TS}): {p}" for p in paragraphs)

    out_name = f"{_SENTINEL_DATE}{seq:06d}_{title}.txt"
    out_path = RAW_DIR(workspace_id) / out_name
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(body, encoding="utf-8")
    return out_path


def _existing_chunk_source_files(workspace_id: Optional[str] = None) -> set:
    """已入库的 source_file 集合（与 ingest_new._existing_chunk_source_files 逻辑一致，
    这里独立实现一份而不是 import 私有函数，避免两个模块耦合到对方的实现细节）。"""
    chunks_path = CHUNKS_JSONL_PATH(workspace_id)
    if not chunks_path.exists():
        return set()
    with open(chunks_path, encoding="utf-8") as f:
        return {json.loads(line)["source_file"] for line in f if line.strip()}


def _ingest_chunks_only(path: Path, workspace_id: Optional[str] = None) -> None:
    """parse → chunk → 向量化入库（跳过摘要 / 长期记忆更新，见模块头部说明）。

    与 ingest_new.ingest_new_file() 的入库那一段等价（同样先检查是否已入库、
    先 ingest() 成功再写 chunks.jsonl，保证失败可重试、不会把失败的文件误记成
    已入库），但故意不调用 summarize_session() / update_memory()。已入库的文件
    直接跳过（幂等，重跑本函数不会产生重复 chunk）。

    Args:
        path: 合成逐字稿路径（workspace 的 raw/ 下）
        workspace_id: 目标 workspace
    """
    session = parse_transcript(path)
    if session.source_file in _existing_chunk_source_files(workspace_id):
        append_change_record(
            "skipped", session.source_file, session.session_date,
            note="已在向量库中，未重复入库", workspace_id=workspace_id,
        )
        return

    chunks = chunk_session(session, workspace_id=workspace_id)
    chunk_dicts = [asdict(c) for c in chunks]

    ingest(chunks=chunk_dicts, mode="append", workspace_id=workspace_id)

    chunks_path = CHUNKS_JSONL_PATH(workspace_id)
    chunks_path.parent.mkdir(parents=True, exist_ok=True)
    with open(chunks_path, "a", encoding="utf-8") as f:
        for c in chunk_dicts:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")

    append_change_record(
        "added", session.source_file, session.session_date,
        n_chunks=len(chunks), note="书籍/参考资料入库（跳过摘要——摘要 prompt 是心理咨询专用，不适用）",
        workspace_id=workspace_id,
    )


def ingest_book_folder(
    src_dir: Path,
    workspace_id: str,
    skip_files: Optional[List[str]] = None,
) -> Dict[str, list]:
    """把一个目录下所有支持格式的书籍批量转换 + 入库（供命令行/一次性任务调用）。

    每份文件独立转换、独立入库，一份失败不影响其余（与 ingest_pending() 的容错
    策略一致）。转换成功的文件再逐份走 _ingest_chunks_only()——parse→chunk→向量化，
    不生成摘要、不更新长期记忆（见模块头部说明）。

    Args:
        src_dir: 源目录（.pdf/.docx/.txt 混放即可，其余后缀原样跳过，不计入结果）
        workspace_id: 目标 workspace
        skip_files: 文件名黑名单（如已知的扫描件，先跳过等 OCR 好了再入库）

    Returns:
        {
            "converted": [文件名, ...],
            "convert_failed": [{"file", "error"}, ...],
            "ingested": [文件名, ...],
            "failed": [{"file", "error"}, ...],
        }
    """
    skip = set(skip_files or [])
    result: Dict[str, list] = {
        "converted": [], "convert_failed": [], "ingested": [], "failed": [],
    }

    files = sorted(
        p for p in src_dir.iterdir()
        if p.is_file() and p.suffix.lower() in _EXTRACTORS and p.name not in skip
    )
    converted_paths: Dict[str, Path] = {}
    for seq, p in enumerate(files):
        try:
            out_path = convert_book_to_transcript(p, workspace_id, seq=seq)
            result["converted"].append(p.name)
            converted_paths[p.name] = out_path
        except Exception as e:  # noqa: BLE001 — 一份坏文件不该拖垮整批
            result["convert_failed"].append({"file": p.name, "error": str(e)})

    for src_name, out_path in converted_paths.items():
        try:
            _ingest_chunks_only(out_path, workspace_id=workspace_id)
            result["ingested"].append(src_name)
        except Exception as e:  # noqa: BLE001 — 一份坏文件不该拖垮整批
            result["failed"].append({"file": src_name, "error": str(e)})

    return result


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 3:
        print("用法: python -m scripts.book_ingest <源目录> <workspace_id> [跳过文件名...]")
        sys.exit(1)

    src, ws = Path(sys.argv[1]), sys.argv[2]
    skip_list = sys.argv[3:] or None
    r = ingest_book_folder(src, ws, skip_files=skip_list)
    print(f"已转换: {r['converted']}")
    if r["convert_failed"]:
        print(f"转换失败: {r['convert_failed']}")
    print(f"已入库: {r['ingested']}")
    if r["failed"]:
        print(f"入库失败: {r['failed']}")
