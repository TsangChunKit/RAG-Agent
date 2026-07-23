"""汇总所有结构化摘要 → 滚动生成 LONG_TERM_MEMORY.md。保持精炼（目标 < 3k token），
每次问答都全量携带。头部的更新时间/咨询次数/日期范围由代码直接计算写入（不问 Gemini，
避免它编错数字/日期）；Gemini 只负责提炼四个正文板块。
"""
import datetime
import json

from config import LONG_TERM_MEMORY_PATH, SUMMARIES_DIR
from scripts.llm import ask_llm
from scripts.settings import dialogue_params, summary_max_tokens

SYSTEM_INSTRUCTION = """\
你是 Andy 长期心理咨询记录的整理助手。你会收到 Andy 所有历次咨询的结构化摘要（按时间排序），\
请提炼一份滚动更新的"长期记忆"档案。这份档案有两个用途：①作为心理咨询 AI 的长期记忆；\
②作为一份可移植的「Andy 个人心理画像」，供其他 AI 平台阅读后快速、深入地理解 Andy。因此它\
需要详实、结构清晰、能独立成篇。

全文称呼要求：一律用「Andy」指代来访者本人，不要使用"来访者""使用者""个案"等第三方标签\
（承接时可用"他"）。

篇幅与结构要求：整体目标 15000–20000 字，务必写足、不要因惜字而压缩。重心在「进展轨迹」\
板块，它应占全文一半以上。只输出以下四个 Markdown 二级标题（##）板块，板块内可自由使用三级\
标题（###）、有序/无序列表、加粗来组织层次，但不要输出这四个板块之外的开场白或结语。

## 核心议题（反复出现）
列出 4-7 个贯穿咨询的核心议题。每个议题用一个三级标题（### 议题名），下面 3-5 句展开：这个\
议题的表现、根源、随时间如何演变（从什么状态走向什么状态），并尽量点出与之相关的具体情境。

## 反复出现的心理模式
列出 4-7 条反复出现的心理/认知/防御模式。每条用「**模式名**」加粗开头，后跟 2-4 句说明：\
它通常在什么情境下被触发、有什么外在表现、对 Andy 造成什么影响。

## 进展轨迹（时间线）
这是最重要、篇幅最大的板块，要写得详实、有血肉、有连续的叙事感。采用两层结构：
- 外层用三级标题（###）划分为若干「阶段」（按主题/心境的自然分期，标注该阶段的起止月份与一\
  句话主旨）；
- 每个阶段内部，再按月份或关键日期列出更细的节点（覆盖尽量多的咨询，不要跳过大段时间，也不必\
  逐次记流水账——主题连贯的相邻几次可合并为一个节点）。每个节点 2-5 句，说清：①当时的触发事件\
  或生活处境；②Andy 的情绪/心理议题如何变化；③是否出现关键的领悟、决定或行为突破。
尽量在节点中引用摘要里真实出现的金句、标志性事件与具体细节，让轨迹具体可感、可追溯。

## 与咨询师的关系风格
用 4-8 句（可分点）概括咨询师的风格，以及 Andy 与咨询师互动方式的演变（从初期到近期如何变化）。

严格要求：只根据给定的摘要内容提炼，不要编造摘要中没有出现的信息；不要自己编造日期或咨询次数。"""


def load_summaries() -> list[dict]:
    files = sorted(SUMMARIES_DIR.glob("*.json"))
    summaries = [json.loads(f.read_text(encoding="utf-8")) for f in files]
    summaries.sort(key=lambda s: s["session_date"])
    return summaries


def _format_summaries(summaries: list[dict]) -> str:
    blocks = []
    for s in summaries:
        blocks.append(
            f"[{s['session_date']}]\n"
            f"主题：{'、'.join(s['topics'])}\n"
            f"情绪基调：{s['emotional_tone']}\n"
            f"事件：{'；'.join(s['key_events'])}\n"
            f"心理议题：{'；'.join(s['psychological_themes'])}\n"
            f"决定/行动：{'；'.join(s['decisions_or_actions'])}\n"
            f"金句：{'；'.join(s['quotes_worth_remembering'])}"
        )
    return "\n\n".join(blocks)


def generate_memory_body(summaries: list[dict]) -> str:
    # 长期记忆既是每次问答的注入内容，也是给外部平台阅读的「Andy 画像」，需要写足约 2 万字并
    # 严格遵循两层结构——这类长文+强指令任务改用更强的对话模型（gemini-3.5-flash），而非默认
    # 摘要用的 flash-lite（后者擅长压缩、不擅长写足）。其余摘要任务仍走 summary profile 的便宜模型。
    resp = ask_llm(
        _format_summaries(summaries),
        profile="summary",
        model=dialogue_params()["model"],
        system_instruction=SYSTEM_INSTRUCTION,
        max_output_tokens=summary_max_tokens("text"),
    )
    return resp.text.strip()


def update_memory(summaries: list[dict] | None = None) -> str:
    summaries = summaries if summaries is not None else load_summaries()
    body = generate_memory_body(summaries)

    dates = [s["session_date"] for s in summaries]
    header = (
        f"# 长期记忆（自动维护，请勿手动编辑主体）\n"
        f"更新时间：{datetime.date.today().isoformat()} | "
        f"已纳入咨询：{len(summaries)} 次（{min(dates)} ~ {max(dates)}）\n\n"
    )

    content = header + body + "\n"
    LONG_TERM_MEMORY_PATH.write_text(content, encoding="utf-8")
    return content


if __name__ == "__main__":
    content = update_memory()
    print(content)
    print(f"\n已写入 {LONG_TERM_MEMORY_PATH}")
