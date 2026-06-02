"""
Atomic file write for critical configuration and metadata.

All writes use: write to temp file -> flush -> fsync -> rename to target.
No direct overwrite. Ensures crash safety and no partial/corrupt files.
"""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

_DEFAULT_MAX_ATTEMPTS = 6
_DEFAULT_RETRY_DELAY_S = 0.12


def _should_retry_os_error(exc: BaseException) -> bool:
    if isinstance(exc, PermissionError):
        return True
    if isinstance(exc, OSError) and getattr(exc, "winerror", None) == 5:
        return True
    return False


def atomic_write_text(
    path: Path | str,
    content: str,
    *,
    encoding: str = "utf-8",
    max_attempts: int = _DEFAULT_MAX_ATTEMPTS,
    retry_delay_s: float = _DEFAULT_RETRY_DELAY_S,
) -> None:
    """
    Write content to path atomically: temp file in same dir, flush, fsync, rename.
    Creates parent directories if needed. Raises OSError on failure.

    Retries replace on PermissionError (common when Dropbox/sync briefly locks the file).
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    prefix = path.name + "."
    last_err: BaseException | None = None
    attempts = max(1, int(max_attempts))

    for attempt in range(attempts):
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
            return
        except Exception as ex:
            last_err = ex
            try:
                fd.close()
            except Exception:
                pass
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
            if attempt + 1 < attempts and _should_retry_os_error(ex):
                time.sleep(retry_delay_s * (attempt + 1))
                continue
            raise

    if last_err is not None:
        raise last_err
