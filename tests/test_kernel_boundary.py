"""The kernel must stay reusable: no app (src.*) or platform imports.

This test IS the architecture. If it fails, the kernel has grown a
dependency that would break extraction to its own repo later.
"""

import ast
from pathlib import Path

import pytest

KERNEL_DIR = Path(__file__).resolve().parent.parent / "agentkernel"

# Roots that must never be imported by kernel modules. chromadb is NOT
# listed: it is an optional dependency, guarded by try/except ImportError.
FORBIDDEN_ROOTS = {
    "src", "psutil", "wmi", "pycaw", "PyQt6",
    "langgraph", "openai", "winotify", "comtypes",
}


def _kernel_modules() -> list[Path]:
    if not KERNEL_DIR.is_dir():
        return []
    return sorted(KERNEL_DIR.glob("*.py"))


def test_kernel_package_exists():
    assert (KERNEL_DIR / "__init__.py").is_file(), (
        "agentkernel/ package missing — the kernel must live at repo root"
    )


@pytest.mark.parametrize("path", _kernel_modules(), ids=lambda p: p.name)
def test_no_forbidden_imports(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    violations = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""]
        else:
            continue
        for name in names:
            root = name.split(".")[0]
            if root in FORBIDDEN_ROOTS or root.startswith("win32"):
                violations.append(f"{path.name}: import of '{name}'")
    assert not violations, "Kernel boundary violated:\n" + "\n".join(violations)
