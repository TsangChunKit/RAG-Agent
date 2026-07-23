"""M7：给 ask.py 的 answer() 套一层 Streamlit 聊天界面。
命令行入口（scripts/ask.py）和这里共用同一个 answer()，不重复实现检索/问答逻辑。
明确不做：用户账号、云部署、多用户、移动端适配——单人本地使用，本地网页够了。

多会话历史（scripts/chat_store.py）：每个对话持久化成本地 JSON 文件，侧边栏可以切换/新建/删除，
刷新页面或重启服务不会丢失历史。
"""
import logging

import streamlit as st

from config import CHAT_MEMORY_PATH
from scripts import index_records, index_settings, settings
from scripts.ask import answer, load_system_instruction, reset_system_instruction, save_system_instruction
from scripts.chat_store import delete_session, list_sessions, load_session, make_title, new_session_id, save_session

# 开了热重载（.streamlit/config.toml 的 runOnSave + watchdog）后，Streamlit 的 LocalSourcesWatcher
# 每次 rerun 都会遍历 sys.modules 找「本项目内被 import 的模块」来监听。途中它会去解析每个模块的
# 文件路径，而 transformers/FlagEmbedding 有一堆需要 torchvision 才能加载的惰性子模块（vitpose /
# yolos / zoedepth 等图像处理器），解析必然失败，于是日志被 "Examining the path of ... raised:
# ModuleNotFoundError: No module named 'torchvision'" 刷屏。这些跟本项目无关、纯噪音（我们根本没
# 装也不需要 torchvision），把这个 watcher 专用 logger 调到 ERROR 压掉即可——热重载本身照常工作，
# 真正的报错也仍会打出来。这样就兼得了「改代码自动生效」和「日志干净、闲时零开销」。
logging.getLogger("streamlit.watcher.local_sources_watcher").setLevel(logging.ERROR)

st.set_page_config(page_title="AI 心理咨询助手", page_icon="🌱", layout="centered")

st.markdown(
    """
    <style>
    .stChatMessage p { line-height: 1.75; font-size: 1.02rem; }
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("🌱 AI 心理咨询助手")
st.caption("基于你历次咨询逐字稿的检索增强问答——只发送检索到的相关片段，不上传全部逐字稿。")


@st.dialog("⚙️ System Instruction 设置", width="large")
def system_instruction_dialog():
    current = load_system_instruction()
    edited = st.text_area("System Instruction（决定 AI 的人设、流派路由规则、回答风格）", value=current, height=420)
    col1, col2 = st.columns(2)
    if col1.button("保存", type="primary", use_container_width=True):
        save_system_instruction(edited)
        st.toast("已保存，下一轮问答立即生效")
        st.rerun()
    if col2.button("恢复默认", use_container_width=True):
        reset_system_instruction()
        st.toast("已恢复默认")
        st.rerun()


_THINKING_LEVELS = ["minimal", "low", "medium", "high"]


@st.dialog("⚙️ Gemini 设置", width="large")
def gemini_settings_dialog():
    cur = settings.load_for_ui()
    st.caption(
        "对话 = 你问答时用的模型；摘要 = 长期记忆/心智地图/对话记忆这类后台批量任务用的（通常用更便宜的模型）。"
        "改完点保存，下一次调用即生效、无需重启。"
    )

    st.markdown("##### 🔀 LLM 后端 provider")
    _PROVIDERS = ["gemini", "grok", "hermes"]
    prov = st.radio(
        "选用哪个后端（切到 grok / hermes 后，下面的「模型」框请填对应模型名，如 grok-4 / grok-4.5）",
        _PROVIDERS,
        index=_PROVIDERS.index(cur.get("provider", "gemini")),
        horizontal=True, key="llm_provider",
        help="gemini = Google Gemini（支持 Explicit Cache）；grok = xAI 直连；"
             "hermes = 本地 Hermes Agent Gateway 代理（转发到 xAI grok，自己夹 OAuth）。"
             "grok / hermes 都是 OpenAI 兼容、无显式缓存、自动退回内联。",
    )

    st.markdown("##### 🔑 API Key")
    kc1, kc2 = st.columns(2)
    with kc1:
        st.caption("Gemini：已设置 ✅" if cur["api_key_set"] else "Gemini：⚠️ 尚未设置")
        api_key = st.text_input(
            "Gemini API Key", value="", type="password",
            placeholder="留空 = 保留现有 key；填入 = 覆盖", help="申请地址：aistudio.google.com/apikey",
        )
    with kc2:
        st.caption("xAI：已设置 ✅" if cur.get("xai_api_key_set") else "xAI：⚠️ 尚未设置")
        xai_key = st.text_input(
            "xAI API Key", value="", type="password",
            placeholder="留空 = 保留现有 key；填入 = 覆盖", help="申请地址：console.x.ai",
        )

    st.caption("Hermes Agent Gateway（本地代理；key 任意，代理自己夹 OAuth）")
    hc1, hc2 = st.columns(2)
    with hc1:
        hermes_url = st.text_input(
            "Hermes Base URL", value=cur.get("hermes_base_url", ""), key="hermes_url",
            placeholder="http://127.0.0.1:8645/v1", help="到 /v1 为止，不含 /chat/completions",
        )
    with hc2:
        hermes_key = st.text_input(
            "Hermes API Key", value="", type="password",
            placeholder="留空 = 保留现有（默认 sk-unused）", help="代理自己处理鉴权，填任意串即可",
        )

    def _thinking_index(v):
        return _THINKING_LEVELS.index(v) if v in _THINKING_LEVELS else _THINKING_LEVELS.index("high")

    col_d, col_s = st.columns(2)
    with col_d:
        st.markdown("##### 💬 对话（问答）")
        d = cur["dialogue"]
        d_model = st.text_input("模型", value=d["model"], key="d_model",
                                help="如 gemini-3.5-flash / gemini-3.1-flash-lite；模型名会更新，用文本框而非写死下拉")
        d_think = st.selectbox("思考深度 thinking_level", _THINKING_LEVELS, index=_thinking_index(d["thinking_level"]), key="d_think")
        d_temp = st.slider("温度 temperature", 0.0, 2.0, float(d["temperature"]), 0.05, key="d_temp")
        d_max = st.number_input("最大输出 token", 256, 65000, int(d["max_output_tokens"]), 256, key="d_max",
                                help="thinking=high 时思考 token 也占这个预算；对话回复一般 4096 够用")
    with col_s:
        st.markdown("##### 📝 摘要（后台批量任务）")
        s = cur["summary"]
        s_model = st.text_input("模型", value=s["model"], key="s_model")
        s_think = st.selectbox("思考深度 thinking_level", _THINKING_LEVELS, index=_thinking_index(s["thinking_level"]), key="s_think")
        s_temp = st.slider("温度 temperature", 0.0, 2.0, float(s["temperature"]), 0.05, key="s_temp")
        st.caption("最大输出 token（按任务分档，图谱输出大需高上限）：")
        smt = cur["summary_max_tokens"]
        s_max_text = st.number_input("文本摘要类（摘要/长期记忆/对话记忆）", 256, 65000, int(smt["text"]), 256, key="s_max_text")
        s_max_cg = st.number_input("AI 对话记忆心智地图", 256, 65000, int(smt["chat_graph"]), 256, key="s_max_cg")
        s_max_tg = st.number_input("真实咨询心智地图", 256, 65000, int(smt["therapy_graph"]), 256, key="s_max_tg")

    c1, c2 = st.columns(2)
    if c1.button("保存", type="primary", use_container_width=True):
        settings.save(
            dialogue={"model": d_model.strip(), "thinking_level": d_think, "temperature": d_temp, "max_output_tokens": int(d_max)},
            summary={"model": s_model.strip(), "thinking_level": s_think, "temperature": s_temp},
            summary_max={"text": int(s_max_text), "chat_graph": int(s_max_cg), "therapy_graph": int(s_max_tg)},
            api_key=api_key or None,
            provider=prov,
            xai_api_key=xai_key or None,
            hermes_api_key=hermes_key or None,
            hermes_base_url=hermes_url or None,
        )
        st.toast("已保存，下一次调用即生效")
        st.rerun()
    if c2.button("恢复默认参数（保留 API Key）", use_container_width=True):
        settings.reset()
        st.toast("已恢复默认参数")
        st.rerun()


@st.dialog("📚 已索引的咨询记录", width="large")
def indexed_records_dialog():
    records = index_records.list_indexed_records()
    total_chunks = sum(r["n_chunks"] for r in records)
    st.caption(
        f"向量库里共 **{len(records)}** 份咨询逐字稿、**{total_chunks}** 个片段（chunk）。"
        "全部索引在本机 LanceDB，建库/检索都不出网。"
    )
    if records:
        import pandas as pd

        df = pd.DataFrame(
            [
                {
                    "咨询日期": r["session_date"],
                    "文件": r["source_file"],
                    "片段数": r["n_chunks"],
                    "摘要": "✅" if r["has_summary"] else "—",
                }
                for r in records
            ]
        )
        st.dataframe(df, use_container_width=True, hide_index=True)
    else:
        st.info("向量库还是空的。把逐字稿放进 private.nosync/data/raw/ 后跑 `python -m scripts.ingest_new <文件>`。")

    st.markdown("##### 🧾 变更记录（最近 30 条）")
    st.caption("每次新增 / 重建 / 跳过入库都会自动记一行，方便回溯「什么时候进了哪份记录」。")
    log = index_records.load_change_log(limit=30)
    if log:
        for e in log:
            label = index_records.ACTION_LABELS.get(e["action"], e["action"])
            bits = [e["ts"], label]
            if e.get("source_file"):
                bits.append(e["source_file"])
            if e.get("n_chunks"):
                bits.append(f"{e['n_chunks']} 片段")
            if e.get("note"):
                bits.append(e["note"])
            st.caption(" ｜ ".join(bits))
    else:
        st.caption("还没有变更记录。新增或重建索引后会出现在这里。")


_EMBED_DEVICES = ["mps", "cpu", "cuda"]


@st.dialog("⚙️ 索引设置", width="large")
def index_settings_dialog():
    cur = index_settings.load_for_ui()
    st.caption(
        "索引**全程在本地**：分块（纯 Python）→ BGE-M3 向量化（Apple GPU / MPS）→ 本地 LanceDB，"
        "建索引和检索都不出网。只有问答 / 摘要才会调 Gemini。改完点保存即写入。"
    )

    with st.expander("❓ 哪些参数改完需要「全量重建」？"):
        st.markdown(
            "| 参数 | 需要全量重建？ | 说明 |\n"
            "|---|---|---|\n"
            "| 分块大小 chunk_size | ✅ 需要 | 直接改变每块文字内容，旧向量全部失效 |\n"
            "| 块间重叠 chunk_overlap | ✅ 需要 | 同上 |\n"
            "| FTS 分词器 base_tokenizer | ✅ 需要 | 影响 FTS 索引，必须重建 FTS |\n"
            "| ngram 最短 / 最长 | ✅ 需要 | 同上，属于 FTS 参数 |\n"
            "| Embedding 模型（换模型） | ✅ 需要 | 向量空间变了，旧向量不能用（且需重启服务）|\n"
            "| Embedding 维度 | ✅ 需要 | 同上 |\n"
            "| top_k（检索返回数量） | ❌ 不需要 | 只影响查询时取多少，改完立即生效 |\n"
            "| 父块窗口扩展 | ❌ 不需要 | 后处理逻辑，改完立即生效 |\n"
            "| batch_size | ❌ 不需要 | 只影响 ingest 速度，不影响已存数据 |\n"
            "| device (mps/cpu) | ❌ 不需要 | 只影响计算设备（换 embedding device 需重启服务）|\n"
            "| Reranker 开关 / rerank_top_k / final_top_k | ❌ 不需要 | 纯后处理，改完立即生效 |\n"
            "| Reranker model / device / fp16 | ❌ 不需要 | 不动向量库，但需重启服务生效 |\n"
            "| 心智地图证据片段（证据日数 / 每日段数 / 扩展 / 摘要） | ❌ 不需要 | 纯查询期后处理，改完立即生效 |\n"
            "| RRF 或其他 fusion 方式 | ❌ 不需要 | 查询时逻辑 |\n"
        )
        st.caption(
            "换句话说：**改了「分块 / FTS / Embedding 模型·维度」才要点下方「全量重建」**；"
            "检索 / reranker 类参数改完下一次问答立即生效。"
        )

    st.markdown("##### 🔍 检索（改完下一次问答立即生效，无需重建）")
    r = cur["retrieval"]
    top_k = st.number_input("检索返回片段数 top_k", 1, 50, int(r["top_k"]), 1)
    win = st.number_input("父块窗口扩展（命中块前后各扩 N 块）", 0, 5, int(r["window_expand"]), 1)

    st.markdown("##### ✂️ 分块（只影响之后新入库的记录；要对全部历史生效请用下方「全量重建」）")
    c = cur["chunking"]
    csz = st.number_input("分块大小 chunk_size（字符）", 100, 2000, int(c["chunk_size"]), 50)
    cov = st.number_input("块间重叠 chunk_overlap（字符）", 0, 500, int(c["chunk_overlap"]), 5)

    st.markdown("##### 🧠 Embedding（本地 BGE-M3；model / device 改动需重启服务生效）")
    e = cur["embedding"]
    e_model = st.text_input("模型", value=e["model"], key="e_model")
    e_dev_idx = _EMBED_DEVICES.index(e["device"]) if e["device"] in _EMBED_DEVICES else 0
    e_dev = st.selectbox("设备 device", _EMBED_DEVICES, index=e_dev_idx, key="e_dev",
                         help="mps = Apple GPU（推荐，实测比 CPU 快约 9 倍）；cpu = 兜底；cuda = NVIDIA 卡")
    e_batch = st.number_input("批大小 batch_size", 1, 1024, int(e["batch_size"]), 8, key="e_batch")
    e_fp16 = st.checkbox("use_fp16（推荐开；切勿改用 Q4 权重）", value=bool(e["use_fp16"]), key="e_fp16")

    st.markdown("##### 🔡 关键词检索分词（FTS；改完需重建索引生效）")
    f = cur["fts"]
    f_tok = st.text_input("分词器 base_tokenizer", value=f["base_tokenizer"], key="f_tok",
                          help="如 jieba/default（中文分词）；词典缺失时可回退到 ngram")
    fc1, fc2 = st.columns(2)
    f_min = fc1.number_input("ngram 最短", 1, 10, int(f["ngram_min"]), 1, key="f_min")
    f_max = fc2.number_input("ngram 最长", 1, 10, int(f["ngram_max"]), 1, key="f_max")

    st.markdown("##### 🎯 Reranker 精排（cross-encoder）")
    st.caption(
        "hybrid 先取候选 → bge-reranker 逐对精排取最终数量 → 父块扩展。**开关 / 候选数 / 保留数**是查询期"
        "后处理，改完下一次问答立即生效、无需重建；**model / device / fp16** 同 embedding，改完需重启服务生效。"
    )
    rr = cur["reranker"]
    rr_on = st.checkbox("启用 reranker（关掉退回纯 hybrid，取上面的 top_k）", value=bool(rr["use_reranker"]), key="rr_on")
    rc1, rc2 = st.columns(2)
    rr_cand = rc1.number_input("候选数 rerank_top_k（hybrid 先取）", 1, 200, int(rr["rerank_top_k"]), 1, key="rr_cand",
                               help="开 rerank 时 hybrid 先取多少候选交给精排；应 ≥ 最终保留数")
    rr_final = rc2.number_input("最终保留数 final_top_k（精排后进父块扩展）", 1, 50, int(rr["final_top_k"]), 1, key="rr_final")
    rr_model = st.text_input("模型 model", value=rr["model"], key="rr_model",
                             help="如 BAAI/bge-reranker-v2-m3；换模型需重启服务生效（进程内单例缓存）")
    rr_dev_idx = _EMBED_DEVICES.index(rr["device"]) if rr["device"] in _EMBED_DEVICES else 0
    rr_dev = st.selectbox("设备 device", _EMBED_DEVICES, index=rr_dev_idx, key="rr_dev",
                          help="mps = Apple GPU（推荐）；cpu = 兜底；cuda = NVIDIA 卡。改完需重启服务生效")
    rr_fp16 = st.checkbox("use_fp16（推荐开）", value=bool(rr["use_fp16"]), key="rr_fp16",
                          help="改完需重启服务生效")

    st.markdown("##### 🕸️ 心智地图证据片段（改完下一次问答立即生效，无需重建）")
    st.caption(
        "命中核心图式 / 应对模式后，沿图收集的「关键证据日期」不再整份逐字稿塞入，而是在那天内做一次"
        "**定向检索**取最相关的几段 + 本场**结构化摘要**（整场覆盖、便宜）。数量 / 宽度越大，上下文越"
        "丰富、token 越多。想更省就把「取几个证据日」调小甚至设 0。"
    )
    ge = cur["graph_evidence"]
    gc1, gc2 = st.columns(2)
    ge_dates = gc1.number_input("最多取几个证据日", 0, 10, int(ge["max_dates"]), 1, key="ge_dates",
                                help="0 = 关闭这条通路（图谱仍会贡献 entity-anchored / 多跳片段）")
    ge_frags = gc2.number_input("每个证据日捞几段", 1, 10, int(ge["fragments_per_date"]), 1, key="ge_frags")
    ge_win = st.number_input("证据片段父块扩展（比普通检索更宽，还原来龙去脉）", 0, 6, int(ge["window_expand"]), 1, key="ge_win")
    ge_sum = st.checkbox("附上该场结构化摘要（整场覆盖，便宜）", value=bool(ge["include_summary"]), key="ge_sum")

    b1, b2 = st.columns(2)
    if b1.button("保存", type="primary", use_container_width=True):
        index_settings.save(
            retrieval={"top_k": int(top_k), "window_expand": int(win)},
            chunking={"chunk_size": int(csz), "chunk_overlap": int(cov)},
            embedding={"model": e_model.strip(), "device": e_dev, "batch_size": int(e_batch), "use_fp16": bool(e_fp16)},
            fts={"base_tokenizer": f_tok.strip(), "ngram_min": int(f_min), "ngram_max": int(f_max)},
            reranker={"use_reranker": bool(rr_on), "rerank_top_k": int(rr_cand), "final_top_k": int(rr_final),
                      "model": rr_model.strip(), "device": rr_dev, "use_fp16": bool(rr_fp16)},
            graph_evidence={"max_dates": int(ge_dates), "fragments_per_date": int(ge_frags),
                            "window_expand": int(ge_win), "include_summary": bool(ge_sum)},
        )
        st.toast("已保存。检索 / reranker 开关及数量下次问答即生效；分块 / FTS 需重建；embedding / reranker 的 model·device·fp16 需重启")
        st.rerun()
    if b2.button("恢复默认参数", use_container_width=True):
        index_settings.reset()
        st.toast("已恢复索引默认参数")
        st.rerun()

    st.divider()
    st.markdown("##### 🔄 全量重建向量库")
    st.caption(
        "用当前分块 / FTS 参数，重新分块 + 向量化**全部**逐字稿并重建 LanceDB。数据量大时较慢，"
        "期间问答检索会受影响。改了分块或 FTS 参数、想对历史记录也生效时才需要。"
    )
    confirm = st.checkbox("我确认要全量重建", key="rebuild_confirm")
    if st.button("开始重建", disabled=not confirm, use_container_width=True):
        with st.spinner("正在重新分块 + 向量化全部逐字稿…（首次会加载 BGE-M3 模型，稍慢）"):
            from scripts.chunk import chunk_all, write_chunks_jsonl
            from scripts.ingest import ingest

            chunks = chunk_all()
            write_chunks_jsonl(chunks)
            table = ingest(mode="overwrite")
            index_records.append_change_record(
                "full_rebuild", "(全部)", n_chunks=table.count_rows(), note="UI 全量重建"
            )
        st.success(f"已重建，共 {table.count_rows()} 个片段")
        st.rerun()


def _switch_session(session_id: str):
    st.session_state.active_session_id = session_id
    st.session_state.messages = load_session(session_id)["messages"]


def _persist_current_session():
    messages = st.session_state.messages
    if not messages:
        return
    first_user_msg = next((m["content"] for m in messages if m["role"] == "user"), "新对话")
    save_session(st.session_state.active_session_id, make_title(first_user_msg), messages)


if "active_session_id" not in st.session_state:
    sessions = list_sessions()
    if sessions:
        _switch_session(sessions[0]["id"])
    else:
        st.session_state.active_session_id = new_session_id()
        st.session_state.messages = []

with st.sidebar:
    st.subheader("对话历史")
    if st.button("＋ 新对话", use_container_width=True):
        st.session_state.active_session_id = new_session_id()
        st.session_state.messages = []
        st.rerun()

    for s in list_sessions():
        col1, col2 = st.columns([5, 1])
        is_active = s["id"] == st.session_state.active_session_id
        if col1.button(("▶ " if is_active else "") + s["title"], key=f"switch_{s['id']}", use_container_width=True):
            _switch_session(s["id"])
            st.rerun()
        if col2.button("🗑️", key=f"delete_{s['id']}"):
            delete_session(s["id"])
            if is_active:
                remaining = list_sessions()
                if remaining:
                    _switch_session(remaining[0]["id"])
                else:
                    st.session_state.active_session_id = new_session_id()
                    st.session_state.messages = []
            st.rerun()

    st.divider()
    st.subheader("设置")
    show_sources = st.checkbox("显示本轮检索来源", value=True)
    show_tokens = st.checkbox("显示本轮 token 用量明细", value=True)

    st.divider()
    st.subheader("高级选项")
    use_full_history = st.checkbox(
        "深度模式（历史包含完整检索片段）",
        value=False,
        help="开启后，历史对话包含完整检索片段。通常不需要开启。\n"
             "注意：深度模式下 context 增长更快，可能更快达到 500K 上限。"
    )
    if st.button("⚙️ Gemini 设置（模型 / 参数 / API Key）"):
        gemini_settings_dialog()
    if st.button("⚙️ 索引设置（本地检索 / 分块 / 向量化）"):
        index_settings_dialog()
    if st.button("📚 已索引的咨询记录 / 变更记录"):
        indexed_records_dialog()
    if st.button("⚙️ 编辑 System Instruction"):
        system_instruction_dialog()
    if st.button("清空当前对话"):
        st.session_state.messages = []
        delete_session(st.session_state.active_session_id)
        st.rerun()

    st.divider()
    st.subheader("AI 对话记忆")
    st.caption("从所有聊天历史提炼的记忆，与真实咨询的长期记忆分开存放。")
    if CHAT_MEMORY_PATH.exists():
        st.caption(CHAT_MEMORY_PATH.read_text(encoding="utf-8").splitlines()[1])
    if st.button("🔄 更新 AI 对话记忆"):
        with st.spinner("正在汇总聊天历史 + 更新对话记忆心智地图…"):
            import json

            from config import CHAT_GRAPH_JSON_PATH
            from scripts.build_chat_graph import build_chat_graph
            from scripts.update_chat_memory import update_chat_memory

            update_chat_memory()
            chat_graph = build_chat_graph()
            CHAT_GRAPH_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
            CHAT_GRAPH_JSON_PATH.write_text(json.dumps(chat_graph, ensure_ascii=False, indent=2), encoding="utf-8")
        st.success("已更新")
        st.rerun()

if "messages" not in st.session_state:
    st.session_state.messages = []  # [{role, content, sources?, token_usage?}]


def _render_meta(sources: list[dict], token_usage: dict | None, matched_graph_nodes: list[dict] | None = None):
    if not (show_sources or show_tokens):
        return

    # 估算累积 context 大小
    total_history = 0
    for m in st.session_state.messages:
        if m["role"] == "user":
            if m.get("compressed"):
                total_history += len(m.get("content", ""))
            else:
                total_history += len(m.get("history_content", m.get("content", "")))
        else:
            total_history += len(m.get("content", ""))

    label_bits = []
    if show_tokens and token_usage:
        label_bits.append(f"共 {token_usage.get('total', 0)} tokens")
    if show_sources:
        label_bits.append(f"{len(sources)} 个来源片段")

    # Context 监控
    label_bits.append(f"累积 {total_history/1000:.0f}K 字符")
    if total_history > 450_000:
        label_bits.append("⚠️ 接近上限")

    with st.expander(" · ".join(label_bits) if label_bits else "详情"):
        if show_tokens and token_usage:
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("输入 input", token_usage.get("input", 0))
            c2.metric("输出 output", token_usage.get("output", 0))
            c3.metric("思考 thinking", token_usage.get("thinking", 0))
            c4.metric("缓存 cached", token_usage.get("cached", 0))
        if matched_graph_nodes:
            tags = "、".join(f"[{n['type']}] {n['label']}" for n in matched_graph_nodes)
            st.caption(f"🕸️ 心智地图命中节点：{tags}（已针对性补充相关片段）")
        if show_sources:
            for s in sources:
                if s.get("via_graph_evidence"):
                    tag = "🕸️🔍 片段（心智地图证据日）"
                elif s.get("via_graph"):  # 兼容旧会话：曾经的"整份逐字稿（心智地图关联）"
                    tag = "🕸️📄 完整逐字稿（心智地图关联）"
                elif s.get("full_transcript"):
                    tag = "📄 完整逐字稿"
                else:
                    tag = "🔍 片段"
                st.caption(f"{tag} ｜ {s['session_date']} ｜ {s['source_file']} ｜ {s['start_ts']}-{s['end_ts']}")


for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if msg["role"] == "assistant" and "sources" in msg:
            _render_meta(msg["sources"], msg.get("token_usage"), msg.get("matched_graph_nodes"))

if prompt := st.chat_input("说说你在想什么…"):
    with st.chat_message("user"):
        st.markdown(prompt)

    # history 传递给 answer()，包含必要的字段供压缩逻辑使用
    history = [
        {
            "role": m["role"],
            "content": m["content"],
            "api_content": m.get("api_content"),
            "history_content": m.get("history_content"),
            "compressed": m.get("compressed", False),
        }
        for m in st.session_state.messages
    ]

    with st.chat_message("assistant"):
        with st.spinner("正在检索历史咨询、组织回答…"):
            try:
                result = answer(prompt, history=history, use_full_history=use_full_history)
            except Exception as e:  # noqa: BLE001
                st.error(f"出错了：{e}")
                st.stop()

        # 显示压缩提示
        if result.get("compression_info") and result["compression_info"]["triggered"]:
            info = result["compression_info"]
            msg_parts = []
            if info.get("compressed_turns", 0) > 0:
                msg_parts.append(f"用户消息 {info['compressed_turns']} 轮（检索片段 → 纯问题）")
            if info.get("llm_compressed_turns", 0) > 0:
                msg_parts.append(f"AI 回答 {info['llm_compressed_turns']} 轮（LLM 智能压缩）")

            st.info(
                f"📦 自动压缩：为保持在 500K context 以内\n"
                f"已压缩：{' + '.join(msg_parts)}\n"
                f"（{info['before']/1000:.0f}K → {info['after']/1000:.0f}K，节省 {(info['before']-info['after'])/1000:.0f}K）"
            )

        st.markdown(result["answer"])
        _render_meta(result["sources"], result.get("token_usage"), result.get("matched_graph_nodes"))

    # 同步压缩标记和压缩内容的变化回 session_state
    if result.get("compression_info"):
        for i, h in enumerate(history):
            if i < len(st.session_state.messages):
                if h.get("compressed"):
                    st.session_state.messages[i]["compressed"] = True
                if h.get("compressed_content"):
                    st.session_state.messages[i]["compressed_content"] = h["compressed_content"]

    st.session_state.messages.append({
        "role": "user",
        "content": prompt,
        "api_content": result.get("api_content"),
        "history_content": result.get("history_content"),
    })
    st.session_state.messages.append(
        {
            "role": "assistant",
            "content": result["answer"],
            "sources": result["sources"],
            "token_usage": result.get("token_usage"),
            "matched_graph_nodes": result.get("matched_graph_nodes"),
        }
    )
    _persist_current_session()
