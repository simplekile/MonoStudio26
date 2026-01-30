from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class ViewItemKind(str, Enum):
    ASSET = "asset"
    SHOT = "shot"
    DEPARTMENT = "department"


@dataclass(frozen=True)
class ViewItem:
    kind: ViewItemKind
    name: str
    type_badge: str
    path: Path
    departments_count: int | None = None
    ref: object | None = None

