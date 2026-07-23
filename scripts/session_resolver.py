"""把问题里的相对 / 序数会话引用解析成具体会话，并生成「会话清单」常驻上下文。

背景（见 scripts/ask.py 的检索链路）：原有 answer() 只有两条拿原文的路：① 绝对日期
（extract_mentioned_dates，只认「2026年7月4日」这种写死年月日的）；② 语义检索。而
「上一次的咨询记录」「最近3次对话」这类**关于次序/时间的元指令**没有绝对日期、也没有
可匹配的内容语义，两条路都落空——所以 AI「索取不到」。这里补上第三条：把这些相对引用
确定性地解析成具体会话，再复用 answer() 已有的「整份塞进上下文」通路。

两套数据源，刻意分开（同 config.py 里 LONG_TERM_MEMORY / CHAT_MEMORY 的区分）：
  - 真实咨询逐字稿：private.nosync/data/raw/*.txt，文件名前 14 位时间戳天然按时间有序。
  - AI 聊天会话：private.nosync/data/chat_sessions/*.json（chat_store），按 updated_at。

关键：本模块只读**源头目录**（raw/ 和 chat_sessions/），不读 LanceDB 索引。索引是 raw/
的下游产物，新逐字稿入库不影响这里的次序判断——新文件时间戳更晚 → 排到末尾 → 「上一次」
自动指向它，无论 ingest 跑没跑。每次都实时 glob，不做模块级缓存，避免新增会话后排序过期。

支持 workspace：所有函数支持 workspace_id 参数。
"""
import json
import re
from typing import Optional

from config import SUMMARIES_DIR
from scripts.chat_store import list_sessions, load_session
from scripts.parse import iter_raw_files, parse_filename_date

# 整份塞入上下文的会话数上限——整份逐字稿很大，防止「最近10次咨询」把上下文撑爆。
# 超过就只取最近 MAX 份，并在上下文里提示「其余请依据长期记忆/对话记忆概述」。
MAX_FULL_SESSIONS = 3

# 数据源判定关键词（简繁通吃）。两者都出现时（如「读取上一次咨询记录然后继续对话」）优先按
# 「咨询」——因为那种句子里「对话」多半是「继续聊」的动词，主语「咨询记录」才是要调取的对象。
_CHAT_WORDS = re.compile(r"对话|對話|聊天|我们(?:之前)?聊|我們(?:之前)?聊|跟你聊|和你聊|上次聊")
_THERAPY_WORDS = re.compile(r"咨询|咨詢|諮詢|咨商|諮商|逐字稿|咨询记录|咨詢紀錄|咨詢記錄|海特|真实咨询|真實咨詢")

# 中文数字（含「两」）→ int，供「最近三次 / 第二次」这类解析用。
_CN_NUM = {
    "两": 2, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
    "十": 10, "十一": 11, "十二": 12, "十三": 13, "十四": 14, "十五": 15,
}
_NUM_OR_CN = r"(?:[两一二三四五六七八九十]{1,3}|\d{1,2})"

# 相对/序数引用（量词简繁通吃）。检查顺序很重要：先 nth（第N次），再 recent_n（最近N次），
# 最后单次指代。
_NTH = re.compile(rf"第({_NUM_OR_CN})\s*(?:次|场|場|回)")
_RECENT_N = re.compile(rf"(?:最近|最后|最後|前|近)({_NUM_OR_CN})\s*(?:次|场|場|回|份|个|個)")
# 单次指代：「上上次」必须先于「上次」判断（否则会被「上次」子串抢先命中）。
_PREV_PREV = re.compile(r"上上(?:一)?次")
_LAST_ONE = re.compile(r"上一次|上次|最近一?次|最新一?次|上回|最近的?那?次")


def _to_int(s: str) -> int | None:
    if s.isdigit():
        return int(s)
    return _CN_NUM.get(s)


def therapy_dates_ordered(workspace_id: Optional[str] = None) -> list[tuple[str, object]]:
    """[(YYYY-MM-DD, Path), ...] 按时间从早到晚（文件名 14 位前缀天然有序）。实时读盘（workspace 感知）。"""
    out = []
    for f in iter_raw_files(workspace_id):
        try:
            date, _ = parse_filename_date(f.name)
        except ValueError:
            continue
        out.append((date, f))
    return out


def _detect_intent(question: str):
    """返回 ('nth', k) | ('recent_n', k) | ('last_offset', k) | None。"""
    if m := _NTH.search(question):
        k = _to_int(m.group(1))
        return ("nth", k) if k else None
    if m := _RECENT_N.search(question):
        k = _to_int(m.group(1))
        return ("recent_n", k) if k else None
    if _PREV_PREV.search(question):
        return ("last_offset", 2)
    if _LAST_ONE.search(question):
        return ("last_offset", 1)
    return None


def _apply_intent(ordered: list, intent) -> list:
    """ordered 是「早→晚」；返回选中的元素（保持早→晚顺序）。"""
    kind, k = intent
    n = len(ordered)
    if kind == "nth":  # 正数第 k 次
        return [ordered[k - 1]] if 1 <= k <= n else []
    if kind == "last_offset":  # 倒数第 k 次（上一次=1，上上次=2），单取一份
        return [ordered[-k]] if 1 <= k <= n else []
    if kind == "recent_n":  # 最近 k 次，取末尾 k 份
        return ordered[-k:] if k > 0 else []
    return []


def resolve(question: str, workspace_id: Optional[str] = None) -> dict:
    """把问题里的相对/序数会话引用解析成具体会话（workspace 感知）。

    返回 {"therapy_dates": [YYYY-MM-DD, ...], "chat_session_ids": [id, ...],
          "overflow": bool}。没有相对引用时三者均为空/False，交回原有检索流程。
    """
    res = {"therapy_dates": [], "chat_session_ids": [], "overflow": False}
    intent = _detect_intent(question)
    if intent is None:
        return res

    wants_chat = bool(_CHAT_WORDS.search(question))
    wants_therapy = bool(_THERAPY_WORDS.search(question))
    kind = "chat" if (wants_chat and not wants_therapy) else "therapy"

    if kind == "therapy":
        ordered = therapy_dates_ordered(workspace_id)  # 早→晚
        picked = _apply_intent(ordered, intent)
        if len(picked) > MAX_FULL_SESSIONS:
            res["overflow"] = True
            picked = picked[-MAX_FULL_SESSIONS:]
        res["therapy_dates"] = [date for date, _ in picked]
    else:
        ordered = list(reversed(list_sessions(workspace_id)))  # list_sessions 是近→远，反转成早→晚对齐咨询
        picked = _apply_intent(ordered, intent)
        if len(picked) > MAX_FULL_SESSIONS:
            res["overflow"] = True
            picked = picked[-MAX_FULL_SESSIONS:]
        res["chat_session_ids"] = [s["id"] for s in picked]
    return res


def _load_topics(workspace_id: Optional[str] = None) -> dict[str, list[str]]:
    """source_file -> topics 列表（来自 summaries/*.json），用于给咨询清单加一句话标签（workspace 感知）。"""
    out: dict[str, list[str]] = {}
    summaries_dir = SUMMARIES_DIR(workspace_id)
    if not summaries_dir.exists():
        return out
    for jf in summaries_dir.glob("*.json"):
        try:
            d = json.loads(jf.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if d.get("source_file") and d.get("topics"):
            out[d["source_file"]] = d["topics"]
    return out


def therapy_manifest(workspace_id: Optional[str] = None) -> str:
    """全部真实咨询记录的极简清单（序号 + 日期 + 主题）。变动低频，可随 static_content 进缓存（workspace 感知）。"""
    entries = therapy_dates_ordered(workspace_id)
    if not entries:
        return "（暂无真实咨询记录）"
    topics_by_file = _load_topics(workspace_id)
    lines = []
    for i, (date, f) in enumerate(entries, 1):
        topics = topics_by_file.get(f.name)
        tag = f"：{'、'.join(topics)}" if topics else ""
        lines.append(f"{i}. {date}{tag}")
    return f"共 {len(entries)} 次真实咨询，按时间从早到晚（第 {len(entries)} 次为最近一次）：\n" + "\n".join(lines)


def chat_manifest(limit: int = 15, workspace_id: Optional[str] = None) -> str:
    """最近若干次 AI 对话的清单（日期 + 标题）。变动较频，放每轮动态内容、不进缓存（workspace 感知）。"""
    sessions = list_sessions(workspace_id)  # 近→远
    if not sessions:
        return "（暂无 AI 对话历史）"
    lines = []
    for i, s in enumerate(sessions[:limit], 1):
        lines.append(f"{i}. {s.get('updated_at', '')[:10]}「{s['title']}」")
    more = f"\n（仅列出最近 {limit} 次，共 {len(sessions)} 次）" if len(sessions) > limit else ""
    return "按时间从近到远：\n" + "\n".join(lines) + more


def render_chat_sessions(session_ids: list[str], workspace_id: Optional[str] = None) -> str:
    """把指定的 AI 聊天会话渲染成可读逐轮文本，供「读取最近N次对话」时整份塞进上下文（workspace 感知）。"""
    parts = []
    for sid in session_ids:
        s = load_session(sid, workspace_id)
        body = "\n".join(
            f"{'我' if m['role'] == 'user' else 'AI'}：{m['content']}" for m in s.get("messages", [])
        )
        parts.append(f"[对话「{s.get('title', '新对话')}」｜{s.get('updated_at', '')[:10]}]\n{body}")
    return "\n\n".join(parts)
