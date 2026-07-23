"""测试 graph schema 加载和降级。"""
import json
from pathlib import Path

import pytest

from scripts.graph_schema_loader import (
    get_node_types,
    get_relation_types,
    list_available_schemas,
    load_schema,
    validate_schema,
)


class TestSchemaLoader:
    """Schema 加载测试。"""

    def test_load_predefined_schema(self, isolated_workspace, monkeypatch):
        """测试加载预定义 schema。"""
        # Mock workspace config
        monkeypatch.setattr("scripts.graph_schema_loader.load_workspace_config",
                           lambda x: {
                               "graph_schema": {
                                   "mode": "predefined",
                                   "schema_file": "generic.json"
                               }
                           })

        schema = load_schema("_legacy")

        assert "node_types" in schema
        assert "relation_types" in schema
        assert get_node_types(schema) is not None

    def test_load_schema_degradation(self, isolated_workspace, monkeypatch):
        """测试 schema 降级（custom → generic → hardcoded）。"""
        # Mock 不存在的 custom schema
        monkeypatch.setattr("scripts.graph_schema_loader.load_workspace_config",
                           lambda x: {
                               "graph_schema": {
                                   "mode": "custom",
                                   "schema_file": "nonexistent.json"
                               }
                           })

        # 应该降级到 generic
        schema = load_schema("_legacy")

        # 验证是降级后的 schema
        assert schema is not None
        assert "node_types" in schema

    def test_get_node_types(self):
        """测试获取节点类型。"""
        schema = {
            "node_types": {
                "concept": {"label": "概念", "layer": 0},
                "entity": {"label": "实体", "layer": 1}
            }
        }

        node_types = get_node_types(schema)

        assert "concept" in node_types
        assert node_types["concept"]["label"] == "概念"

    def test_get_relation_types(self):
        """测试获取关系类型。"""
        schema = {
            "relation_types": {
                "relates_to": "关联",
                "causes": "导致"
            }
        }

        relation_types = get_relation_types(schema)

        assert "relates_to" in relation_types
        assert relation_types["relates_to"] == "关联"

    def test_list_available_schemas(self, monkeypatch):
        """测试列举可用 schemas。"""
        # Mock GRAPH_SCHEMAS_DIR
        fake_schema_dir = Path("/fake/schemas")
        monkeypatch.setattr("scripts.graph_schema_loader.GRAPH_SCHEMAS_DIR", fake_schema_dir)

        # Mock glob 返回
        def fake_glob(pattern):
            return [
                fake_schema_dir / "counseling.json",
                fake_schema_dir / "generic.json",
            ]

        monkeypatch.setattr(Path, "glob", fake_glob)

        schemas = list_available_schemas()

        # 注意：实际实现可能需要调整
        # 这里只是示例测试结构


class TestSchemaValidation:
    """Schema 验证测试。"""

    def test_validate_schema_valid(self):
        """测试有效 schema。"""
        schema = {
            "node_types": {
                "concept": {"label": "概念", "layer": 0, "has_domain": False}
            },
            "relation_types": {
                "relates_to": "关联"
            }
        }

        assert validate_schema(schema) is True

    def test_validate_schema_missing_fields(self):
        """测试缺少必要字段的 schema。"""
        schema = {
            "node_types": {}
            # 缺少 relation_types
        }

        assert validate_schema(schema) is False

    def test_validate_schema_invalid_structure(self):
        """测试无效结构。"""
        schema = {
            "node_types": "invalid",  # 应该是 dict
            "relation_types": {}
        }

        assert validate_schema(schema) is False
