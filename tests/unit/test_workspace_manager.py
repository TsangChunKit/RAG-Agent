"""测试 workspace 管理功能。"""
import json
from pathlib import Path

import pytest

from scripts.workspace_manager import (
    create_workspace,
    get_current_workspace,
    get_workspace_dir,
    list_workspaces,
    load_workspace_config,
    validate_workspace,
)


class TestWorkspaceBasics:
    """基础 workspace 操作测试。"""

    def test_create_workspace(self, isolated_workspace, monkeypatch):
        """测试创建新 workspace。"""
        # 设置 PRIVATE_DIR 指向测试目录
        monkeypatch.setattr("scripts.workspace_manager.PRIVATE_DIR", isolated_workspace)
        monkeypatch.setattr("scripts.workspace_manager.WORKSPACES_ROOT",
                           isolated_workspace / "workspaces")

        # 创建 workspace
        create_workspace(
            name="test-ws",
            display_name="测试空间",
            domain="generic"
        )

        # 验证目录结构
        ws_dir = isolated_workspace / "workspaces" / "test-ws"
        assert ws_dir.exists()
        assert (ws_dir / "data" / "raw").exists()
        assert (ws_dir / "db").exists()

        # 验证配置文件
        config_file = ws_dir / ".workspace_config.json"
        assert config_file.exists()

        config = json.loads(config_file.read_text())
        assert config["name"] == "test-ws"
        assert config["display_name"] == "测试空间"
        assert config["domain"] == "generic"

    def test_list_workspaces_empty(self, isolated_workspace, monkeypatch):
        """测试空 workspace 列表。"""
        monkeypatch.setattr("scripts.workspace_manager.PRIVATE_DIR", isolated_workspace)
        monkeypatch.setattr("scripts.workspace_manager.WORKSPACES_ROOT",
                           isolated_workspace / "workspaces")

        # 空目录
        (isolated_workspace / "workspaces").mkdir()
        # 确保没有旧 data 目录
        old_data = isolated_workspace / "data"
        if old_data.exists():
            import shutil
            shutil.rmtree(old_data)

        workspaces = list_workspaces()
        # 应该没有 workspace（没有旧数据，也没有新 workspace）
        assert workspaces == []

    def test_list_workspaces_with_legacy(self, isolated_workspace, monkeypatch):
        """测试识别 _legacy workspace。"""
        monkeypatch.setattr("scripts.workspace_manager.PRIVATE_DIR", isolated_workspace)
        monkeypatch.setattr("scripts.workspace_manager.WORKSPACES_ROOT",
                           isolated_workspace / "workspaces")

        # 创建旧数据目录
        (isolated_workspace / "data").mkdir(parents=True, exist_ok=True)
        (isolated_workspace / "workspaces").mkdir(parents=True, exist_ok=True)

        workspaces = list_workspaces()
        assert len(workspaces) == 1
        assert workspaces[0]["name"] == "_legacy"

    def test_get_workspace_dir_legacy(self, isolated_workspace, monkeypatch):
        """测试获取 _legacy workspace 目录。"""
        monkeypatch.setattr("scripts.workspace_manager.PRIVATE_DIR", isolated_workspace)

        ws_dir = get_workspace_dir("_legacy")
        assert ws_dir == isolated_workspace

    def test_validate_workspace(self, isolated_workspace, monkeypatch):
        """测试 workspace 验证。"""
        monkeypatch.setattr("scripts.workspace_manager.PRIVATE_DIR", isolated_workspace)
        monkeypatch.setattr("scripts.workspace_manager.WORKSPACES_ROOT",
                           isolated_workspace / "workspaces")

        create_workspace("test-ws", "测试", "generic")

        assert validate_workspace("test-ws") is True
        assert validate_workspace("nonexistent") is False


class TestWorkspaceConfig:
    """Workspace 配置测试。"""

    def test_load_workspace_config_default(self, isolated_workspace, monkeypatch):
        """测试加载默认配置。"""
        monkeypatch.setattr("scripts.workspace_manager.PRIVATE_DIR", isolated_workspace)

        # _legacy workspace 使用默认配置
        config = load_workspace_config("_legacy")

        assert config["name"] == "_legacy"
        assert "graph_schema" in config
        assert "persona" in config

    def test_load_workspace_config_custom(self, isolated_workspace, monkeypatch,
                                          sample_workspace_config):
        """测试加载自定义配置。"""
        monkeypatch.setattr("scripts.workspace_manager.PRIVATE_DIR", isolated_workspace)
        monkeypatch.setattr("scripts.workspace_manager.WORKSPACES_ROOT",
                           isolated_workspace / "workspaces")

        # 创建 workspace
        ws_dir = isolated_workspace / "workspaces" / "test-ws"
        ws_dir.mkdir(parents=True)

        config_file = ws_dir / ".workspace_config.json"
        config_file.write_text(json.dumps(sample_workspace_config))

        # 加载配置
        config = load_workspace_config("test-ws")

        assert config["name"] == "test-workspace"
        assert config["domain"] == "counseling"
