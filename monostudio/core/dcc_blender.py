from __future__ import annotations

import base64
import json
import subprocess
from pathlib import Path
from typing import Any


class BlenderDccAdapter:
    """
    Desktop-side Blender launcher for MONOS.

    This runs Blender as an external process and executes the Blender-side MONOS adapter
    (`monos_blender.adapter`) via `--python-expr`.
    """

    def __init__(self, *, blender_executable: str, repo_root: Path) -> None:
        self._blender_executable = (blender_executable or "").strip()
        self._repo_root = repo_root

    def open_file(self, *, filepath: str, context: dict[str, Any]) -> None:
        self._launch_with_expr(
            self._expr_call("open_file", filepath=filepath, context=context),
        )

    def create_new_file(self, *, filepath: str, context: dict[str, Any]) -> None:
        self._launch_with_expr(
            self._expr_call("create_new_file", filepath=filepath, context=context),
        )

    def _launch_with_expr(self, expr: str) -> None:
        exe = self._blender_executable
        if not exe:
            raise RuntimeError("Blender executable is not configured.")

        try:
            subprocess.Popen(
                [exe, "--python-expr", expr],
                cwd=str(self._repo_root),
                close_fds=True,
            )
        except Exception as e:
            raise RuntimeError(f"Failed to launch Blender using executable: {exe!r}") from e

    def _expr_call(self, fn_name: str, *, filepath: str, context: dict[str, Any]) -> str:
        if not isinstance(filepath, str) or not filepath.strip():
            raise RuntimeError("Internal error: filepath must be a non-empty string.")
        if not isinstance(context, dict):
            raise RuntimeError("Internal error: context must be a dict.")

        payload = json.dumps(context, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        ctx_b64 = base64.b64encode(payload).decode("ascii")
        repo = str(self._repo_root)

        # NOTE: This code executes inside Blender's embedded Python.
        return (
            "import sys, json, base64\n"
            f"sys.path.insert(0, {repo!r})\n"
            "from monos_blender import adapter as _a\n"
            f"_ctx = json.loads(base64.b64decode({ctx_b64!r}).decode('utf-8'))\n"
            f"_a.{fn_name}(filepath={filepath!r}, context=_ctx)\n"
        )

