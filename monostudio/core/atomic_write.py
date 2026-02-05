"""
Atomic file write for critical configuration and metadata.

All writes use: write to temp file -> flush -> fsync -> rename to target.
No direct overwrite. Ensures crash safety and no partial/corrupt files.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def atomic_write_text(
    path: Path | str,
    content: str,
    *,
    encoding: str = "utf-8",
) -> None:
    """
    Write content to path atomically: temp file in same dir, flush, fsync, rename.
    Creates parent directories if needed. Raises OSError on failure.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    prefix = path.name + "."
    fd = tempfile.NamedTemporaryFile(
        mode="w",
        encoding=encoding,
        prefix=prefix,
        suffix=".tmp",
        dir=path.parent,
        delete=False,
    )
    tmp_path = Path(fd.name)
    try:
        fd.write(content)
        fd.flush()
        if hasattr(os, "fsync"):
            os.fsync(fd.fileno())
        fd.close()
        os.replace(str(tmp_path), str(path))
    except Exception:
        fd.close()
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
