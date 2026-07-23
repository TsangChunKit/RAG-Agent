"""Graph Schema 加载器：支持多种 schema 模式（预定义 / 通用 / 自定义），含降级逻辑。

Schema 文件存放在 scripts/graph_schemas/ 目录下，每个 schema 定义节点类型、关系类型、
system instruction 模板等。

降级路径：custom → generic → 硬编码（graph_utils.NODE_TYPES）
"""
from typing import Optional

import json
from pathlib import Path
from typing import Dict, Optional

from scripts.workspace_manager import get_workspace_dir, load_workspace_config


# ── Schema 路径 ─────────────────────────────────────────────────────────

GRAPH_SCHEMAS_DIR = Path(__file__).parent / "graph_schemas"


# ── Schema 加载 ─────────────────────────────────────────────────────────

def load_schema(workspace_id: Optional[str] = None) -> Dict:
    """加载 workspace 的 graph schema，支持多级降级。

    Args:
        workspace_id: workspace 名称，None 表示使用当前 workspace

    Returns:
        Dict: schema 定义，包含 node_types / relation_types / system_instruction_template 等

    降级路径：
        1. workspace config 指定的 schema
        2. generic.json
        3. 硬编码（graph_utils.NODE_TYPES）
    """
    try:
        # 加载 workspace config
        config = load_workspace_config(workspace_id)
        graph_schema_config = config.get("graph_schema", {})
        schema_mode = graph_schema_config.get("mode", "generic")

        # 模式 1：预定义 schema
        if schema_mode == "predefined":
            schema_file = graph_schema_config.get("schema_file")
            if not schema_file:
                raise ValueError("predefined mode requires schema_file")

            schema_path = GRAPH_SCHEMAS_DIR / schema_file
            if not schema_path.exists():
                raise FileNotFoundError(f"Schema file not found: {schema_file}")

            return json.loads(schema_path.read_text(encoding="utf-8"))

        # 模式 2：通用 schema
        elif schema_mode == "generic":
            custom_prompt = graph_schema_config.get("custom_prompt")
            schema = load_generic_schema()

            # 可选：覆盖 system_instruction_template
            if custom_prompt:
                schema["system_instruction_template"] = custom_prompt

            return schema

        # 模式 3：自定义 schema
        elif schema_mode == "custom":
            custom_file = graph_schema_config.get("schema_file")
            if not custom_file:
                raise ValueError("custom mode requires schema_file")

            custom_path = get_workspace_dir(workspace_id) / custom_file
            if not custom_path.exists():
                raise FileNotFoundError(f"Custom schema file not found: {custom_file}")

            return json.loads(custom_path.read_text(encoding="utf-8"))

        else:
            raise ValueError(f"Unknown schema mode: {schema_mode}")

    except Exception as e:
        # 降级 1：generic schema
        try:
            print(f"⚠️  加载 schema 失败，降级到 generic: {e}")
            return load_generic_schema()
        except Exception as e2:
            # 降级 2：硬编码
            print(f"⚠️  generic schema 也失败，降级到硬编码: {e2}")
            return load_hardcoded_schema()


def load_generic_schema() -> Dict:
    """加载通用 schema（generic.json）。"""
    schema_path = GRAPH_SCHEMAS_DIR / "generic.json"
    if not schema_path.exists():
        raise FileNotFoundError("generic.json not found")

    return json.loads(schema_path.read_text(encoding="utf-8"))


def load_hardcoded_schema() -> Dict:
    """降级到硬编码 schema（从 graph_utils.py 读取）。"""
    from scripts.graph_utils import NODE_TYPES, RELATION_TYPES, MERGE_SIM_THRESHOLD

    return {
        "version": "1.0",
        "domain": "hardcoded",
        "node_types": NODE_TYPES,
        "relation_types": RELATION_TYPES,
        "merge_threshold": MERGE_SIM_THRESHOLD,
        "system_instruction_template": None,  # 使用 session_graph.py 的默认 prompt
    }


# ── Schema 字段提取 ─────────────────────────────────────────────────────

def get_node_types(schema: Dict) -> Dict:
    """从 schema 提取节点类型定义。

    Args:
        schema: load_schema() 返回的 schema

    Returns:
        Dict: {type_name: {label, layer, has_domain}}
    """
    return schema.get("node_types", {})


def get_relation_types(schema: Dict) -> Dict:
    """从 schema 提取关系类型定义。

    Args:
        schema: load_schema() 返回的 schema

    Returns:
        Dict: {relation_type: label}
    """
    return schema.get("relation_types", {})


def get_merge_threshold(schema: Dict) -> float:
    """从 schema 提取归并阈值。

    Args:
        schema: load_schema() 返回的 schema

    Returns:
        float: 语义相似度阈值（默认 0.80）
    """
    return schema.get("merge_threshold", 0.80)


def get_system_instruction(schema: Dict) -> Optional[str]:
    """从 schema 提取 system instruction 模板。

    Args:
        schema: load_schema() 返回的 schema

    Returns:
        Optional[str]: prompt 模板，None 表示使用默认
    """
    return schema.get("system_instruction_template")


def get_schema_domains(schema: Dict) -> Optional[list]:
    """从 schema 提取 schema_domains（仅心理学 schema 有）。

    Args:
        schema: load_schema() 返回的 schema

    Returns:
        Optional[list]: schema domains 列表（如五大图式领域）
    """
    return schema.get("schema_domains")


# ── 辅助函数 ────────────────────────────────────────────────────────────

def list_available_schemas() -> list:
    """列举 scripts/graph_schemas/ 下的所有 schema 文件。

    Returns:
        list: schema 文件名列表（不含路径）
    """
    if not GRAPH_SCHEMAS_DIR.exists():
        return []

    return [f.name for f in GRAPH_SCHEMAS_DIR.glob("*.json")]


def validate_schema(schema: Dict) -> bool:
    """验证 schema 结构是否有效。

    Args:
        schema: schema 定义

    Returns:
        bool: 是否有效
    """
    required_fields = ["node_types", "relation_types"]
    for field in required_fields:
        if field not in schema:
            return False

        if not isinstance(schema[field], dict):
            return False

        if not schema[field]:  # 不能为空
            return False

    return True
