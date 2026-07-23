"""每份文档生成一个结构化摘要 JSON（调 LLM），支持多 workspace。

产物 schema 见 PROJECT_SPEC.md §5.5。session_date/source_file 等元数据直接取自文件名解析结果，
不问 LLM（避免它编造日期）；只让 LLM 提炼核心内容。
"""
import json
from typing import Optional

from tqdm import tqdm

from config import SUMMARIES_DIR
from scripts.llm import ask_llm
from scripts.settings import summary_max_tokens
from scripts.parse import ParsedSession, iter_raw_files, parse_transcript, render_full_text

SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "topics": {"type": "array", "items": {"type": "string"}},
        "emotional_tone": {"type": "string"},
        "key_events": {"type": "array", "items": {"type": "string"}},
        "psychological_themes": {"type": "array", "items": {"type": "string"}},
        "decisions_or_actions": {"type": "array", "items": {"type": "string"}},
        "quotes_worth_remembering": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "topics",
        "emotional_tone",
        "key_events",
        "psychological_themes",
        "decisions_or_actions",
        "quotes_worth_remembering",
    ],
}

SYSTEM_INSTRUCTION = """\
你是一位心理咨询记录整理助手。你会收到一份心理咨询逐字稿（说话人、时间戳、原话），\
请提炼出结构化摘要，字段含义：
- topics：本次咨询涉及的主题标签（如"职业转换""消费心理""亲密关系"），2-5 个，短词/短语；
- emotional_tone：整体情绪基调，一句话概括；
- key_events：本次咨询提到的具体事件/进展（客观事实，不是感受），每条一句话；
- psychological_themes：本次咨询体现出的心理议题/模式（如认知扭曲、依恋模式、价值冲突），每条一句话；
- decisions_or_actions：咨询中提到的决定或后续行动（含未决事项），每条一句话；
- quotes_worth_remembering：1-3 句最值得记住的原话，尽量贴近原文表达，可以为了可读性去掉口语中的\
重复/语气词，但不能改变原意或添加原文没有的内容。

严格要求：只根据给定的逐字稿内容提炼，不要编造逐字稿中没有出现的信息。"""


def summary_path(source_file: str, workspace_id: Optional[str] = None):
    """获取摘要文件路径（workspace 感知）。"""
    stem = source_file.rsplit(".", 1)[0]
    return SUMMARIES_DIR(workspace_id) / f"{stem}.json"


def summarize_session(session: ParsedSession) -> dict:
    transcript_text = render_full_text(session)
    resp = ask_llm(
        transcript_text,
        profile="summary",
        system_instruction=SYSTEM_INSTRUCTION,
        response_schema=SUMMARY_SCHEMA,
        max_output_tokens=summary_max_tokens("text"),
    )
    extracted = json.loads(resp.text)
    return {
        "session_date": session.session_date,
        "source_file": session.source_file,
        "file_datetime": session.file_datetime,
        **extracted,
    }


def summarize_all(force: bool = False, workspace_id: Optional[str] = None) -> list[dict]:
    """生成所有文档的摘要（workspace 感知）。"""
    summaries_dir = SUMMARIES_DIR(workspace_id)
    summaries_dir.mkdir(parents=True, exist_ok=True)
    summaries = []
    for f in tqdm(iter_raw_files(workspace_id), desc="summarizing"):
        out_path = summary_path(f.name, workspace_id)
        if out_path.exists() and not force:
            summaries.append(json.loads(out_path.read_text(encoding="utf-8")))
            continue
        session = parse_transcript(f)
        summary = summarize_session(session)
        out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        summaries.append(summary)
    return summaries


if __name__ == "__main__":
    import sys

    force = "--force" in sys.argv
    summaries = summarize_all(force=force)
    print(f"共生成/加载 {len(summaries)} 份摘要，位于 {SUMMARIES_DIR}")
    print(json.dumps(summaries[0], ensure_ascii=False, indent=2))
