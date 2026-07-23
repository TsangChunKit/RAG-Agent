"""map-reduce 建图的 **map** 步：从单份文档抽取一张细粒度子图（fragment）。

和旧版"一次性把全部摘要塞进一个 LLM 调用出一张全局图"相比，逐份抽取的粒度天花板高得多：
① 直接读原始文档（而非压缩过的摘要），保住细节；② 一次只看一份，LLM 不被迫把多份压成高度
抽象的概念，而是尽量抽出具体的节点。跨份的"同一概念"归并留给 reduce 步（scripts/graph_utils.resolve_graph）。

产物按份缓存在 config.GRAPH_FRAGMENTS_DIR（和 summaries 一样），重跑只抽没缓存的新文档。

节点分类依据 workspace 的 graph schema（由 graph_schema_loader 加载），支持多领域：
- counseling: 心理学理论分层（need/person/schema/belief/mode/coping/trigger/automatic_thought/emotion/event）
- sutras: 佛学概念（concept/person/teaching/practice/text）
- solution_arch: 架构设计（requirement/component/technology/pattern/risk/decision）
- generic: 通用兜底（concept/entity/event/process）
"""
from typing import Optional
import json

from tqdm import tqdm

from config import GRAPH_FRAGMENTS_DIR
from scripts.graph_schema_loader import load_schema, get_node_types, get_relation_types, get_system_instruction, get_schema_domains
from scripts.graph_utils import NODE_TYPES, RELATION_TYPES  # 保留作为兜底默认值
from scripts.llm import ask_llm
from scripts.parse import ParsedSession, iter_raw_files, parse_transcript, render_full_text
from scripts.settings import summary_max_tokens
from scripts.workspace_manager import get_current_workspace

def _build_session_graph_schema(node_types: dict, relation_types: dict, schema_domains: Optional[list] = None) -> dict:
    """

动态构建 SESSION_GRAPH_SCHEMA，支持不同 workspace 的节点/关系类型。

    Args:
        node_types: 节点类型定义（从 schema 加载）
        relation_types: 关系类型定义（从 schema 加载）
        schema_domains: schema domains 列表（仅心理学 schema 有，用于 domain 字段说明）

    Returns:
        JSONSchema 定义
    """
    # 动态生成 domain 字段说明
    domain_desc = "domain 字段说明"
    has_domain_types = [t for t, v in node_types.items() if v.get("has_domain")]
    if has_domain_types and schema_domains:
        domain_desc = f"仅 type={'/'.join(has_domain_types)} 时填对应领域；其他类型留空字符串"
    else:
        domain_desc = "所有类型都留空字符串"

    return {
        "type": "object",
        "properties": {
            "nodes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "description": "份内唯一，格式 '类型:简称'"},
                        "type": {"type": "string", "enum": list(node_types.keys())},
                        "label": {"type": "string", "description": "图上显示的短标签，尽量用可跨文档复用的规范概念名"},
                        "domain": {"type": "string", "description": domain_desc},
                        "description": {"type": "string", "description": "1-3 句具体说明，可引用本次文档里的具体情节作为依据"},
                    },
                    "required": ["id", "type", "label", "domain", "description"],
                },
            },
            "edges": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "source": {"type": "string"},
                        "target": {"type": "string"},
                        "relation_type": {"type": "string", "enum": [r for r in relation_types.keys() if r != "relates_to"]},
                        "relation": {"type": "string", "description": "具体关系描述"},
                    },
                    "required": ["source", "target", "relation_type", "relation"],
                },
            },
        },
        "required": ["nodes", "edges"],
    }


def fragment_path(source_file: str, workspace_id: Optional[str] = None):
    """获取 fragment 缓存路径（workspace 感知）。"""
    stem = source_file.rsplit(".", 1)[0]
    return GRAPH_FRAGMENTS_DIR(workspace_id) / f"{stem}.json"


def _extract(session: ParsedSession, workspace_id: Optional[str] = None) -> dict:
    """抽取单份文档的子图，使用当前 workspace 的 schema。"""
    # 加载 workspace schema
    schema = load_schema(workspace_id)
    node_types = get_node_types(schema)
    relation_types = get_relation_types(schema)
    schema_domains = get_schema_domains(schema)

    # 获取 system instruction（优先使用 schema 中的模板）
    system_instruction = get_system_instruction(schema)
    if not system_instruction:
        # 降级：使用旧版硬编码 prompt（仅用于兼容，新 workspace 应在 schema 中定义）
        system_instruction = f"""\
你是知识图谱构建专家。你会收到一份文档。请从中抽取知识图谱。

抽取时只根据文档内容，不编造；id 用 "类型:简称" 格式；label 用简洁名称，description 写具体说明。
"""

    # 动态构建 response schema
    session_graph_schema = _build_session_graph_schema(node_types, relation_types, schema_domains)

    # 调用 LLM 抽取
    resp = ask_llm(
        render_full_text(session),
        profile="summary",
        system_instruction=system_instruction,
        response_schema=session_graph_schema,
        max_output_tokens=summary_max_tokens("therapy_graph"),
    )
    raw = json.loads(resp.text)

    # 注入本份日期；丢弃引用了未定义节点 id 的坏边。
    date = session.session_date
    for n in raw["nodes"]:
        n["related_dates"] = [date]
    node_ids = {n["id"] for n in raw["nodes"]}
    edges = []
    for e in raw["edges"]:
        if e["source"] in node_ids and e["target"] in node_ids and e["source"] != e["target"]:
            e["evidence_dates"] = [date]
            edges.append(e)
    return {
        "source_file": session.source_file,
        "session_date": date,
        "nodes": raw["nodes"],
        "edges": edges,
    }


def build_session_fragment(session: ParsedSession, force: bool = False, workspace_id: Optional[str] = None) -> dict:
    """抽取（或读缓存）单份文档的子图 fragment（workspace 感知）。"""
    path = fragment_path(session.source_file, workspace_id)
    if path.exists() and not force:
        return json.loads(path.read_text(encoding="utf-8"))
    fragment = _extract(session, workspace_id)
    fragments_dir = GRAPH_FRAGMENTS_DIR(workspace_id)
    fragments_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(fragment, ensure_ascii=False, indent=2), encoding="utf-8")
    return fragment


def ensure_fragments(force: bool = False, workspace_id: Optional[str] = None) -> list[dict]:
    """确保每份文档都有子图 fragment（缺的才抽，force=True 全量重抽），返回全部 fragment（workspace 感知）。"""
    fragments = []
    for f in tqdm(list(iter_raw_files(workspace_id)), desc="逐份抽取图谱子图"):
        session = parse_transcript(f)
        if not session.utterances:
            continue
        fragments.append(build_session_fragment(session, force=force, workspace_id=workspace_id))
    return fragments


if __name__ == "__main__":
    import sys

    if "--one" in sys.argv:
        # 验证用：只抽第一份（或指定文件名），打印结果，不写全量。
        files = list(iter_raw_files())
        target = None
        for a in sys.argv[1:]:
            if not a.startswith("--"):
                target = next((f for f in files if a in f.name), None)
        session = parse_transcript(target or files[0])
        frag = _extract(session)
        print(json.dumps(frag, ensure_ascii=False, indent=2))
        from collections import Counter
        print("\n节点类型分布:", Counter(n["type"] for n in frag["nodes"]))
        print("关系类型分布:", Counter(e["relation_type"] for e in frag["edges"]))
    else:
        frags = ensure_fragments(force="--force" in sys.argv)
        total_n = sum(len(f["nodes"]) for f in frags)
        total_e = sum(len(f["edges"]) for f in frags)
        print(f"共 {len(frags)} 份 fragment，原始节点 {total_n} 个、边 {total_e} 条（归并前）")
