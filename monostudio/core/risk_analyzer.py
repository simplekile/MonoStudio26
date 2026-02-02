from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum


class RiskLevel(str, Enum):
    SAFE = "SAFE"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


@dataclass(frozen=True)
class ProjectSnapshot:
    """
    Filesystem-agnostic, UI-agnostic snapshot used for risk evaluation.
    All fields must be present; if any signal is unknown (None), risk escalates to CRITICAL.
    """

    project_id: str
    asset_count: int | None
    shot_count: int | None
    publish_version_count: int | None
    has_external_references: bool | None  # None = unknown
    has_render_cache: bool | None  # None = unknown


@dataclass(frozen=True)
class RiskReport:
    risk_level: RiskLevel
    summary: str
    reasons: list[str]
    metrics: dict


def analyze_project_risk(snapshot: ProjectSnapshot) -> RiskReport:
    """
    Deterministic, explainable risk evaluation.

    Priority order (highest wins):
    1) CRITICAL: external refs True OR render cache True OR any required signal is unknown (None)
    2) HIGH: publish_version_count > 0
    3) MEDIUM: (asset_count > 0 OR shot_count > 0) AND publish_version_count == 0
    4) SAFE: asset_count == 0 AND shot_count == 0 AND publish_version_count == 0
    """

    reasons: list[str] = []

    # Unknown signals => CRITICAL
    unknown_fields: list[str] = []
    if snapshot.asset_count is None:
        unknown_fields.append("asset_count")
    if snapshot.shot_count is None:
        unknown_fields.append("shot_count")
    if snapshot.publish_version_count is None:
        unknown_fields.append("publish_version_count")
    if snapshot.has_external_references is None:
        unknown_fields.append("has_external_references")
    if snapshot.has_render_cache is None:
        unknown_fields.append("has_render_cache")

    if unknown_fields:
        reasons.append(f"One or more required signals are unknown: {', '.join(unknown_fields)}.")
        return RiskReport(
            risk_level=RiskLevel.CRITICAL,
            summary="CRITICAL: Required risk signals are unknown; operation is not safely evaluable.",
            reasons=reasons,
            metrics=asdict(snapshot),
        )

    assert snapshot.asset_count is not None
    assert snapshot.shot_count is not None
    assert snapshot.publish_version_count is not None
    assert snapshot.has_external_references is not None
    assert snapshot.has_render_cache is not None

    # CRITICAL conditions
    if snapshot.has_external_references:
        reasons.append("External references detected.")
        return RiskReport(
            risk_level=RiskLevel.CRITICAL,
            summary="CRITICAL: External references detected; operation may break dependencies.",
            reasons=reasons,
            metrics=asdict(snapshot),
        )
    if snapshot.has_render_cache:
        reasons.append("Render cache detected.")
        return RiskReport(
            risk_level=RiskLevel.CRITICAL,
            summary="CRITICAL: Render cache detected; operation may invalidate cached outputs.",
            reasons=reasons,
            metrics=asdict(snapshot),
        )

    # HIGH
    if snapshot.publish_version_count > 0:
        reasons.append("Publish versions exist.")
        return RiskReport(
            risk_level=RiskLevel.HIGH,
            summary="HIGH: Publish versions exist; operation may break published paths.",
            reasons=reasons,
            metrics=asdict(snapshot),
        )

    # MEDIUM
    if (snapshot.asset_count > 0 or snapshot.shot_count > 0) and snapshot.publish_version_count == 0:
        reasons.append("Assets or shots exist, but no publish versions.")
        return RiskReport(
            risk_level=RiskLevel.MEDIUM,
            summary="MEDIUM: Project has content but no publishes; references may still exist.",
            reasons=reasons,
            metrics=asdict(snapshot),
        )

    # SAFE
    if snapshot.asset_count == 0 and snapshot.shot_count == 0 and snapshot.publish_version_count == 0:
        reasons.append("No assets, shots, or publish versions found.")
        return RiskReport(
            risk_level=RiskLevel.SAFE,
            summary="SAFE: Empty project; operation impact is minimal.",
            reasons=reasons,
            metrics=asdict(snapshot),
        )

    # Fallback: safest behavior.
    reasons.append("Risk could not be classified deterministically; escalating.")
    return RiskReport(
        risk_level=RiskLevel.CRITICAL,
        summary="CRITICAL: Risk could not be classified deterministically.",
        reasons=reasons,
        metrics=asdict(snapshot),
    )

