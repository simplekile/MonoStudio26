"""Immutable pipeline row snapshot types (Main View Engine v2)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

DimState = Literal["none", "no_work", "no_publish"]
HealthLevel = Literal["ok", "warn", "error"]
DccBadgeStatus = Literal["exists", "creating"]
AlertKind = Literal["health", "notes"]


@dataclass(frozen=True, slots=True)
class StatusChip:
    label: str
    color_hex: str
    status_key: str = ""


@dataclass(frozen=True, slots=True)
class AlertChip:
    kind: AlertKind
    level: HealthLevel | None = None  # health
    icon_name: str = ""
    color_hex: str = ""
    notes_count: int = 0
    notes_mode: str = "empty"  # empty | open | resolved


@dataclass(frozen=True, slots=True)
class DccBadgeSnapshot:
    dcc_id: str
    status: DccBadgeStatus
    brand_slug: str = ""
    color_hex: str = ""


@dataclass(frozen=True, slots=True)
class PipelineRowSnapshot:
    path: str
    display_name: str
    dim: DimState
    thumb_token: str
    status: StatusChip | None
    alerts: AlertChip | None
    dcc_stack: tuple[DccBadgeSnapshot, ...]
    meta: tuple[str, ...]

    def to_variant_map(self) -> dict[str, object]:
        """Flat map for QML `model.snapshot` binding."""
        dcc_overflow = max(0, len(self.dcc_stack) - 3)
        visible_dcc = self.dcc_stack[:3]
        return {
            "path": self.path,
            "displayName": self.display_name,
            "dim": self.dim,
            "thumbToken": self.thumb_token,
            "statusLabel": self.status.label if self.status else "",
            "statusColor": self.status.color_hex if self.status else "",
            "statusKey": self.status.status_key if self.status else "",
            "alertKind": self.alerts.kind if self.alerts else "",
            "alertLevel": self.alerts.level if self.alerts and self.alerts.level else "",
            "alertIcon": self.alerts.icon_name if self.alerts else "",
            "alertColor": self.alerts.color_hex if self.alerts else "",
            "notesCount": self.alerts.notes_count if self.alerts else 0,
            "notesMode": self.alerts.notes_mode if self.alerts else "empty",
            "dccNames": ",".join(b.dcc_id for b in visible_dcc),
            "dccOverflow": dcc_overflow,
            "meta": list(self.meta),
        }
