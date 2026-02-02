from __future__ import annotations

import re
from datetime import date, datetime


_WS_RE = re.compile(r"\s+")
_BAD_RE = re.compile(r"[^a-z0-9_]+")
_US_RE = re.compile(r"_+")


def sanitize_project_name_for_id(project_name: str) -> str:
    """
    Sanitize a human-readable project name into a machine-safe token.

    Rules (MONOS v1):
    - Trim leading/trailing whitespace
    - Collapse multiple spaces/whitespace into a single underscore
    - Lowercase
    - Allow only: a-z, 0-9, underscore
    """

    s = (project_name or "").strip()
    if not s:
        return "project"

    s = _WS_RE.sub("_", s)
    s = s.lower()
    s = _BAD_RE.sub("_", s)
    s = _US_RE.sub("_", s).strip("_")
    return s or "project"


def generate_project_id(project_name: str, *, created_date: date | datetime | None = None) -> str:
    """
    Generate an immutable Project ID (folder-safe) using the CREATED DATE (today by default).

    Format:
      YYMMDD_<SANITIZED_PROJECT_NAME>

    Notes:
    - This uses created_date (NOT the project start date).
    """

    d = created_date
    if d is None:
        d = date.today()
    if isinstance(d, datetime):
        d = d.date()

    prefix = d.strftime("%y%m%d")
    token = sanitize_project_name_for_id(project_name)
    return f"{prefix}_{token}"

