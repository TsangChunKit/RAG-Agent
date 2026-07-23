"""汇总"使用者与 AI 助手的聊天历史"→ 滚动生成 CHAT_MEMORY.md。

刻意和 update_memory.py（汇总真实咨询逐字稿摘要 → LONG_TERM_MEMORY.md）分开：
这里的素材是聊天记录，不是与真人咨询师"海特"的真实咨询，两者的可信度/性质不同，
不应该被混在同一份记忆里，也不应该互相污染。ask.py 的 answer() 会把两份记忆分别
标注清楚喂给 Gemini。
"""
import datetime
import json

from config import CHAT_MEMORY_PATH, CHAT_SESSIONS_DIR
from scripts.llm import ask_llm
from scripts.settings import summary_max_tokens

SYSTEM_INSTRUCTION = """\
你是使用者与一个 AI 心理咨询助手的聊天历史整理助手。这些对话不是使用者与真人咨询师的真实\
咨询记录，而是使用者平时主动找这个 AI 助手聊天时留下的提问、反思和探讨。请提炼滚动更新的\
"AI 对话记忆"，只输出以下板块的内容（不要输出板块之外的开场白或结语），保持精炼：

## 反复讨论的议题
使用者在和 AI 聊天时反复问起/关心的主题，每条一句话。

## 使用者自己提出的觉察或结论
使用者在对话过程中自己得出的、值得记住的觉察或决定，每条一句话（不是 AI 说的话，是使用者自己说的）。

## 待跟进的想法
对话里提到但还没有定论、后续可以再深入的问题或行动，每条一句话。

严格要求：只根据给定的聊天记录提炼，不要编造没有出现过的内容；不要把这些当成真实咨询内容\
去描述（比如不要说"在咨询中提到"，应该说"在和 AI 聊天时提到"）。"""


def load_chat_sessions() -> list[dict]:
    if not CHAT_SESSIONS_DIR.exists():
        return []
    sessions = []
    for f in sorted(CHAT_SESSIONS_DIR.glob("*.json")):
        try:
            sessions.append(json.loads(f.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            continue
    sessions.sort(key=lambda s: s.get("updated_at", ""))
    return sessions


def _format_sessions(sessions: list[dict]) -> str:
    blocks = []
    for s in sessions:
        turns = []
        for m in s.get("messages", []):
            if m["role"] == "user":
                turns.append(f"使用者问：{m['content']}")
            else:
                preview = m["content"][:300].replace("\n", " ")
                turns.append(f"AI答（节选）：{preview}")
        blocks.append(f"[对话 {s.get('updated_at', '')[:10]}｜{s.get('title', '')}]\n" + "\n".join(turns))
    return "\n\n".join(blocks)


def generate_chat_memory_body(sessions: list[dict]) -> str:
    resp = ask_llm(
        _format_sessions(sessions),
        profile="summary",
        system_instruction=SYSTEM_INSTRUCTION,
        max_output_tokens=summary_max_tokens("text"),
    )
    return resp.text.strip()


def update_chat_memory(sessions: list[dict] | None = None) -> str:
    sessions = sessions if sessions is not None else load_chat_sessions()
    if not sessions:
        content = (
            "# AI 对话记忆（自动维护，来自与 AI 助手的聊天历史，非真实咨询记录）\n"
            "更新时间：" + datetime.date.today().isoformat() + " | 已纳入对话：0 次\n\n"
            "（还没有聊天记录。）\n"
        )
        CHAT_MEMORY_PATH.write_text(content, encoding="utf-8")
        return content

    body = generate_chat_memory_body(sessions)
    header = (
        f"# AI 对话记忆（自动维护，来自与 AI 助手的聊天历史，非真实咨询记录）\n"
        f"更新时间：{datetime.date.today().isoformat()} | 已纳入对话：{len(sessions)} 次\n\n"
    )
    content = header + body + "\n"
    CHAT_MEMORY_PATH.write_text(content, encoding="utf-8")
    return content


if __name__ == "__main__":
    content = update_chat_memory()
    print(content)
    print(f"\n已写入 {CHAT_MEMORY_PATH}")
