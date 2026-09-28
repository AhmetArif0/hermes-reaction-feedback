"""Packaging checks: what the catalog installs, what its docs page renders, and what the code may touch."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "reaction-feedback"
SOURCES = sorted(PLUGIN.glob("*.py"))


def _manifest_list(key: str) -> list:
    lines = (PLUGIN / "plugin.yaml").read_text(encoding="utf-8").splitlines()
    start = lines.index(f"{key}:") + 1
    items = []
    for line in lines[start:]:
        if not line.startswith("  - "):
            break
        items.append(line[4:].strip())
    return items


def test_plugin_readme_matches_repo_readme():
    """The catalog docs page renders <subdir>/README.md; GitHub renders the root one. Keep them identical."""
    assert (PLUGIN / "README.md").read_bytes() == (ROOT / "README.md").read_bytes()


def test_manifest_declares_exactly_the_registered_hooks():
    source = (PLUGIN / "__init__.py").read_text(encoding="utf-8")
    registered = [part.split('"')[1] for part in source.split("ctx.register_hook(")[1:]]
    assert _manifest_list("provides_hooks") == registered == [
        "pre_gateway_dispatch", "pre_llm_call", "gateway_platform_event"]


def test_manifest_version_is_the_one_the_readme_documents():
    manifest = (PLUGIN / "plugin.yaml").read_text(encoding="utf-8")
    version = next(line.split(":", 1)[1].strip() for line in manifest.splitlines() if line.startswith("version:"))
    assert f"### {version}" in (ROOT / "README.md").read_text(encoding="utf-8")


def test_code_has_no_network_subprocess_or_file_writes():
    """What the README's "Security and footprint" section promises, checked on the source."""
    banned_modules = {"socket", "subprocess", "urllib", "http", "requests", "httpx", "aiohttp",
                      "shutil", "tempfile", "sqlite3", "pickle", "ctypes", "multiprocessing"}
    banned_calls = {"open", "exec", "eval", "compile", "__import__", "system", "popen", "remove",
                    "unlink", "rmdir", "rename", "replace", "write_text", "write_bytes", "mkdir"}
    for path in SOURCES:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                names = []
            for name in names:
                assert name.split(".")[0] not in banned_modules, f"{path.name} imports {name}"
            if isinstance(node, ast.Call):
                func = node.func
                called = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""
                assert called not in banned_calls, f"{path.name}:{node.lineno} calls {called}()"


def test_only_documented_hermes_imports():
    allowed = {"hermes_constants"}  # get_hermes_home, documented for plugins
    for path in SOURCES:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level == 0:
                top = (node.module or "").split(".")[0]
                assert top not in {"hermes_cli", "gateway", "agent", "tools", "run_agent", "hermes_state"}, top
                if top.startswith("hermes"):
                    assert top in allowed, f"{path.name} imports {node.module}"
