"""集中管理所有可调参数。其余脚本一律从这里 import，不要在各处硬编码路径/参数。"""
from pathlib import Path

# ---- 目录 ----
BASE_DIR = Path(__file__).resolve().parent
# 所有"个人隐私数据"（原始逐字稿、向量库、长期/对话记忆、心智地图、API key）统一放在
# private.nosync/ 里。".nosync" 是 macOS 公认的 iCloud 排除机制：即便 iCloud「桌面与文稿」
# 同步被打开，名字以 .nosync 结尾的文件夹及其内容也不会被上传到 iCloud——满足「咨询数据
# 完全本地、不出机器」的硬约束（见 PROJECT_SPEC.md §0.5）。代码/配置（*.py、系统提示词、
# eval）不含个人内容，留在项目根目录，可以正常同步/进 git。
PRIVATE_DIR = BASE_DIR / "private.nosync"
ENV_PATH = PRIVATE_DIR / ".env"          # GEMINI_API_KEY（由 scripts/llm.py 加载）
RAW_DIR = PRIVATE_DIR / "data" / "raw"
PROCESSED_DIR = PRIVATE_DIR / "data" / "processed"
SUMMARIES_DIR = PRIVATE_DIR / "data" / "summaries"
DB_DIR = PRIVATE_DIR / "db"
LONG_TERM_MEMORY_PATH = PRIVATE_DIR / "LONG_TERM_MEMORY.md"
# AI 对话记忆——来自使用者与本 AI 助手的聊天历史，不是与真人咨询师"海特"的真实咨询记录，
# 刻意和 LONG_TERM_MEMORY_PATH（临床数据）分开存放，避免两者被混为一谈。
CHAT_MEMORY_PATH = PRIVATE_DIR / "CHAT_MEMORY.md"
EVAL_QUESTIONS_PATH = BASE_DIR / "eval" / "eval_questions.yaml"
GRAPH_JSON_PATH = PRIVATE_DIR / "data" / "graph.json"
# 逐次抽取（map-reduce 建图）的每份咨询子图片段缓存目录——和 summaries 一样，每份逐字稿
# 抽一次、缓存一份，重跑只处理没缓存的新逐字稿，避免每次全量重抽 57+ 次 LLM 调用。
GRAPH_FRAGMENTS_DIR = PRIVATE_DIR / "data" / "graph_fragments"
# AI 对话记忆的心智地图——和上面真实咨询的图谱分开存放，生成时便宜得多（只喂 graph.json
# 当参考 + 聊天记录，不重新处理全部咨询摘要），随 update_chat_memory 一起自动更新。
CHAT_GRAPH_JSON_PATH = PRIVATE_DIR / "data" / "chat_graph.json"
# 可在 Streamlit 设置弹窗里编辑的 system instruction；不含个人逐字稿内容，留在根目录（可同步）。
SYSTEM_INSTRUCTION_PATH = BASE_DIR / "system_instruction.md"
# 多会话聊天历史持久化目录（app.py 用），每个会话一个 JSON 文件。
CHAT_SESSIONS_DIR = PRIVATE_DIR / "data" / "chat_sessions"
# Explicit Caching 状态记录（system instruction + 长期记忆 + 心智地图这类固定内容），
# 记录当前生效的缓存资源名 + 内容指纹，内容一变就检测出来自动重建。
EXPLICIT_CACHE_STATE_PATH = PRIVATE_DIR / "data" / ".explicit_cache_state.json"
# Gemini 运行时可调参数 + API key（可在 Streamlit「⚙️ Gemini 设置」里改）。含 API key，放隐私目录。
# 不存在或缺字段时，scripts/settings.py 回退到下面 §Gemini 里的默认值——删掉这个文件 = 恢复默认。
GEMINI_SETTINGS_PATH = PRIVATE_DIR / "gemini_settings.json"
# 索引运行时可调参数（检索 top_k/窗口、分块大小/重叠、本地 embedding、FTS 分词），可在 Streamlit
# 「⚙️ 索引设置」里改。和 gemini_settings.json 一样：不存在或缺字段时，scripts/index_settings.py
# 回退到下面 §分块 / §Embedding / §LanceDB / §检索 里的默认常量——删掉这个文件 = 恢复默认。
# 不含个人内容，但为与其它运行时设置放一起、复用「删文件=恢复默认」的心智模型，同样放隐私目录。
INDEX_SETTINGS_PATH = PRIVATE_DIR / "index_settings.json"
# 索引变更记录（append-only JSONL）：每次新增/重建/跳过入库都追加一行（时间、动作、文件、片段数），
# 供 Streamlit「📚 已索引的咨询记录」展示审计轨迹。属运行产物、含逐字稿文件名，放隐私目录。
INDEX_CHANGELOG_PATH = PRIVATE_DIR / "data" / "index_changelog.jsonl"

# ---- Hermes Agent Gateway（本地 OpenAI 兼容代理，转发到 xAI grok，自己夹使用者的 OAuth）----
# 这些是"默认值"，可在 Streamlit「⚙️ Gemini 设置」里覆盖（存进 gemini_settings.json）。
# key 任意（proxy 自己处理鉴权），仍可被 .env（HERMES_API_KEY）或设置覆盖；模型名在「模型」框里填 grok-4.5。
HERMES_BASE_URL = "http://127.0.0.1:8645/v1"
HERMES_API_KEY = "sk-unused"

# ---- 逐字稿解析 ----
# 文件名前 14 位数字：前 8 位 = YYYYMMDD（咨询日期），后 6 位 = 文件生成时间（记录但不依赖）
FILENAME_DATETIME_RE = r"^(\d{14})"
# 发言人(HH:MM:SS): 文本  —— 不硬编码说话人名字
TRANSCRIPT_LINE_RE = r"^(.+?)\((\d{2}:\d{2}:\d{2})\):\s?(.*)$"

# ---- 分块 ----
CHUNK_SIZE_CHARS = 400          # 300–500 字目标，取中间值
CHUNK_OVERLAP_CHARS = 65        # 50–80 字目标，取中间值
CHUNK_WINDOW_EXPAND = 1         # 父块扩展：命中块前后各扩展 N 个块

# ---- Embedding（BGE-M3）----
EMBEDDING_MODEL_NAME = "BAAI/bge-m3"
EMBEDDING_USE_FP16 = True        # 已验证：速度快、质量损失极小。切勿改用 Q4 权重做检索。
EMBEDDING_DIM = 1024
# 实测（M5 Pro/48GB）：FlagEmbedding 默认加载到 CPU，未使用 Apple GPU。
# 显式指定 devices="mps" 后，batch_size=256 时比 CPU 快约 9 倍（0.71s vs 6.52s），
# dense 向量数值差异仅 ~1.5e-4（fp16 精度级噪声，不影响检索质量）。
EMBEDDING_DEVICE = "mps"
EMBEDDING_BATCH_SIZE = 256       # M5 Pro/48GB 实测 256 明显更快且稳定（统一内存充裕）

# ---- LanceDB ----
LANCEDB_TABLE_NAME = "sessions"
# FTS 关键词侧的分词器：改用 jieba/default 做真正的中文分词（效果优先，用户已同意联网下载）。
# 环境搭建步骤（新机器/词典丢失时重新执行）：
#   1. pip install pylance
#   2. python -m lance.download jieba   # 会打印 LANCE_LANGUAGE_MODEL_HOME 路径
#   3. 已知坑：lance 自带下载器写死的 GitHub 路径已失效（messense/jieba-rs 仓库重构后
#      dict.txt 从 src/data/dict.txt 移到了 jieba/src/data/dict.txt），下载器不会校验
#      HTTP 状态码，会把 404 页面的 HTML 当成词典写入，导致建索引时报「不是合法频率整数」。
#      需要手动重新下载覆盖：
#        curl -s "https://raw.githubusercontent.com/messense/jieba-rs/main/jieba/src/data/dict.txt" \
#          -o "$(python -c 'import lance; print(lance.download.LANGUAGE_MODEL_HOME)')/jieba/default/dict.txt"
# 之前用的 ngram(2-3字符) 分词器已验证可用，作为该环境词典缺失时的本地零依赖备选方案。
FTS_BASE_TOKENIZER = "jieba/default"
FTS_NGRAM_MIN_LENGTH = 2
FTS_NGRAM_MAX_LENGTH = 3

# ---- 检索 ----
RETRIEVAL_TOP_K = 8

# ---- 心智地图证据日 → 定向片段（GraphRAG 证据检索；查询期后处理，改完下一次问答立即生效）----
# 命中核心图式/应对模式后，沿图收集的"关键证据日期"不再整份逐字稿塞入上下文（太占 token 且大量
# 无关对话稀释注意力），而是用锚点概念向量在那天的块里做一次定向检索，取最相关的几段 + 本场结构化
# 摘要（摘要已由 summarize.py 预生成，几百 token 就概括整场弧线）。这几个数量/宽度/开关都是纯
# 查询期后处理，可在 Streamlit「⚙️ 索引设置」→ 心智地图证据片段 里热调，改完下一次问答立即生效。
GRAPH_EVIDENCE_MAX_DATES = 3          # 最多取几个证据日（0 = 关闭这条通路，图谱仍贡献 entity-anchored 片段）
GRAPH_EVIDENCE_FRAGMENTS_PER_DATE = 3 # 每个证据日在那天内捞几段最相关片段
GRAPH_EVIDENCE_WINDOW_EXPAND = 2      # 证据片段专属父块扩展（比普通检索的 window_expand 更宽，还原来龙去脉）
GRAPH_EVIDENCE_INCLUDE_SUMMARY = True # 是否附上该场的结构化摘要（整场覆盖，便宜）

# ---- Reranker（cross-encoder 精排；这些是"默认值"，实际运行值由 scripts/index_settings.py
#      从 INDEX_SETTINGS_PATH 读取，可在 Streamlit「⚙️ 索引设置」→ Reranker 里改）----
# hybrid（dense + FTS + RRF）先取较多候选，再用 bge-reranker-v2-m3 这个多语种 cross-encoder
# 对 (query, passage) 逐对精排，取最高的 FINAL_TOP_K 条作为最终结果，之后才做父块扩展。
# 本地运行（和 embedding 一样跑 mps），不出网。USE_RERANKER 是 A/B 开关：关掉就退回纯 hybrid。
# 开关 / RERANKER_TOP_K / FINAL_TOP_K 是查询期后处理，改完下一次问答立即生效、无需重建；
# MODEL / DEVICE / USE_FP16 因模型进程内缓存为单例，改完需重启服务生效（同 EMBEDDING）。
USE_RERANKER = True
RERANKER_MODEL_NAME = "BAAI/bge-reranker-v2-m3"
RERANKER_DEVICE = "mps"          # 和 EMBEDDING_DEVICE 一致，跑本机 Apple GPU
RERANKER_USE_FP16 = True
RERANKER_TOP_K = 20              # 开启 rerank 时 hybrid 先取的候选数（交给 reranker 精排）
FINAL_TOP_K = 8                  # rerank 后最终保留、进入父块扩展的数量

# ---- Gemini（这些都是"默认值"；实际运行值由 scripts/settings.py 从 GEMINI_SETTINGS_PATH
#      读取，可在 Streamlit「⚙️ Gemini 设置」里改，改完下一次调用即生效、无需重启）----
# 对话（问答）参数：
# 2026-07 联网核对（ai.google.dev/gemini-api/docs/whats-new-gemini-3.5）：
# gemini-3.5-flash 已 GA，支持 thinking_level（low/medium/high，默认 medium）。
GEMINI_MODEL = "gemini-3.5-flash"
GEMINI_THINKING_LEVEL = "high"
GEMINI_TEMPERATURE = 0.6
# thinking_level=high 时，思考 token 也计入这个预算（实测过 high thinking 消耗
# 100–250+ token），2048 偏紧，调大到 4096 给深度共情式回答留够空间。
GEMINI_MAX_OUTPUT_TOKENS = 4096

# 摘要/长期记忆/心智地图/聊天记忆这类批量提炼任务，用更便宜的 flash-lite（2026-07 联网核对：
# gemini-3.1-flash-lite 已 GA，是当前最省成本的稳定模型），不需要 gemini-3.5-flash 的推理深度。
GEMINI_SUMMARY_MODEL = "gemini-3.1-flash-lite"
GEMINI_SUMMARY_THINKING_LEVEL = "high"
GEMINI_SUMMARY_TEMPERATURE = 0.6
# summary 类任务的 max_output_tokens 按"输出体量"分三档（图谱要输出大段结构化 JSON，需要高上限）：
GEMINI_SUMMARY_MAX_TOKENS = {
    "text": 60000,         # 文本摘要类：每份逐字稿摘要 / 长期记忆（详实画像，约 2 万字）/ AI 对话记忆
    "chat_graph": 16000,   # AI 对话记忆心智地图
    "therapy_graph": 32000,  # 真实咨询心智地图
}
