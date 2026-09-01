"""pytest 配置和共享 fixtures。

参考：
- LlamaIndex: prompt_type 驱动的 mock LLM
- LangChain: 单元/集成分层
- LanceDB: tmpdir + monkeypatch 隔离
"""
import json
import os
import shutil
from pathlib import Path
from typing import Generator
from unittest.mock import MagicMock, patch

import lancedb
import pytest

from scripts import index_settings as index_settings_module
from scripts import llm as llm_module
from scripts import settings as settings_module
from scripts import workspace_manager


# ── 环境隔离 ──────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def mock_env_vars(monkeypatch):
    """自动 mock 所有环境变量（避免真实 API 调用）。"""
    monkeypatch.setenv("GEMINI_API_KEY", "fake-test-key-" + "a" * 32)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-test-key-" + "x" * 32)
    # 禁用 Streamlit email 提示
    monkeypatch.setenv("STREAMLIT_EMAIL", "")


# ── 硬隔离：三道闸门，任何测试都不可能污染真实数据 / 打真实 API ──────────────
#
# 这三个 fixture 是 autouse 且放在 conftest 顶层的，因为「让每个测试自己记得隔离」
# 已经被证明不可靠：tests/integration/ 里 13 个测试都以为自己隔离了，实际全都在写
# 真实的 private.nosync/workspaces/（详见下面 isolate_data_root 的说明）。
# 失败可见性 > 靠自觉：写错 patch 目标时应该立刻响，而不是静默污染。


@pytest.fixture(autouse=True)
def isolate_data_root(tmp_path, monkeypatch):
    """把 workspace 根目录钉在 tmp_path，测试写不进真实 private.nosync/。

    为什么必须在这里做，而不是每个测试自己 patch：
    `workspace_manager.WORKSPACES_ROOT = PRIVATE_DIR / "workspaces"` 是 **import-time
    求值的模块常量**，所以 `patch("scripts.workspace_manager.PRIVATE_DIR", tmp_path)`
    对它完全无效——WORKSPACES_ROOT 早就指向真实目录了。历史上集成测试就是这么把
    ws1/ws2/test-ws/test-workspace 建到真实 private.nosync/workspaces/ 里去的
    （test-workspace 甚至被误提交进 git），下一轮再跑就撞
    `ValueError: Workspace already exists`。这里直接钉住两个名字，从根上切断。
    """
    private = tmp_path / "private.nosync"
    workspaces = private / "workspaces"
    workspaces.mkdir(parents=True)
    monkeypatch.setattr(workspace_manager, "PRIVATE_DIR", private)
    monkeypatch.setattr(workspace_manager, "WORKSPACES_ROOT", workspaces)
    # workspace 的选择来源也清干净：set_current_workspace() 在非 Streamlit 环境写的是
    # 这个环境变量，不清会跨测试泄漏。
    monkeypatch.delenv("CURRENT_WORKSPACE", raising=False)
    # st.session_state 是**进程级**的：Streamlit bare 模式下 st.stop() 是 no-op，但
    # session_state 赋值是真的会写进去（SafeSessionState 的降级实现）。get_current_workspace()
    # 第一优先级读的就是它，所以上一个测试调过 set_current_workspace("ws1") 之后，
    # 后面每个测试都会以为自己在 ws1 里——而 ws1 在它自己的 tmp_path 里早就没了。
    _clear_streamlit_session_state()

    # 全局设置文件也钉进 tmp_path。这两个路径原本指向真实 private.nosync/：
    # settings.save(api_key=...) 会覆盖使用者真实的 API key，
    # index_settings.save(...) 会覆盖使用者调好的检索参数。测试没有理由碰它们。
    # 这两个名字都是 import-time 绑进各自模块 namespace 的，所以要按模块 patch。
    monkeypatch.setattr(index_settings_module, "INDEX_SETTINGS_PATH",
                        private / "index_settings.json")
    monkeypatch.setattr(settings_module, "GEMINI_SETTINGS_PATH",
                        private / "gemini_settings.json")
    monkeypatch.setattr(settings_module, "ENV_PATH", private / ".env")

    # 预置一个 workspace，让隔离后的基线状态和生产一致（生产里永远至少有一个）。
    # 不这么做的话，"0 个 workspace" 会变成默认状态，而 app.py 对这种情况的处理依赖
    # st.stop()——它在 bare 模式（测试里没有 ScriptRunContext）**不会真的中断执行**，
    # 于是 import app 会一路跌到 ws_names[0] 抛 IndexError。测过真实环境的代码不该被
    # 一个测试专属的状态判死。需要"空 workspace 列表"的测试用 empty_workspaces_root。
    _seed_workspace(workspaces / DEFAULT_TEST_WORKSPACE, DEFAULT_TEST_WORKSPACE)
    yield private
    _clear_streamlit_session_state()


DEFAULT_TEST_WORKSPACE = "default-ws"


def _clear_streamlit_session_state() -> None:
    """清空 st.session_state（进程级，跨测试泄漏的唯一来源之一）。

    bare 模式下 session_state 的行为随 Streamlit 版本变化（有版本会在无 ScriptRunContext 时
    抛异常），所以整段包在 try 里——清不掉不该让测试失败。
    """
    try:
        import streamlit as st

        for key in list(st.session_state.keys()):
            del st.session_state[key]
    except Exception:
        pass


def _seed_workspace(ws_dir: Path, name: str) -> Path:
    """铺一个最小可用的 workspace 目录结构。

    故意不调用 create_workspace()——fixture 不该依赖被测函数的行为（那样 create_workspace
    一坏，所有测试跟着一起挂，失败原因还指不到点上）。
    """
    for sub in ("data/raw", "data/processed", "data/summaries",
                "data/graph_fragments", "data/chat_sessions", "db"):
        (ws_dir / sub).mkdir(parents=True, exist_ok=True)
    (ws_dir / ".workspace_config.json").write_text(
        json.dumps({
            "name": name,
            "display_name": f"测试用 {name}",
            "description": "conftest 预置的测试 workspace",
            "domain": "generic",
            "graph_schema": {"mode": "generic"},
            "persona": {
                "system_instruction_file": None,
                "ai_name": "测试助手",
                "context_role": "助手",
            },
            "chunk_prefix_template": "[{session_date} 测试｜发言人：{speakers}｜时间段：{start_ts}–{end_ts}]",
            "domain_label": "测试",
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    (ws_dir / "LONG_TERM_MEMORY.md").write_text("", encoding="utf-8")
    (ws_dir / "CHAT_MEMORY.md").write_text("", encoding="utf-8")
    return ws_dir


@pytest.fixture
def empty_workspaces_root(isolate_data_root):
    """清掉预置 workspace，得到一个空的 workspaces 根目录。

    给那些要断言「一共有几个 workspace」或「没有任何 workspace 时怎么降级」的测试用。
    """
    root = workspace_manager.WORKSPACES_ROOT
    shutil.rmtree(root)
    root.mkdir(parents=True)
    return root


@pytest.fixture(autouse=True)
def block_real_llm_calls(monkeypatch):
    """堵死真实 LLM 调用；漏 mock 时大声报错，而不是静默出网。

    历史事故：测试 patch 的是 `scripts.llm.ask_llm`，但 ask.py / summarize.py /
    session_graph.py 全都是 `from scripts.llm import ask_llm`——名字已经绑进各自的
    namespace，patch 源模块拦不到任何东西。于是那些"mock 了 LLM"的测试其实在打真实
    API，`test_max_context_length` 还真把 100 轮 × 1 万字的历史发出去，撞出
    `openai.APIStatusError: Maximum request body size 1048576 exceeded`。

    堵在两个 SDK 的 client 构造函数和 Copilot CLI 的进程入口上（也是
    tests/unit/test_llm.py 的 mock 层级），
    真的需要走这条路的测试自己 patch 同名目标即可——内层 patch 生效，退出时恢复成这里
    的闸门。
    """
    def _blocked(*args, **kwargs):
        raise AssertionError(
            "测试里发生了真实 LLM 调用。要 mock 的是调用方 namespace 里的名字"
            "（如 scripts.ask.ask_llm / scripts.summarize.ask_llm），"
            "不是 scripts.llm.ask_llm。"
        )

    monkeypatch.setattr("scripts.llm.genai.Client", _blocked)
    monkeypatch.setattr("openai.OpenAI", _blocked)
    monkeypatch.setattr(llm_module, "run_subprocess", _blocked)
    # client 是模块级缓存，不清会跨测试泄漏（上一个测试的 mock client 被下一个复用，
    # 于是"没 mock"的测试反而莫名其妙地通过）。
    monkeypatch.setattr(llm_module, "_client", None)
    monkeypatch.setattr(llm_module, "_client_key", None)
    monkeypatch.setattr(llm_module, "_openai_clients", {})


@pytest.fixture
def isolated_workspace(tmp_path, monkeypatch) -> Generator[Path, None, None]:
    """为每个测试创建独立的 workspace 环境。

    使用 tmp_path + monkeypatch.chdir() 双重隔离：
    - tmp_path: pytest 自动清理的临时目录
    - chdir: 改变当前工作目录，避免污染真实数据

    参考：LanceDB 的 autouse fixture
    """
    workspace_dir = tmp_path / "test_workspace"
    workspace_dir.mkdir()

    # 创建必要的子目录
    (workspace_dir / "data" / "raw").mkdir(parents=True)
    (workspace_dir / "data" / "processed").mkdir(parents=True)
    (workspace_dir / "data" / "summaries").mkdir(parents=True)
    (workspace_dir / "data" / "graph_fragments").mkdir(parents=True)
    (workspace_dir / "data" / "chat_sessions").mkdir(parents=True)
    (workspace_dir / "db").mkdir(parents=True)

    # 切换工作目录
    original_cwd = Path.cwd()
    monkeypatch.chdir(workspace_dir)

    yield workspace_dir

    # pytest 会自动清理 tmp_path，无需手动删除
    monkeypatch.chdir(original_cwd)


# ── LLM Mock ──────────────────────────────────────────────────────────────


@pytest.fixture
def mock_gemini():
    """Mock Google Gemini API 调用。

    参考：LlamaIndex 的 mock_llm fixture
    """
    with patch("google.genai.Client") as mock_client:
        # Mock response
        mock_response = MagicMock()
        mock_response.text = "Mocked LLM response"
        mock_response.usage_metadata.prompt_token_count = 100
        mock_response.usage_metadata.candidates_token_count = 50
        mock_response.usage_metadata.total_token_count = 150
        mock_response.usage_metadata.cached_content_token_count = 0
        mock_response.usage_metadata.thoughts_token_count = 0

        # Mock client
        mock_client.return_value.models.generate_content.return_value = mock_response

        yield mock_client


@pytest.fixture
def mock_embedder():
    """Mock BGE-M3 embeddings。

    返回固定维度的随机向量，避免下载模型。
    """
    with patch("scripts.embedder.embed") as mock_embed, \
         patch("scripts.embedder.embed_one") as mock_embed_one:

        import numpy as np

        # embed() 返回批量结果
        def fake_embed(texts):
            return {
                "dense_vecs": np.random.rand(len(texts), 1024).astype(np.float32)
            }

        # embed_one() 返回单个向量
        def fake_embed_one(text):
            return np.random.rand(1024).astype(np.float32)

        mock_embed.side_effect = fake_embed
        mock_embed_one.side_effect = fake_embed_one

        yield {"embed": mock_embed, "embed_one": mock_embed_one}


# ── 数据库 Mock ───────────────────────────────────────────────────────────


@pytest.fixture
def test_lancedb(tmp_path) -> Generator[lancedb.DBConnection, None, None]:
    """创建测试用的 LanceDB 实例。

    每个测试独立 DB，自动清理。
    参考：LanceDB 官方测试
    """
    db_path = tmp_path / "test.lancedb"
    db = lancedb.connect(str(db_path))
    yield db
    # pytest 自动清理 tmp_path


# ── 测试数据 ──────────────────────────────────────────────────────────────


@pytest.fixture
def sample_transcript() -> str:
    """示例逐字稿内容（最小可解析）。

    ⚠️ 格式必须是 `发言人(HH:MM:SS): 文本`，对应 config.TRANSCRIPT_LINE_RE。
    曾经写成 `[00:00:15] Andy: …`（口语化的 SRT 风格），parse_transcript() 一条都匹配不上，
    于是 utterances 为空、chunk_session() 返回 []，三个集成测试全挂在"分块结果为空"上，
    而报错信息完全指不到"逐字稿格式不对"。fixture 数据也要跟着真实格式走。
    """
    return """20240101120000_咨询记录.txt

Andy(00:00:15): 最近工作压力很大，感觉喘不过气。
咨询师(00:01:30): 能具体说说是什么让你感到压力吗？
Andy(00:02:45): 项目截止日期快到了，但进度落后很多，每天加班到深夜还是做不完。
咨询师(00:03:20): 听起来你已经很努力了，这种"怎么做都不够"的感觉持续多久了？
Andy(00:04:05): 大概三个月吧，从上个项目开始就一直这样，睡也睡不好。
"""


@pytest.fixture
def sample_workspace_config() -> dict:
    """示例 workspace 配置。"""
    return {
        "name": "test-workspace",
        "display_name": "测试工作空间",
        "domain": "counseling",
        "graph_schema": {
            "mode": "predefined",
            "schema_file": "counseling.json"
        },
        "persona": {
            "system_instruction_file": None,
            "ai_name": "测试助手",
            "context_role": "测试咨询师"
        },
        "chunk_prefix_template": "[{session_date} 测试]",
        "domain_label": "测试",
    }


# ── 集成测试门控 ──────────────────────────────────────────────────────────


def pytest_addoption(parser):
    """添加 --integration 命令行选项。

    参考：LlamaIndex 的测试分离策略
    """
    parser.addoption(
        "--integration",
        action="store_true",
        default=False,
        help="运行集成测试（需要真实 API 或较长时间）"
    )


def pytest_configure(config):
    """注册 integration marker。"""
    config.addinivalue_line(
        "markers", "integration: 集成测试（使用 --integration 启用）"
    )


def pytest_collection_modifyitems(config, items):
    """自动跳过集成测试（除非指定 --integration）。"""
    if config.getoption("--integration"):
        return  # 运行所有测试

    skip_integration = pytest.mark.skip(reason="需要 --integration flag")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip_integration)
