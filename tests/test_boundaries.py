"""Dependency-boundary and smoke-import tests (Phase 0, task 5).

The seven Cognitio layers (plus the Layer-0 config package and the runnable worker app) form a
strict dependency order: a package may import another Cognitio package only if that package sits
*below* it. This enforces AGENTS.md's "dependencies point downward only" rule mechanically.

The rank below is the dependency order (an orchestrator ranks above what it orchestrates — e.g.
``pipeline`` runs ``extraction`` as a job stage, so it ranks higher). It is consistent with every
internal dependency declared in the members' ``pyproject.toml`` files.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent

# Lower number == lower layer. A package may import only strictly-lower-ranked packages.
LAYER_RANK: dict[str, int] = {
    "cognitio_config": 0,
    "cognitio_storage": 1,
    "cognitio_connectors": 2,
    "cognitio_extraction": 3,
    "cognitio_query": 4,
    "cognitio_review": 5,
    "cognitio_pipeline": 6,
    "cognitio_api": 7,
    "cognitio_worker": 8,
}

PACKAGE_DIRS: dict[str, Path] = {
    "cognitio_config": _REPO_ROOT / "packages/config/src/cognitio_config",
    "cognitio_storage": _REPO_ROOT / "packages/storage/src/cognitio_storage",
    "cognitio_connectors": _REPO_ROOT / "packages/connectors/src/cognitio_connectors",
    "cognitio_extraction": _REPO_ROOT / "packages/extraction/src/cognitio_extraction",
    "cognitio_query": _REPO_ROOT / "packages/query/src/cognitio_query",
    "cognitio_review": _REPO_ROOT / "packages/review/src/cognitio_review",
    "cognitio_pipeline": _REPO_ROOT / "packages/pipeline/src/cognitio_pipeline",
    "cognitio_api": _REPO_ROOT / "packages/api/src/cognitio_api",
    "cognitio_worker": _REPO_ROOT / "apps/worker/src/cognitio_worker",
}

_COGNITIO_PACKAGES = frozenset(LAYER_RANK)


def _imported_cognitio_packages(source: str) -> set[str]:
    """Return the set of cognitio_* top-level packages imported by a module's source."""
    tree = ast.parse(source)
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                if top in _COGNITIO_PACKAGES:
                    found.add(top)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                top = node.module.split(".")[0]
                if top in _COGNITIO_PACKAGES:
                    found.add(top)
    return found


def _module_files(package_dir: Path) -> list[Path]:
    return [p for p in package_dir.rglob("*.py") if "tests" not in p.parts]


def is_upward_import(importer: str, imported: str) -> bool:
    """An import is upward (illegal) when the imported package is not strictly lower-ranked."""
    if importer == imported:
        return False
    return LAYER_RANK[imported] >= LAYER_RANK[importer]


def test_layer_rank_and_dirs_cover_every_package() -> None:
    assert set(LAYER_RANK) == set(PACKAGE_DIRS)
    for name, path in PACKAGE_DIRS.items():
        assert path.is_dir(), f"missing source dir for {name}: {path}"


@pytest.mark.parametrize("package", sorted(PACKAGE_DIRS))
def test_no_upward_layer_imports(package: str) -> None:
    """No package imports a package at its own or a higher layer."""
    violations: list[str] = []
    for module_file in _module_files(PACKAGE_DIRS[package]):
        imported = _imported_cognitio_packages(module_file.read_text())
        for dependency in imported:
            if is_upward_import(package, dependency):
                rel = module_file.relative_to(_REPO_ROOT)
                violations.append(f"{rel}: {package} -> {dependency}")
    assert not violations, "Upward (illegal) layer imports detected:\n" + "\n".join(violations)


def test_upward_import_detector_flags_known_violation() -> None:
    """The checker itself rejects an invalid upward import (acceptance: invalid imports fail)."""
    # Storage (Layer 1) importing the API (Layer 7) is illegal.
    assert is_upward_import("cognitio_storage", "cognitio_api") is True
    # ...and a sideways import (same layer) is also illegal.
    assert is_upward_import("cognitio_query", "cognitio_query") is False  # self never flagged
    # A legal downward import is allowed.
    assert is_upward_import("cognitio_api", "cognitio_storage") is False


def test_upward_import_detector_catches_injected_source() -> None:
    """A synthetic lower-layer module that imports an upper layer is detected."""
    bad_source = "from cognitio_api.main import app\nimport cognitio_review\n"
    imported = _imported_cognitio_packages(bad_source)
    assert "cognitio_api" in imported
    assert any(is_upward_import("cognitio_storage", dep) for dep in imported)


@pytest.mark.parametrize("package", sorted(LAYER_RANK))
def test_every_package_imports(package: str) -> None:
    """Smoke test: every package (and the worker app) imports without error."""
    import importlib

    module = importlib.import_module(package)
    assert module.__name__ == package
