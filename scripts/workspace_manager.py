"""Workspace 管理：多领域隔离的核心模块。

负责 workspace 的创建、切换、配置加载、验证等。支持向后兼容（旧路径自动视为 _legacy workspace）。
"""
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

# 避免循环依赖：直接从 config 读取 BASE_DIR 和 PRIVATE_DIR
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from config import BASE_DIR, PRIVATE_DIR


# ── Workspace 路径管理 ──────────────────────────────────────────────────

WORKSPACES_ROOT = PRIVATE_DIR / "workspaces"


def get_current_workspace() -> str:
    """获取当前 workspace，支持多种来源（优先级递减）。

    Returns:
        workspace_id (str): "_legacy" 表示旧路径兼容模式
    """
    # 1. Streamlit session_state（UI 选择器）
    try:
        import streamlit as st
        if hasattr(st, "session_state") and "current_workspace" in st.session_state:
            return st.session_state.current_workspace
    except ImportError:
        pass  # 非 Streamlit 环境

    # 2. 环境变量
    if os.getenv("CURRENT_WORKSPACE"):
        return os.getenv("CURRENT_WORKSPACE")

    # 3. 向后兼容：检测是否有旧路径数据
    old_data_dir = PRIVATE_DIR / "data"
    if old_data_dir.exists() and not WORKSPACES_ROOT.exists():
        return "_legacy"  # 旧路径模式

    # 4. 默认 workspace（新路径）
    if WORKSPACES_ROOT.exists() and list(WORKSPACES_ROOT.iterdir()):
        # 返回第一个 workspace（按字母排序）
        workspaces = sorted([d.name for d in WORKSPACES_ROOT.iterdir() if d.is_dir()])
        return workspaces[0] if workspaces else "_legacy"

    return "_legacy"


def set_current_workspace(workspace_id: str):
    """切换当前 workspace（写入 Streamlit session_state）。

    Args:
        workspace_id: workspace 名称
    """
    try:
        import streamlit as st
        st.session_state.current_workspace = workspace_id
    except ImportError:
        # 非 Streamlit 环境，使用环境变量
        os.environ["CURRENT_WORKSPACE"] = workspace_id


def get_workspace_dir(workspace_id: Optional[str] = None) -> Path:
    """获取 workspace 根目录，支持向后兼容。

    Args:
        workspace_id: workspace 名称，None 表示使用当前 workspace

    Returns:
        Path: workspace 根目录

    Raises:
        ValueError: workspace 不存在
    """
    if workspace_id is None:
        workspace_id = get_current_workspace()

    # 新路径
    if workspace_id != "_legacy":
        new_path = WORKSPACES_ROOT / workspace_id
        if new_path.exists():
            return new_path

        # workspace 不存在时，检查是否应该降级到 _legacy
        if not WORKSPACES_ROOT.exists():
            return PRIVATE_DIR  # 向后兼容

        raise ValueError(f"Workspace not found: {workspace_id}")

    # 旧路径兜底（向后兼容）
    return PRIVATE_DIR


# ── Workspace 配置管理 ──────────────────────────────────────────────────

DEFAULT_WORKSPACE_CONFIG = {
    "name": "_legacy",
    "display_name": "默认工作空间",
    "description": "向后兼容的默认 workspace",
    "domain": "counseling",
    "graph_schema": {
        "mode": "predefined",
        "schema_file": "counseling.json"
    },
    "persona": {
        "system_instruction_file": None,  # None 表示使用根目录的 system_instruction.md
        "ai_name": "AI 心理咨询助手",
        "context_role": "咨询师海特"
    },
    "chunk_prefix_template": "[{session_date} {domain_label}｜发言人：{speakers}｜时间段：{start_ts}–{end_ts}]",
    "domain_label": "咨询",
}


def load_workspace_config(workspace_id: Optional[str] = None) -> Dict:
    """加载 workspace 配置，支持降级到默认配置。

    Args:
        workspace_id: workspace 名称，None 表示使用当前 workspace

    Returns:
        Dict: workspace 配置
    """
    if workspace_id is None:
        workspace_id = get_current_workspace()

    # _legacy workspace 使用默认配置
    if workspace_id == "_legacy":
        return DEFAULT_WORKSPACE_CONFIG.copy()

    # 读取 workspace config 文件
    config_path = get_workspace_dir(workspace_id) / ".workspace_config.json"
    if config_path.exists():
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
            # 合并默认配置（确保所有字段都存在）
            merged = DEFAULT_WORKSPACE_CONFIG.copy()
            merged.update(config)
            return merged
        except Exception as e:
            print(f"⚠️  加载 workspace config 失败，使用默认配置: {e}")

    # 降级到默认配置
    return DEFAULT_WORKSPACE_CONFIG.copy()


def save_workspace_config(workspace_id: str, config: Dict):
    """保存 workspace 配置。

    Args:
        workspace_id: workspace 名称
        config: workspace 配置
    """
    if workspace_id == "_legacy":
        raise ValueError("Cannot save config for _legacy workspace")

    ws_dir = get_workspace_dir(workspace_id)
    config_path = ws_dir / ".workspace_config.json"
    config_path.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")


# ── Workspace 列举与验证 ─────────────────────────────────────────────────

def list_workspaces() -> List[Dict]:
    """列举所有 workspace。

    Returns:
        List[Dict]: workspace 信息列表，每项包含 name / display_name / created_at
    """
    workspaces = []

    # 1. _legacy workspace（如果存在旧路径数据）
    old_data_dir = PRIVATE_DIR / "data"
    if old_data_dir.exists() and not WORKSPACES_ROOT.exists():
        workspaces.append({
            "name": "_legacy",
            "display_name": "默认工作空间（兼容模式）",
            "created_at": None,
        })

    # 2. 新路径 workspaces
    if WORKSPACES_ROOT.exists():
        for ws_dir in WORKSPACES_ROOT.iterdir():
            if not ws_dir.is_dir():
                continue

            config = load_workspace_config(ws_dir.name)
            workspaces.append({
                "name": ws_dir.name,
                "display_name": config.get("display_name", ws_dir.name),
                "created_at": config.get("created_at"),
            })

    return sorted(workspaces, key=lambda x: x["name"])


def validate_workspace(workspace_id: str) -> bool:
    """验证 workspace 目录结构是否完整。

    Args:
        workspace_id: workspace 名称

    Returns:
        bool: 是否有效
    """
    try:
        ws_dir = get_workspace_dir(workspace_id)

        # _legacy workspace 只需验证旧路径存在
        if workspace_id == "_legacy":
            return (ws_dir / "data").exists()

        # 新 workspace 验证必要目录
        required_dirs = ["data", "data/raw", "db"]
        for dir_path in required_dirs:
            if not (ws_dir / dir_path).exists():
                return False

        return True
    except Exception:
        return False


# ── Workspace 创建 ──────────────────────────────────────────────────────

def create_workspace(
    name: str,
    display_name: str,
    domain: str,
    graph_schema_mode: str = "generic",
    schema_file: Optional[str] = None,
) -> Path:
    """创建新 workspace。

    Args:
        name: workspace 名称（英文，用于目录名）
        display_name: 显示名称（中文）
        domain: 领域（counseling / sutras / solution_arch / generic）
        graph_schema_mode: schema 模式（predefined / generic / custom）
        schema_file: schema 文件名（mode=predefined 时必填）

    Returns:
        Path: workspace 根目录

    Raises:
        ValueError: workspace 已存在或参数无效
    """
    if name == "_legacy":
        raise ValueError("Cannot create workspace named '_legacy' (reserved)")

    ws_dir = WORKSPACES_ROOT / name
    if ws_dir.exists():
        raise ValueError(f"Workspace already exists: {name}")

    # 创建目录结构
    ws_dir.mkdir(parents=True, exist_ok=True)
    (ws_dir / "data" / "raw").mkdir(parents=True, exist_ok=True)
    (ws_dir / "data" / "processed").mkdir(parents=True, exist_ok=True)
    (ws_dir / "data" / "summaries").mkdir(parents=True, exist_ok=True)
    (ws_dir / "data" / "graph_fragments").mkdir(parents=True, exist_ok=True)
    (ws_dir / "data" / "chat_sessions").mkdir(parents=True, exist_ok=True)
    (ws_dir / "db").mkdir(parents=True, exist_ok=True)

    # 创建配置文件
    config = {
        "name": name,
        "display_name": display_name,
        "description": f"{display_name} 的知识库",
        "domain": domain,
        "graph_schema": {
            "mode": graph_schema_mode,
        },
        "persona": {
            "system_instruction_file": "system_instruction.md",
            "ai_name": f"{display_name} AI 助手",
            "context_role": "助手"
        },
        "chunk_prefix_template": "[{session_date} {domain_label}｜发言人：{speakers}｜时间段：{start_ts}–{end_ts}]",
        "domain_label": display_name,
        "created_at": datetime.now().isoformat(),
    }

    if graph_schema_mode == "predefined":
        if not schema_file:
            raise ValueError("schema_file is required for predefined mode")
        config["graph_schema"]["schema_file"] = schema_file
    elif graph_schema_mode == "custom":
        if not schema_file:
            raise ValueError("schema_file is required for custom mode")
        config["graph_schema"]["schema_file"] = schema_file

    save_workspace_config(name, config)

    # 创建空的记忆文件
    (ws_dir / "LONG_TERM_MEMORY.md").write_text("", encoding="utf-8")
    (ws_dir / "CHAT_MEMORY.md").write_text("", encoding="utf-8")

    # 创建默认 system_instruction.md（如果需要）
    if config["persona"]["system_instruction_file"]:
        si_path = ws_dir / config["persona"]["system_instruction_file"]
        si_path.write_text(
            f"你是 {display_name} 领域的 AI 助手，擅长回答相关问题。",
            encoding="utf-8"
        )

    print(f"✅ Workspace 创建成功: {name} ({display_name})")
    return ws_dir


# ── 辅助函数 ────────────────────────────────────────────────────────────

def get_workspace_stat(workspace_id: Optional[str] = None) -> Dict:
    """获取 workspace 统计信息。

    Args:
        workspace_id: workspace 名称，None 表示使用当前 workspace

    Returns:
        Dict: 包含 doc_count / graph_exists / last_indexed_at 等
    """
    if workspace_id is None:
        workspace_id = get_current_workspace()

    ws_dir = get_workspace_dir(workspace_id)
    data_dir = ws_dir / "data"

    stat = {
        "doc_count": 0,
        "graph_exists": False,
        "chat_graph_exists": False,
        "last_indexed_at": None,
    }

    # 统计文档数
    raw_dir = data_dir / "raw"
    if raw_dir.exists():
        stat["doc_count"] = len(list(raw_dir.glob("*.txt")))

    # 检查图谱
    graph_path = data_dir / "graph.json"
    stat["graph_exists"] = graph_path.exists()

    chat_graph_path = data_dir / "chat_graph.json"
    stat["chat_graph_exists"] = chat_graph_path.exists()

    # 读取最后索引时间（从 index_changelog.jsonl）
    changelog_path = data_dir / "index_changelog.jsonl"
    if changelog_path.exists():
        try:
            lines = changelog_path.read_text(encoding="utf-8").strip().split("\n")
            if lines and lines[-1]:
                last_record = json.loads(lines[-1])
                stat["last_indexed_at"] = last_record.get("timestamp")
        except Exception:
            pass

    return stat
