from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class _DccEntry:
    dcc_id: str
    label: str
    executable: str
    departments: tuple[str, ...]
    is_default: bool
    raw: dict[str, Any]


class DccRegistry:
    """
    Read-only DCC registry (data + rules only).
    Loads and validates project-level DCC configuration.
    """

    def __init__(self, *, entries: dict[str, _DccEntry], default_dcc: str | None, source_path: Path) -> None:
        self._entries = entries
        self._default_dcc = default_dcc
        self._source_path = Path(source_path)

    @staticmethod
    def default_path(*, repo_root: Path | None = None) -> Path:
        root = Path(repo_root) if repo_root is not None else Path(__file__).resolve().parents[2]
        return root / "monostudio_data" / "pipeline" / "dccs.json"

    @classmethod
    def from_file(cls, path: Path) -> "DccRegistry":
        path = Path(path)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as e:
            raise RuntimeError(f"DCC registry file is missing: {str(path)!r}") from e
        except (OSError, json.JSONDecodeError) as e:
            raise RuntimeError(f"Invalid DCC registry JSON: {str(path)!r}") from e

        if not isinstance(raw, dict):
            raise RuntimeError(f"Invalid DCC registry format (expected object): {str(path)!r}")

        dccs = raw.get("dccs")
        if not isinstance(dccs, dict) or not dccs:
            raise RuntimeError(f"Invalid DCC registry format (missing/empty 'dccs'): {str(path)!r}")

        entries: dict[str, _DccEntry] = {}
        default_id: str | None = None

        for dcc_id, node in dccs.items():
            if not isinstance(dcc_id, str) or not dcc_id.strip():
                raise RuntimeError(f"Invalid DCC id (key) in registry: {dcc_id!r} ({str(path)!r})")
            if not isinstance(node, dict):
                raise RuntimeError(f"Invalid DCC entry for {dcc_id!r} (expected object): {str(path)!r}")

            label = node.get("label")
            if not isinstance(label, str) or not label.strip():
                raise RuntimeError(f"Invalid DCC {dcc_id!r}: missing/invalid 'label' ({str(path)!r})")

            exe = node.get("executable")
            if not isinstance(exe, str) or not exe.strip():
                raise RuntimeError(f"Invalid DCC {dcc_id!r}: missing/invalid 'executable' ({str(path)!r})")

            depts = node.get("departments")
            if not isinstance(depts, list) or not depts:
                raise RuntimeError(f"Invalid DCC {dcc_id!r}: 'departments' must be a non-empty list ({str(path)!r})")

            departments_out: list[str] = []
            seen: set[str] = set()
            for d in depts:
                if not isinstance(d, str) or not d.strip():
                    raise RuntimeError(f"Invalid DCC {dcc_id!r}: department names must be non-empty strings ({str(path)!r})")
                dep = d.strip()
                if dep != dep.lower():
                    raise RuntimeError(
                        f"Invalid DCC {dcc_id!r}: department {dep!r} must be lowercase ({str(path)!r})"
                    )
                if dep not in seen:
                    seen.add(dep)
                    departments_out.append(dep)

            is_default = bool(node.get("default")) if "default" in node else False
            if is_default:
                if default_id is not None and default_id != dcc_id:
                    raise RuntimeError(
                        f"Invalid DCC registry: multiple defaults ({default_id!r}, {dcc_id!r}) ({str(path)!r})"
                    )
                default_id = dcc_id

            # Keep full node for UI/resolver access (copy to enforce read-only semantics).
            raw_copy = deepcopy(node)
            entries[dcc_id] = _DccEntry(
                dcc_id=dcc_id.strip(),
                label=label.strip(),
                executable=exe.strip(),
                departments=tuple(departments_out),
                is_default=is_default,
                raw=raw_copy,
            )

        return cls(entries=entries, default_dcc=default_id, source_path=path)

    # ==================================================
    # Required public API
    # ==================================================

    def get_all_dccs(self) -> list[str]:
        return list(self._entries.keys())

    def get_dcc_info(self, dcc_id: str) -> dict:
        did = (dcc_id or "").strip()
        entry = self._entries.get(did)
        if entry is None:
            raise RuntimeError(f"Unknown DCC id: {did!r}")
        out = deepcopy(entry.raw)
        out["id"] = entry.dcc_id
        out["label"] = entry.label
        out["executable"] = entry.executable
        out["departments"] = list(entry.departments)
        if entry.is_default:
            out["default"] = True
        return out

    def get_available_dccs(self, department: str) -> list[str]:
        dep = (department or "").strip()
        if not dep:
            return []
        dep_norm = dep.casefold()
        out: list[str] = []
        for dcc_id, e in self._entries.items():
            if any(d.casefold() == dep_norm for d in e.departments):
                out.append(dcc_id)
        return out

    def get_default_dcc(self) -> str | None:
        return self._default_dcc

    def is_dcc_allowed(self, dcc_id: str, department: str) -> bool:
        did = (dcc_id or "").strip()
        dep = (department or "").strip()
        if not did or not dep:
            return False
        e = self._entries.get(did)
        if e is None:
            return False
        dep_norm = dep.casefold()
        return any(d.casefold() == dep_norm for d in e.departments)

    def resolve_default_dcc(self, *, department: str | None, last_used: str | None = None) -> str | None:
        """
        Resolution priority:
          1. last_used (if allowed)
          2. project default (if allowed)
          3. None
        """
        dep = (department or "").strip() or None
        last = (last_used or "").strip() or None

        if dep is None:
            # When department is unknown, return last_used if known, else default if defined.
            if last is not None and last in self._entries:
                return last
            return self._default_dcc

        if last is not None and self.is_dcc_allowed(last, dep):
            return last

        if self._default_dcc is not None and self.is_dcc_allowed(self._default_dcc, dep):
            return self._default_dcc

        return None


@lru_cache(maxsize=1)
def get_default_dcc_registry() -> DccRegistry:
    return DccRegistry.from_file(DccRegistry.default_path())

