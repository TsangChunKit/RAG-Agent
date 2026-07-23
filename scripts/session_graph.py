"""map-reduce 建图的 **map** 步：从单份咨询逐字稿抽取一张细粒度子图（fragment）。

和旧版"一次性把全部摘要塞进一个 LLM 调用出一张全局图"相比，逐份抽取的粒度天花板高得多：
① 直接读原始逐字稿（而非压缩过 6 个字段的摘要），保住细节；② 一次只看一份，LLM 不被迫把
53+ 份压成 4-8 个高度抽象的图式，而是尽量抽出这份里具体的信念/触发情境/自动思维/情绪。
跨份的"同一概念"归并留给 reduce 步（scripts/graph_utils.resolve_graph）。

产物按份缓存在 config.GRAPH_FRAGMENTS_DIR（和 summaries 一样），重跑只抽没缓存的新逐字稿。

节点分类依现代心理学理论分层，单一真相源见 scripts/graph_utils.NODE_TYPES / RELATION_TYPES：
- need（核心情感需要）：Schema Therapy 五大核心情感需要 + 自我决定论 + CCRT 渴望(W)
- person（依附对象/重要他人）：依附理论（Bowlby/Ainsworth）
- schema（早期不适应图式）：Young 图式治疗 18 图式 / 5 领域
- belief（中间信念/规则/条件假设）：Beck 认知概念化图的中间信念层（"如果…就…"）
- mode（图式模式）：图式治疗模式模型（脆弱儿童/愤怒儿童/惩罚性父母/疏离保护者/健康成人…）
- coping（应对/防御模式）：图式治疗三应对方式（屈服/回避/过度补偿）+ 防御机制 + 安全行为
- trigger（触发情境）：Ellis ABC 激活事件
- automatic_thought（自动思维）：Beck 认知模型
- emotion（情绪）：基本情绪理论 + Gross 情绪调节过程模型
- event（关键事件）：具体情境证据
"""
import json

from tqdm import tqdm

from config import GRAPH_FRAGMENTS_DIR
from scripts.graph_utils import NODE_TYPES, RELATION_TYPES
from scripts.llm import ask_llm
from scripts.parse import ParsedSession, iter_raw_files, parse_transcript, render_full_text
from scripts.settings import summary_max_tokens

# 抽取时不让 LLM 填日期（一份 fragment 的所有证据都来自这一份的 session_date，代码里注入，
# 避免 LLM 编造/串日期）；所以 schema 里没有 related_dates / evidence_dates 字段。
SESSION_GRAPH_SCHEMA = {
    "type": "object",
    "properties": {
        "nodes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "份内唯一，格式 '类型:简称'，如 schema:被抛弃 / belief:必须完美才配被爱 / trigger:伴侣已读不回"},
                    "type": {"type": "string", "enum": list(NODE_TYPES)},
                    "label": {"type": "string", "description": "图上显示的短标签，尽量用可跨会话复用的规范概念名（如'被抛弃图式'而非'这次的被抛弃感'）"},
                    "domain": {"type": "string", "description": "仅 type=schema 时填 Schema Therapy 五大领域之一的原文；其他类型留空字符串"},
                    "description": {"type": "string", "description": "1-3 句具体说明，可引用本次逐字稿里的具体情节/原话作为依据"},
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
                    "relation_type": {"type": "string", "enum": [r for r in RELATION_TYPES if r != "relates_to"]},
                    "relation": {"type": "string", "description": "具体关系描述；人物相关的边尽量写成'渴望(W)…→对方反应(RO)…→自我反应(RS)…'"},
                },
                "required": ["source", "target", "relation_type", "relation"],
            },
        },
    },
    "required": ["nodes", "edges"],
}

SCHEMA_DOMAINS = [
    "联结与拒绝（Disconnection & Rejection）",
    "自主性与表现受损（Impaired Autonomy & Performance）",
    "限制受损（Impaired Limits）",
    "他人取向（Other-Directedness）",
    "过度警觉与抑制（Overvigilance & Inhibition）",
]

SYSTEM_INSTRUCTION = f"""\
你是一位熟悉图式治疗（Schema Therapy，含图式与模式模型、五大核心情感需要）、Beck 认知概念化图、
Ellis ABC 模型、Gross 情绪调节、依附理论、自我决定论、核心冲突关系主题（CCRT）与精神病理网络
理论的心理咨询督导。你会收到**一份**咨询逐字稿（说话人、时间戳、原话）。请只根据这一份逐字稿，
抽取出一张**尽可能细粒度**的心理概念关系子图，供之后跨会话归并成整体心智地图。

## 节点类型（务必分层且细，不要只抽几个高层图式就完）
- **need（核心情感需要）**：图式治疗五大核心情感需要 / SDT（自主·胜任·联结）/ CCRT 的渴望(W)，
  如"被无条件接纳""安全稳定的联结""自主与掌控感"。
- **person（依附对象/重要他人）**：反复出现的真实人物，合并同一人不同称呼，区分不同时期的对象。
- **schema（早期不适应图式）**：参考 Young 五大领域来命名并在 domain 字段填对应领域原文：
{chr(10).join(f"    - {d}" for d in SCHEMA_DOMAINS)}
- **belief（中间信念/规则/条件假设）**：Beck 中间信念层——"如果…就…"的规则、态度、假设，
  如"如果我不完美，就不配被爱""表达需求会被抛弃"。**这一层最能提升粒度，请尽量多抽。**
- **mode（图式模式）**：当下被激活的情绪状态/自我部分，如"脆弱儿童""愤怒儿童""惩罚性父母"
  "疏离保护者""过度补偿者""健康成人"。
- **coping（应对/防御模式）**：屈服 / 回避 / 过度补偿三大应对方式，及具体防御机制、安全行为。
- **trigger（触发情境）**：会激活某图式/模式/情绪的具体情境（ABC 里的 A），如"伴侣已读不回"
  "被上司当众质疑"——比 event 更抽象、更可跨会话复用。
- **automatic_thought（自动思维）**：情境中一闪而过的念头，尽量贴近原话。
- **emotion（情绪）**：具体情绪标签（焦虑、羞耻、愤怒、麻木…）。
- **event（关键事件）**：本次提到的具体标志性事件/进展（客观事实）。

## 边（relation_type）——要产出丰富的跨层与横向连接，把"链路"和"内心拉扯"显性化
- unmet：need → schema（核心需要长期未满足，形成了这个图式）
- originates：person → schema 或 need（这个人是该图式/需要的发展源头，常是童年人物）
- assumes：schema → belief（核心图式派生出这条中间信念/规则）
- derives：schema → coping 或 mode（图式驱动出的应对模式/被激活的模式）
- activates：trigger → schema / mode / emotion（这个情境激活了它）
- triggers：person → coping 或 mode
- produces：belief 或 mode → automatic_thought（这条信念/这个模式下冒出的自动思维）
- evokes：automatic_thought 或 trigger → emotion（引发的情绪）
- regulated_by：emotion → coping（用这个应对/防御策略去调节该情绪）
- co_occurs：schema ↔ schema（两个图式常一起被激活、相互强化）
- reinforces：coping/mode ↔ coping/mode（相互强化）
- conflicts_with：coping/mode ↔ coping/mode（互相拉扯/矛盾，如"讨好"vs"想真实表达"）
- manifested_in：schema / coping / mode → event（在某个具体事件里体现出来）

人物相关的边（originates / triggers）relation 字段尽量写成 CCRT 三段式：
"渴望(W)：… → 对方反应(RO)：… → 自我反应(RS)：…"。

## 严格要求
- 只根据这一份逐字稿抽取，**绝不编造**逐字稿中没有出现的图式/信念/人物/情境/事件；
- id 用 "类型:简称" 格式，份内不重复；边的 source/target 必须引用本份里已定义的节点 id；
- label 尽量用**可跨会话复用的规范概念名**（这样不同会话里的同一概念才能被正确归并），
  概念的本次具体情节写进 description；
- 不要填任何日期字段（schema 里也没有），日期由系统按这份逐字稿的日期统一注入。
"""


def fragment_path(source_file: str):
    stem = source_file.rsplit(".", 1)[0]
    return GRAPH_FRAGMENTS_DIR / f"{stem}.json"


def _extract(session: ParsedSession) -> dict:
    resp = ask_llm(
        render_full_text(session),
        profile="summary",
        system_instruction=SYSTEM_INSTRUCTION,
        response_schema=SESSION_GRAPH_SCHEMA,
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


def build_session_fragment(session: ParsedSession, force: bool = False) -> dict:
    """抽取（或读缓存）单份咨询的子图 fragment。"""
    path = fragment_path(session.source_file)
    if path.exists() and not force:
        return json.loads(path.read_text(encoding="utf-8"))
    fragment = _extract(session)
    GRAPH_FRAGMENTS_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(fragment, ensure_ascii=False, indent=2), encoding="utf-8")
    return fragment


def ensure_fragments(force: bool = False) -> list[dict]:
    """确保每份逐字稿都有子图 fragment（缺的才抽，force=True 全量重抽），返回全部 fragment。"""
    fragments = []
    for f in tqdm(list(iter_raw_files()), desc="逐份抽取心智地图子图"):
        session = parse_transcript(f)
        if not session.utterances:
            continue
        fragments.append(build_session_fragment(session, force=force))
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
