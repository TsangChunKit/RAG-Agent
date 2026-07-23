# PROJECT_SPEC.md — 个人 AI 心理咨询师系统

> **本文件的用途**:这是交给 Claude Code 的完整技术规格与实施蓝图。
> 所有架构决策、技术选型、API 集成方式均已于 2026-07 联网验证,尽量避免 LLM 幻觉。
> 在 Claude Code 中的用法:把本文件放在项目根目录,对 Claude Code 说
> "请阅读 PROJECT_SPEC.md 并按其中的 Milestone 顺序实现"。
>
> **文档最后更新**:2026-07-04
> **规格作者**:与 Claude (claude.ai) 协作制定,经 web 验证
> **实施者**:Claude Code(交互式)+ 使用者本人

---

## 0. 背景与目的(Why)

### 0.1 使用者是谁,数据是什么

使用者每周与心理咨询师"海特"进行一次定期咨询(固定周日 14:00,腾讯会议)。
每次咨询结束后:
1. 从腾讯会议手动下载**逐字稿 txt**;
2. 上传到 Google Drive
   (`https://drive.google.com/drive/u/0/folders/17osT3EPGDm6gHwKqLyJyTP9VCZFdo0Iy`)。

**逐字稿的确切格式**(已核对真实样本,务必据此解析):

```
发言人(HH:MM:SS): 内容文字。

发言人(HH:MM:SS): 下一段内容。
```

真实样例(节选):

```
Andy(00:00:17): 哈海特。

海特(00:00:19): 我们就开始今天的咨询。

Andy(00:02:03): 我想一下。先分享一下……这周我感觉发生了蛮多正向的事情……
```

关键格式事实:
- 每一行是一次发言,格式恒为 `说话人(时间戳): 文本`。
- 说话人目前只有两位:`Andy`(使用者)和 `海特`(咨询师)。**不要硬编码只认这两个名字**——用正则捕获 `^(.+?)\((\d{2}:\d{2}:\d{2})\):\s?(.*)$`,以防未来出现第三人或名字变化。
- 段落之间以空行分隔。
- 单份逐字稿约 200 行、时长约 50 分钟。
- **文件内没有日期**。

### 0.2 日期在哪里(关键)

**咨询日期只存在于文件名**,格式为 `YYYYMMDDHHMMSS-...`。
例:`20260627163210-Andy预定的会议-逐字稿文本-1.txt` → 咨询日期 **2026-06-27**。

解析规则:取文件名开头连续 14 位数字,前 8 位 = `YYYYMMDD` 即咨询日期。
(时分秒 `163210` 是文件生成时间,不是咨询开始时间,记录但不依赖它。)

### 0.3 痛点(Pain Point)

- 目前累计约 **53 个逐字稿文件**,总 token 量已达 **~500k** 量级。
- 若每次都把全部逐字稿直接上传给 LLM:
  - 费用高;
  - 极易逼近甚至突破 1M token 上下文上限;
  - 大量无关内容稀释了 LLM 注意力,反而降低回答质量。

### 0.4 目标(What we're building)

一个**本地运行**的个人 AI 心理咨询系统,能够:
1. **高效检索**所有历史咨询,而不是全量塞进上下文 → 大幅省 token。
2. 基于检索到的相关片段,**运用不同心理学流派与原则**进行解答
   (CBT 认知行为、精神分析、正念/接纳承诺、依恋理论等)。
3. 从问答与咨询中**提取并滚动维护长期记忆**(核心议题、反复模式、进展轨迹)。
4. **每周增量更新**:新逐字稿加入时,一条命令完成入库 + 摘要 + 记忆更新,无需重跑全量。

**成功标准**:单次问答的输入 token 从 ~500k 降到通常 **10–30k**,同时回答质量不下降
(答案不细碎、不遗漏关键上下文)。

### 0.5 隐私要求(硬约束)

心理咨询数据高度敏感。**Embedding 与向量库必须完全本地**,咨询原文不出使用者的机器。
唯一允许出网的是"问答生成"这一步(调用 Gemini API),且只发送**检索出的相关片段**,
不整体上传全部逐字稿。若使用者日后希望连问答也本地化,可替换为本地 LLM(见 §7.3)。

---

## 1. 架构总览(经验证的三层 + 检索层三项升级)

系统采用业界 2026 主流的 **RAG 双管道 + 分层记忆** 架构。核心认知(已验证):
**RAG 失败时,约 73% 的失败点在"检索"而非"生成"**。所以省 token 的关键不是少塞内容,
而是**塞得准**——检索质量直接决定回答质量。

### 1.1 三层设计

```
┌─────────────────────────────────────────────────────────────┐
│ 第一层：向量检索层（解决"精确调取某次谈了什么"，核心省 token）│
│   逐字稿 → 上下文化分块 → BGE-M3 向量化 → LanceDB（本地）      │
│   提问时只检索 top-k 相关片段，而非全量                        │
├─────────────────────────────────────────────────────────────┤
│ 第二层：浓缩记忆层（解决"长期主线与人物画像"）                 │
│   每份逐字稿 → 结构化摘要 JSON → 滚动汇总 LONG_TERM_MEMORY.md  │
│   这份记忆很小（几 k token），每次问答都全量携带              │
├─────────────────────────────────────────────────────────────┤
│ 第三层：元数据层（解决"时间维度查询"）                        │
│   文件名解析出咨询日期 → 写入每个 chunk 的 metadata           │
│   支持"过去一个月我谈了什么""某议题的演变时间线"             │
└─────────────────────────────────────────────────────────────┘
```

### 1.2 检索层的三项关键升级（这是"省 token 又不伤质量"的正解）

这三项都在初版就要实现,理由见 §3 的 effort/impact 表。

**升级 A — 混合检索(Hybrid Search)。**
纯向量检索有已知硬伤:会漏掉精确关键词(人名、机构名、"电脑""换工作"这类具体词)。
纯关键词检索又会漏掉语义相似。**混合检索(向量 + 关键词)是对朴素 RAG 最大的单项质量提升。**
好消息:选用的 BGE-M3 模型**单次前向传播同时输出稠密向量(语义)和稀疏向量(关键词)**,
一个模型搞定混合检索,无需额外搭 BM25 服务。

**升级 B — 上下文化分块(Contextual Chunking)。**
2026 标准做法:给每个 chunk 前面加 1–2 句上下文摘要,说明它讲什么、在哪次咨询、什么主题。
例:某片段前自动加一句
`[2026-06-27 咨询｜主题：消费心理｜Andy 在讨论买高端游戏电脑时的花钱挣扎]`。
这样即使片段本身短,检索和 LLM 都知道它的来龙去脉,答案不会支离破碎。

**升级 C — 父块/窗口扩展(Parent/Window Expansion)。**
用**小块**(300–500 字)去做检索命中(高精度),但**喂给 LLM 时把命中块前后相邻的块也一起带上**
(还原完整上下文)。这直接解决"检索太细碎导致答案缺上下文"的顾虑。
本质是把 RAG 拆成"搜索(小粒度、高召回)"和"阅读(大粒度、完整)"两个阶段。

---

## 2. 技术选型(全部经 2026-07 联网验证)

| 组件 | 选型 | 状态 | 为什么 |
|---|---|---|---|
| 运行环境 | 本地 MacBook Pro **M5 Pro / 48GB** | ✅ | 使用者现有机器,配置充裕 |
| Embedding | **BGE-M3**(`BAAI/bge-m3`) | ✅ 验证 | 中文开源标杆;单模型出稠密+稀疏+ColBERT;MIT 许可;本地免费 |
| Embedding 加载库 | **FlagEmbedding** 的 `BGEM3FlagModel` | ✅ 验证 | 官方库,唯一能一次拿到稠密+稀疏两种向量 |
| 向量库 | **LanceDB**(开源本地版) | ✅ 验证 | 基于文件、无需起服务、原生混合检索 + 元数据过滤、Apple Silicon 友好 |
| 问答 LLM | **Google Gemini API** | ✅ 验证 | 使用者已有;中文好;合规(见 §4) |
| 开发工具 | **Claude Code**(在 Claude Desktop 内,交互式) | ✅ 验证 | 走 Pro 订阅、云无关、原生 MCP;非 Kiro(见 §4.4) |
| 语言 | **Python 3.11+** | — | 生态最全 |
| Reranker(v1.5) | `BAAI/bge-reranker-v2-m3` | ✅ 验证 | 与 BGE-M3 同门;初版先不加,不够再加 |

### 2.1 为什么 Embedding 选 BGE-M3 而非 Qwen3-Embedding-8B

- 有一篇 **2026-06 在 M5 Max(128GB)上的实测评测**直接覆盖本场景,结论是一个清晰的阶梯:
  Qwen3-Embedding-8B 在技术/推理密集任务上分数最高,但 **BGE-M3 在会话式、短查询任务上把差距拉近**。
  心理咨询逐字稿正是**会话式、口语化**内容,BGE-M3 恰好是其强项区。
- **只有 BGE-M3 从单次前向传播同时输出稠密 + 稀疏向量**,这是实现混合检索最省事的路径。
- Qwen3-8B 即使 Q4 量化也要约 5GB 常驻,53 份的小项目性价比不划算。
  (M5 Pro 跑得动,若日后追求极致质量可换,但**换 embedding = 整库重新索引**,故初版选定后尽量别换。)
- 量化提示(已验证):`use_fp16=True` 即可,速度快、质量损失极小;
  **不要用 Q4 权重**——实测 Q4 会掉 1–2 个 nDCG 点,不适合检索。

### 2.2 为什么向量库选 LanceDB

- 基于文件、无需起服务,53 份规模下**零运维**。
- **原生混合检索**:`query_type="hybrid"`,且支持"外部 embedder 显式传入向量"的模式,
  完美契合我们本地用 BGE-M3 自己算向量的场景(见 §5.3 代码)。
- 原生元数据过滤(日期查询很顺),Apple Silicon 优化好。
- 备选:**ChromaDB**(更老牌、文档更多)。若使用者更想要社区问答最丰富的方案可换,
  但 LanceDB 的混合检索 + 元数据过滤在本项目更顺手。

---

## 3. 功能分级与 effort/impact(初版包含哪些)

**Impact = 对"省 token + 回答质量"的贡献。** 设计阶段(即本文件)现在就定好;
写代码到 Claude Code 做。**更换 embedding = 整库重建**,所以选型必须一开始就对。

| 功能 | Effort | Impact | 版本 |
|---|:---:|:---:|:---:|
| 文件名解析日期 → metadata | 极低 | 高 | **v1** ✅ |
| 递归分块 300–500 字 + 重叠 50–80 字 | 低 | 高 | **v1** ✅ |
| BGE-M3 向量化 + LanceDB 建库 | 低 | 高(核心省 token) | **v1** ✅ |
| 混合检索(dense+sparse) | 低* | 很高 | **v1** ✅ |
| 上下文化分块(每块加摘要句) | 中 | 很高 | **v1** ✅ |
| 父块/窗口扩展(防细碎) | 低 | 高 | **v1** ✅ |
| 每份结构化摘要(JSON) | 中 | 高 | **v1** ✅ |
| 滚动长期记忆 LONG_TERM_MEMORY.md | 中 | 高 | **v1** ✅ |
| 增量更新脚本(每周加新文件) | 低 | 高(可维护性) | **v1** ✅ |
| 心理学流派路由(提问先判议题再选框架) | 中 | 中 | **v1** ✅(轻量版,见 §5.4) |
| Reranker 重排序 | 中 | 中–高 | v1.5 🔶 |
| 议题标签体系(职业/关系/消费/自我价值…) | 中 | 中 | v1.5 🔶 |
| 情绪轨迹曲线 | 中 | 中(锦上添花) | v2 ⬜ |
| Agentic RAG(多跳迭代检索) | 高 | 中 | 暂不 ⬛ |
| 知识图谱 | 高 | 中 | 暂不 ⬛ |

\* 混合检索标"低",因 BGE-M3 一个模型自带稠密+稀疏,不用额外搭 BM25。

**为何 Reranker 放 v1.5**:业界建议按阶段递进——先建评估框架,再依次加混合检索、元数据过滤、
重排序等越来越贵的步骤。53 份规模下,混合检索 + 上下文化分块大概率已够好。先跑起来用评估集测,
不够再加 `bge-reranker-v2-m3`。

**为何 Agentic RAG / 知识图谱暂不做**:它们对"跨领域复杂多跳推理"有价值,但本项目是单人连续咨询、
议题连贯,普通混合检索足够。过度工程只会拖慢上线。

---

## 4. LLM 与合规(务必遵守)

### 4.1 结论:开发用 Claude Code,系统运行用 Gemini API

| 阶段 | 用什么 | 计费/合规 |
|---|---|---|
| **开发**(写代码、调试,人坐在终端前) | **Claude Code**(交互式) | 走 Pro 订阅,合规 ✅ |
| **系统运行**(脚本自动调 LLM 做问答/摘要) | **Gemini API** | 走 Gemini 计费,合规 ✅ |

### 4.2 为什么运行时不能用 Claude Pro(重要,已验证)

本项目的问答/摘要是"脚本自动调用 LLM",属于**程序化/自动化访问**。
- Anthropic 消费者条款:**除非通过 Anthropic API Key 访问或另有明确许可,不得通过自动化或非人工方式
  (bot、脚本等)访问服务**。
- 用**订阅账号**(非 API key)跑 headless/程序化模式,官方文档未警示的风险是**可能被封号**;
  且实践中 `claude -p` 在有订阅、未设 API key 时**仍按 API 逐 token 计费**——
  已有真实案例两天内产生 **1800 美元以上**意外费用。
- 分界线:**人在打字 = 走订阅;代码自己调用 = 另算/踩线**。

因此:**在 Claude Code 里交互式开发这个系统 = 合规走订阅;系统跑起来后的自动问答 = 用 Gemini。**
两者不冲突,这是唯一干净的组合。

### 4.3 环境变量注意(避免误扣费)

- 登录 Claude Code 用 Pro 账号:`claude login`。
- **务必确认没有设 `ANTHROPIC_API_KEY` 环境变量**——一旦设了,Claude Code 会改用该 key 走 API 计费,
  而不是订阅。检查:`echo "$ANTHROPIC_API_KEY"`(有值就去 `.zshrc`/`.bashrc`/`.env` 里清掉;
  别把 key 贴到任何地方)。

### 4.4 为什么开发工具选 Claude Code 而非 Amazon Kiro(已验证)

- **Kiro 最强项是 AWS 原生集成**(Lambda/DynamoDB/S3/CloudFormation/IAM),本项目不碰 AWS,零价值。
- **Kiro 截至 2026-04 不支持 MCP**;本项目要连 Google Drive、可能要自动化,Claude Code 原生 MCP 是刚需。
- Claude Code **云无关**,且使用者已装 Claude Desktop + 有 Pro 订阅,零额外成本、零切换。
- Kiro 的 spec-driven 好处(研究显示规格驱动减少 23–37% 逻辑错误)**我们已用本文件手动实现**,
  且经联网验证,比 AI 自动生成的规格更可靠。

---

## 5. 详细实施(脚本职责 + 已验证代码骨架)

> 以下代码经 2026-07 联网核对官方文档,可作为 Claude Code 的实现起点。
> Claude Code 应在此基础上补全错误处理、日志、CLI 参数。

### 5.0 目录结构

```
ai-therapist/
├── PROJECT_SPEC.md          # 本文件
├── .env                     # GEMINI_API_KEY（不进 git）
├── .gitignore               # 忽略 .env, db/, data/raw/
├── requirements.txt
├── config.py                # 所有可调参数集中于此
├── data/
│   ├── raw/                 # 从 Google Drive 下载的 53 个 txt
│   ├── processed/           # 清洗+分块+metadata 的中间产物（JSONL）
│   └── summaries/           # 每份逐字稿的结构化摘要（JSON）
├── db/                      # LanceDB 持久化目录
├── LONG_TERM_MEMORY.md      # 滚动浓缩的长期记忆
├── scripts/
│   ├── parse.py             # 解析逐字稿 + 从文件名取日期
│   ├── chunk.py             # 上下文化分块
│   ├── ingest.py            # 向量化 + 入 LanceDB（含混合检索索引）
│   ├── summarize.py         # 每份结构化摘要（调 Gemini）
│   ├── update_memory.py     # 汇总摘要 → 更新 LONG_TERM_MEMORY.md（调 Gemini）
│   ├── ask.py               # 检索 + 组装上下文 + 问答（调 Gemini）
│   └── ingest_new.py        # 增量：新文件一条龙（parse→chunk→ingest→summarize→update_memory）
└── eval/
    └── eval_questions.yaml  # 5–10 个已知答案的问题，测检索质量
```

### 5.1 requirements.txt（起点）

```
lancedb
FlagEmbedding
google-genai
pandas
pyarrow
tantivy          # LanceDB 全文检索索引需要
python-dotenv
pyyaml
tqdm
```

### 5.2 Embedding：BGE-M3 加载与编码（已验证 API）

```python
# scripts/embedder.py
from FlagEmbedding import BGEM3FlagModel

# use_fp16=True：速度快、质量损失极小（已验证）。切勿用 Q4 权重做检索。
_model = BGEM3FlagModel('BAAI/bge-m3', use_fp16=True)

def embed(texts: list[str]) -> dict:
    """
    返回 dense（稠密/语义向量）和 sparse（稀疏/关键词权重）两种表示。
    这是混合检索的基础，一次前向传播同时得到两者。
    """
    out = _model.encode(
        texts,
        return_dense=True,
        return_sparse=True,
        return_colbert_vecs=False,   # 本项目暂不用 ColBERT，省算力
    )
    # out['dense_vecs']       -> np.ndarray, 形状 (N, 1024)
    # out['lexical_weights']  -> list[dict]，每个是 {token_id: weight}
    return out
```

> 说明:BGE-M3 稠密向量维度为 **1024**。稀疏向量是 token→权重的字典,
> LanceDB 混合检索可用其做关键词侧;最简实现是把命中片段的原文交给 LanceDB 的 FTS(全文检索),
> 稠密向量走向量侧——见 §5.3。

### 5.3 LanceDB：建表 + 混合检索（已验证 API）

关键点(已验证):当我们用**外部 embedder**(BGE-M3)自己算向量、不走 LanceDB 内置 embedding 时,
混合检索要用 `.vector(向量)` + `.text(查询词)` **显式传入**两侧,再 `query_type="hybrid"`。

```python
# scripts/ingest.py（核心片段）
import lancedb
from lancedb.rerankers import RRFReranker

db = lancedb.connect("./db")

# 表结构：每行是一个 chunk
# 字段：id, text（含上下文化前缀）, raw_text（原始片段）, vector（1024 维稠密）,
#       session_date（YYYY-MM-DD）, speaker, source_file, chunk_index,
#       prev_chunk_id, next_chunk_id（用于父块扩展）
# 建表时把 dense 向量作为 vector 列写入；text 列建 FTS 索引做关键词侧。

table = db.create_table("sessions", data=rows, mode="overwrite")
table.create_fts_index("text")          # 全文检索索引（关键词侧）
# 向量索引：53 份数据量小，可不建 ANN 索引直接暴力搜；数据量大时再 table.create_index()

# ---- 检索（scripts/ask.py 核心片段）----
def retrieve(query: str, k: int = 8):
    q = embed([query])
    q_vec = q['dense_vecs'][0]

    reranker = RRFReranker()   # 默认倒数排名融合，合并向量+关键词结果
    results = (
        table.search(query_type="hybrid")
             .vector(q_vec)           # 语义侧：BGE-M3 稠密向量
             .text(sanitize(query))   # 关键词侧：原始查询词（需清洗特殊字符）
             .rerank(reranker)
             .limit(k)
             .to_pandas()
    )
    return results

import re
def sanitize(q: str) -> str:
    # 去掉会破坏 LanceDB FTS 的字符（已验证的坑）
    return re.sub(r"['\"\\]", "", q)
```

> **父块扩展**:`retrieve` 拿到 k 个命中后,对每个命中用其 `prev_chunk_id` / `next_chunk_id`
> 再查出相邻块,拼成完整上下文再交给 LLM。这一步在 `ask.py` 里做,不在检索本身。

### 5.4 Gemini 问答（已验证 API + 模型名）

**模型选择(已验证,2026-07)**:
- 稳定生产推荐:`gemini-2.5-flash`(2.5 家族中最快、最省成本的多模态模型,官方文档持续列出)。
- Google 已推出更新的 `gemini-3.x` 系列和新的 `interactions` API,但那是较新的接口;
  **本项目用成熟稳定的 `client.models.generate_content` + `gemini-2.5-flash` 起步**,
  跑通后若想升级模型,只改模型名字符串即可。
- 复杂议题分析想要更强推理时,可切 `gemini-2.5-pro`(2.5 家族最强推理/编码)。
- **Claude Code 在实现时应先联网核对当前可用的确切模型名**(模型名会更新),不要盲信本文件的字符串。

```python
# scripts/llm.py（已验证 SDK 用法）
from google import genai
from google.genai import types
import os
from dotenv import load_dotenv

load_dotenv()
# SDK 自动读取 GEMINI_API_KEY 或 GOOGLE_API_KEY 环境变量
client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

MODEL = "gemini-2.5-flash"   # Claude Code：实现时联网确认最新稳定模型名

def ask_llm(system_instruction: str, user_content: str) -> str:
    resp = client.models.generate_content(
        model=MODEL,
        contents=user_content,
        config=types.GenerateContentConfig(
            system_instruction=system_instruction,
            temperature=0.6,
            max_output_tokens=2048,
        ),
    )
    return resp.text
```

**问答时的上下文组装**(这是省 token 的核心):

```
最终喂给 Gemini 的 = 
    system_instruction（心理学流派指引，见下）
  + LONG_TERM_MEMORY.md 全文（小，几 k token）
  + 检索出的 top-k 片段（已做父块扩展，含上下文化前缀）
  + 使用者当前问题
```

**心理学流派路由(v1 轻量版)**:在 system_instruction 里给 LLM 一段指引,让它:
1. 先判断使用者问题属于哪类议题(职业焦虑 / 亲密关系 / 消费与自我价值 / 存在与意义 等);
2. 据此选择最合适的流派框架作答:
   - 认知扭曲、灾难化想象 → **CBT 认知重构**;
   - 反复模式、早年经验 → **精神分析 / 心理动力**;
   - 决策纠结、价值澄清、"道理都懂但做不到" → **接纳承诺疗法 ACT + 正念**;
   - 亲密关系模式 → **依恋理论**;
3. 说明用了哪个框架,保持温和、非评判(镜像咨询师"海特"的风格)。

> 注:样本逐字稿里出现的正是这些议题(换工作的恐惧与克服、买高端电脑的花钱挣扎、
> 亲密关系小摩擦、"理性明白但感性受伤"),流派路由针对性很强。

### 5.5 结构化摘要（summarize.py 的产物 schema）

每份逐字稿生成一个 JSON(调 Gemini,一次性 53 次,之后每周只加 1 次):

```json
{
  "session_date": "2026-06-27",
  "source_file": "20260627163210-....txt",
  "topics": ["职业转换", "消费心理", "亲密关系"],
  "emotional_tone": "整体正向、夹带对花钱与显卡贬值的焦虑",
  "key_events": [
    "第二轮面试顺利，接近拿到 offer",
    "在现公司提出技术方案获上司与新加坡同事认可"
  ],
  "psychological_themes": [
    "克服换工作的恐惧（灾难化想象 vs 真实难度）",
    "只存不花 vs 及时行乐的价值冲突"
  ],
  "decisions_or_actions": [
    "下周谈待遇，考虑要求加薪幅度",
    "电脑仍在纠结，尚未购买"
  ],
  "quotes_worth_remembering": [
    "我觉得阻碍我换工作的，未必是换工作本身难，而是背后很多想象带来的恐惧"
  ]
}
```

### 5.6 长期记忆（LONG_TERM_MEMORY.md 结构）

`update_memory.py` 读取所有摘要 JSON,调 Gemini 滚动更新这份 Markdown。保持精炼(目标 < 3k token):

```markdown
# 长期记忆（自动维护，请勿手动编辑主体）
更新时间：2026-06-27 | 已纳入咨询：53 次

## 核心议题（反复出现）
- 职业转换与自我价值：从害怕投简历 → 面试 → 接近成功的心路历程
- 消费与自我价值："只存不花"的挣扎，理性与感性的拉锯
- 亲密关系：与女友的小摩擦与修复模式

## 反复出现的心理模式
- "道理都懂但做不到"——理性认知与情绪体验的落差
- 灾难化想象放大对未知的恐惧

## 进展轨迹（时间线）
- 3月：还没勇气投简历
- （中略）
- 6月：第二轮面试顺利，进入谈待遇阶段

## 与咨询师的关系风格
- 咨询师"海特"：温和、非评判、常用第三者视角提问
```

### 5.7 增量更新（ingest_new.py）

每周新逐字稿下载到 `data/raw/` 后,一条命令:

```bash
python scripts/ingest_new.py data/raw/新文件.txt
# 内部依次：parse → chunk → embed → 追加进 LanceDB → summarize → update_memory
```

LanceDB 支持**追加新行而不重建全表**(append column / add data),所以增量很轻。

---

## 6. 实施顺序（Milestones，交给 Claude Code 按序执行）

**Claude Code:请严格按此顺序,每个 Milestone 完成后暂停让使用者验收。**

### M0 — 脚手架
- 建目录结构、`requirements.txt`、`config.py`、`.gitignore`、`.env` 模板。
- 安装依赖,验证 `FlagEmbedding`、`lancedb`、`google-genai` 能 import。
- **联网确认** BGE-M3 加载方式与 Gemini 当前稳定模型名(不要盲信本文件字符串)。

### M1 — 解析 + 分块（不涉及 LLM/网络，先跑通）
- `parse.py`:从文件名取日期,正则解析发言人/时间戳/文本。
- `chunk.py`:递归分块 300–500 字 + 重叠;生成上下文化前缀;记录 prev/next chunk id。
- **验收**:拿 3–5 份 txt 跑,人工检查分块是否语义完整、日期是否正确、上下文前缀是否合理。

### M2 — 向量化 + 建库 + 混合检索
- `embedder.py` + `ingest.py`:BGE-M3 编码,写入 LanceDB,建 FTS 索引。
- `ask.py` 的 `retrieve()`:混合检索 + 父块扩展(先不接 LLM,只打印检索结果)。
- **验收**:用 eval 问题测,人工看检索出的片段是否命中、是否够完整(不细碎)。

### M3 — Gemini 问答 + 流派路由
- `llm.py` + `ask.py` 完整版:组装 `记忆 + 检索片段 + 问题`,调 Gemini,含流派路由 system prompt。
- **验收**:问 3–5 个真实问题,检查回答质量、流派是否贴切、语气是否温和;
  用 usage metadata 确认单次输入 token 落在 10–30k。

### M4 — 摘要 + 长期记忆
- `summarize.py`:对全部 53 份生成结构化摘要 JSON。
- `update_memory.py`:汇总 → 生成 `LONG_TERM_MEMORY.md`。
- **验收**:人工读长期记忆,是否准确概括核心议题与进展。

### M5 — 增量更新
- `ingest_new.py`:新文件一条龙。
- **验收**:拿一份"新"txt 跑,确认只增量处理、库与记忆都正确更新。

### M6（可选，v1.5）— 评估 + Reranker
- 若 M2/M3 发现检索不够准,加 `bge-reranker-v2-m3`。
- 扩充 eval 集,建立可重复的检索质量度量。

下面是 M7 的内容,直接粘贴到 spec 里即可。我把它放在 §6 的 M6 之后、§7 附录之前最合适。

---

### M7（可选，阶段二）— 前端 UI

> **前置条件**:M1–M5 后端已跑通并验收(检索质量、回答质量、token 已降到 10–30k)。
> UI 是"薄壳",不影响后端架构,故放到最后、按需实现。
> **本节的主要作用之一**:提前告知 Claude Code UI 的存在,使其在实现后端时把
> "检索 + 上下文组装 + 问答"写成**可复用函数**,而非写死在命令行脚本里,避免加 UI 时返工。

**选型:Streamlit(首选)或 Gradio。**
- 都是 Python 轻量 Web UI 框架,几十行即可把 `ask.py` 变成浏览器聊天界面。
- 本地运行(`localhost`),数据不出机器,**符合本项目隐私硬约束(§0.5)**。
- Claude Code 对二者驾轻就熟;阶段二直接说"用 Streamlit 给 ask.py 套聊天界面"即可。

**UI 需具备的能力**:
- 气泡式**连续对话**(界面层维护对话历史,支持多轮);
- 历史**可滚动回看**;
- 中文长段落**排版舒适**(咨询回复通常较长);
- (可选)显示本轮**检索到的片段来源与咨询日期**,便于溯源;
- (可选)显示本轮**输入 token 数**,直观确认省 token 效果。

**对后端的接口要求(Claude Code 实现 M3 时就要满足)**:
`ask.py` 须暴露一个纯函数,签名类似:

```python
def answer(question: str, history: list[dict] | None = None) -> dict:
    """
    UI 与 CLI 共用的核心入口。
    返回：{
        "answer": str,              # LLM 回复文本
        "sources": list[dict],      # 检索到的片段（含 session_date、source_file）
        "input_tokens": int,        # 本轮输入 token 数
    }
    """
```
命令行入口和未来的 Streamlit 前端都调用同一个 `answer()`,不重复实现检索/问答逻辑。

**验收**:浏览器打开本地 UI,进行 3–5 轮连续对话,确认多轮上下文正确、历史可回看、
排版可读;若开启来源显示,核对片段与日期准确。

**明确不做(避免过度工程)**:用户账号系统、云部署、多用户、移动端适配——本项目是单人本地使用,
Streamlit 本地页足矣。

---

## 7. 附录

### 7.1 使用者需先准备的事项（本地，约 10 分钟）
1. 安装 Claude Code(已装 Claude Desktop 的话,在其中启用 Code)。用 **Pro 账号**登录,
   **确认没有 `ANTHROPIC_API_KEY` 环境变量**。
2. 从 Google Drive 把 53 个 txt 下载到 `data/raw/`。
3. 申请 Gemini API key,放进 `.env`:`GEMINI_API_KEY=...`。
4. 在项目目录启动 Claude Code,说"阅读 PROJECT_SPEC.md 并从 M0 开始实现"。

### 7.2 评估集示例（eval/eval_questions.yaml）
准备 5–10 个"你知道答案"的问题,用来判断检索准不准、要不要加 reranker:

```yaml
- q: "我哪一次谈到了买游戏电脑的纠结？"
  expect_contains: ["电脑", "显卡", "花钱"]
  expect_date: "2026-06-27"
- q: "我对换工作的恐惧具体是什么？"
  expect_contains: ["灾难化", "面试", "被发现", "市场价值"]
- q: "咨询师建议我用什么视角看自己的心路历程？"
  expect_contains: ["第三者", "视角"]
```

### 7.3 未来可选升级（不影响初版）
- **连问答也本地化**:把 Gemini 换成本地 LLM(如通过 Ollama 跑开源模型),彻底不出网。
  代价是回答质量可能不及 Gemini,视使用者对隐私 vs 质量的权衡。
- **议题标签体系**:给摘要打结构化标签,支持"把所有亲密关系的谈话串成时间线"。
- **情绪轨迹曲线**:每份摘要记情绪基调,画几个月的情绪/进展曲线。
- **Reranker / Agentic RAG**:数据量增长或问题变复杂时再上。

### 7.4 已验证事实清单（供 Claude Code 交叉核对，避免幻觉）
- 逐字稿格式:`说话人(HH:MM:SS): 文本`,日期只在文件名前 8 位数字。
- BGE-M3:`BGEM3FlagModel('BAAI/bge-m3', use_fp16=True)`,`encode(..., return_dense=True, return_sparse=True)`;
  稠密 1024 维;单次前向出稠密+稀疏;勿用 Q4。
- LanceDB 混合检索(外部向量):`table.search(query_type="hybrid").vector(vec).text(q).rerank(RRFReranker()).limit(k)`;
  需 `table.create_fts_index("text")`;FTS 查询词要清洗 `['"\\]`。
- Gemini SDK:`google-genai` 包;`genai.Client(api_key=...)`;`client.models.generate_content(model="gemini-2.5-flash", contents=..., config=types.GenerateContentConfig(...))`;
  `resp.text` 取结果;环境变量 `GEMINI_API_KEY`。**实现时联网确认最新稳定模型名。**
- 合规:程序化调用**必须用 API key 的 Gemini**,不可用 Claude 订阅跑脚本(封号 + 误扣费风险)。
- 开发工具:Claude Code(云无关、原生 MCP、走 Pro 订阅),非 Kiro(无 MCP、绑 AWS)。

---

**— END OF SPEC —**
