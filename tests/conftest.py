"""pytest 配置和共享 fixtures。

参考：
- LlamaIndex: prompt_type 驱动的 mock LLM
- LangChain: 单元/集成分层
- LanceDB: tmpdir + monkeypatch 隔离
"""
import os
from pathlib import Path
from typing import Generator
from unittest.mock import MagicMock, patch

import lancedb
import pytest


# ── 环境隔离 ──────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def mock_env_vars(monkeypatch):
    """自动 mock 所有环境变量（避免真实 API 调用）。"""
    monkeypatch.setenv("GEMINI_API_KEY", "fake-test-key-" + "a" * 32)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-test-key-" + "x" * 32)
    # 禁用 Streamlit email 提示
    monkeypatch.setenv("STREAMLIT_EMAIL", "")


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
    """示例逐字稿内容（最小可解析）。"""
    return """20240101120000_咨询记录.txt

[00:00:15] Andy: 最近工作压力很大，感觉喘不过气。
[00:01:30] 咨询师: 能具体说说是什么让你感到压力吗？
[00:02:45] Andy: 项目截止日期快到了，但进度落后很多。
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
