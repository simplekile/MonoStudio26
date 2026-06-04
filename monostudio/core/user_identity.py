"""Current-user identity for the serverless (Dropbox-shared) studio.

This is the single seam every feature should use to answer "who am I?".

Design (see ``.cursor/rules/plan_user_system_dropbox_v1.mdc``):

- **Roster (shared via Dropbox)**: ``<workspace_root>/.monostudio/users.json`` —
  the studio's list of people. One file per workspace, shared by all projects.
- **Local pin (per machine)**: stored in the app-level ``app_settings.json``
  (never synced). Manual override, mainly for shared machines.
- **Device fingerprint (auto-resolve)**: each user lists the devices bound to
  them; a machine resolves itself via its fingerprint, so no per-launch picker
  is needed on personal machines.

Resolution order for the current user: local pin -> device fingerprint -> None
(callers fall back to the OS account name for display).

This module is intentionally free of Qt so it can be unit-tested headless.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import socket
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from getpass import getuser
from pathlib import Path

from monostudio.core.app_paths import get_app_settings_path, migrate_app_settings_if_needed
from monostudio.core.atomic_write import atomic_write_text

ROSTER_SCHEMA = 1
REQUESTS_SCHEMA = 1
_PBKDF2_ITERATIONS = 200_000

# Deterministic accent palette for new users (MONOS-friendly hues).
_USER_COLORS = (
    "#3b82f6", "#14b8a6", "#8b5cf6", "#f97316", "#ef4444",
    "#22c55e", "#eab308", "#ec4899", "#06b6d4", "#a3e635",
)

# Studio roster roles (display / assignment hints — not app security; see access_control).
STUDIO_ROLES = (
    "artist",
    "lead",
    "supervisor",
    "producer",
    "coordinator",
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------- #
# Data model
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class DeviceBinding:
    fp: str
    label: str = ""
    added_at: str = ""

    def to_dict(self) -> dict:
        return {"fp": self.fp, "label": self.label, "added_at": self.added_at}


@dataclass(frozen=True)
class StudioUser:
    id: str
    name: str
    email: str = ""
    color_hex: str = "#3b82f6"
    role: str = "artist"
    departments: tuple[str, ...] = ()
    active: bool = True
    created_at: str = ""
    devices: tuple[DeviceBinding, ...] = field(default_factory=tuple)
    pwd_hash: str = ""
    avatar: str = ""  # filename under <ws>/.monostudio/avatars/

    @property
    def initials(self) -> str:
        parts = [p for p in (self.name or "").replace("_", " ").split() if p]
        if not parts:
            return "?"
        if len(parts) == 1:
            return parts[0][:2].upper()
        return (parts[0][0] + parts[-1][0]).upper()

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "email": self.email,
            "color_hex": self.color_hex,
            "role": self.role,
            "departments": list(self.departments),
            "active": bool(self.active),
            "created_at": self.created_at,
            "devices": [d.to_dict() for d in self.devices],
            "pwd_hash": self.pwd_hash,
            "avatar": self.avatar,
        }


def _user_from_dict(raw: dict) -> StudioUser | None:
    if not isinstance(raw, dict):
        return None
    uid = str(raw.get("id") or "").strip()
    name = str(raw.get("name") or "").strip()
    if not uid or not name:
        return None
    devices: list[DeviceBinding] = []
    for d in raw.get("devices") or []:
        if isinstance(d, dict) and str(d.get("fp") or "").strip():
            devices.append(
                DeviceBinding(
                    fp=str(d["fp"]).strip(),
                    label=str(d.get("label") or "").strip(),
                    added_at=str(d.get("added_at") or "").strip(),
                )
            )
    deps = tuple(
        str(x).strip() for x in (raw.get("departments") or []) if str(x).strip()
    )
    color = str(raw.get("color_hex") or "").strip() or "#3b82f6"
    return StudioUser(
        id=uid,
        name=name,
        email=str(raw.get("email") or "").strip(),
        color_hex=color,
        role=str(raw.get("role") or "artist").strip() or "artist",
        departments=deps,
        active=bool(raw.get("active", True)),
        created_at=str(raw.get("created_at") or "").strip(),
        devices=tuple(devices),
        pwd_hash=str(raw.get("pwd_hash") or "").strip(),
        avatar=str(raw.get("avatar") or "").strip(),
    )


def _replace_user(u: StudioUser, **changes) -> StudioUser:
    data = {
        "id": u.id, "name": u.name, "email": u.email, "color_hex": u.color_hex,
        "role": u.role, "departments": u.departments, "active": u.active,
        "created_at": u.created_at, "devices": u.devices,
        "pwd_hash": u.pwd_hash, "avatar": u.avatar,
    }
    data.update(changes)
    return StudioUser(**data)


def new_user(
    name: str,
    *,
    email: str = "",
    color_hex: str | None = None,
    role: str = "artist",
    departments: tuple[str, ...] = (),
) -> StudioUser:
    """Construct a fresh user with a stable id and an auto-picked color."""
    clean = (name or "").strip() or "Artist"
    uid = "u_" + uuid.uuid4().hex[:6]
    if not color_hex:
        idx = int(hashlib.sha256(uid.encode("utf-8")).hexdigest(), 16) % len(_USER_COLORS)
        color_hex = _USER_COLORS[idx]
    return StudioUser(
        id=uid,
        name=clean,
        email=(email or "").strip(),
        color_hex=color_hex,
        role=normalize_studio_role(role),
        departments=tuple(departments),
        active=True,
        created_at=_utc_now_iso(),
        devices=(),
    )


# --------------------------------------------------------------------------- #
# Roster (shared, workspace-level)
# --------------------------------------------------------------------------- #
def users_file(workspace_root: Path) -> Path:
    return Path(workspace_root) / ".monostudio" / "users.json"


def read_roster(workspace_root: Path | None) -> list[StudioUser]:
    if workspace_root is None:
        return []
    path = users_file(workspace_root)
    try:
        if not path.is_file():
            return []
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, dict):
        return []
    out: list[StudioUser] = []
    for raw in data.get("users") or []:
        u = _user_from_dict(raw)
        if u is not None:
            out.append(u)
    return out


def write_roster(workspace_root: Path, users: list[StudioUser]) -> None:
    payload = {
        "schema": ROSTER_SCHEMA,
        "updated_at": _utc_now_iso(),
        "users": [u.to_dict() for u in users],
    }
    atomic_write_text(
        users_file(workspace_root),
        json.dumps(payload, indent=2, ensure_ascii=False),
    )


def get_user(workspace_root: Path | None, user_id: str) -> StudioUser | None:
    uid = (user_id or "").strip()
    if not uid:
        return None
    for u in read_roster(workspace_root):
        if u.id == uid:
            return u
    return None


def upsert_user(workspace_root: Path, user: StudioUser) -> StudioUser:
    """Add or replace a user (matched by id) and persist the roster."""
    roster = read_roster(workspace_root)
    replaced = False
    for i, u in enumerate(roster):
        if u.id == user.id:
            roster[i] = user
            replaced = True
            break
    if not replaced:
        roster.append(user)
    write_roster(workspace_root, roster)
    return user


def deactivate_user(workspace_root: Path, user_id: str) -> None:
    roster = read_roster(workspace_root)
    changed = False
    for i, u in enumerate(roster):
        if u.id == user_id and u.active:
            roster[i] = _replace_user(u, active=False)
            changed = True
            break
    if changed:
        write_roster(workspace_root, roster)


def delete_user(workspace_root: Path, user_id: str, *, remove_avatar_file: bool = True) -> bool:
    """Permanently remove a user from the shared roster (admin team management).

    Returns False if the user was not found or the roster could not be written.
    """
    uid = (user_id or "").strip()
    if not uid:
        return False
    roster = read_roster(workspace_root)
    target: StudioUser | None = None
    remaining: list[StudioUser] = []
    for u in roster:
        if u.id == uid:
            target = u
        else:
            remaining.append(u)
    if target is None:
        return False
    if remove_avatar_file and target.avatar.strip():
        try:
            av = avatars_dir(workspace_root) / target.avatar.strip()
            if av.is_file():
                av.unlink()
        except OSError:
            pass
    try:
        write_roster(workspace_root, remaining)
    except OSError:
        return False
    _clear_pins_for_user_id(uid)
    return True


def _clear_pins_for_user_id(user_id: str) -> None:
    """Drop local session pins that pointed at a deleted roster user."""
    uid = (user_id or "").strip()
    if not uid:
        return
    data = _read_app_settings()
    pins = data.get("user_pins")
    if not isinstance(pins, dict):
        return
    changed = False
    for key in list(pins.keys()):
        if str(pins.get(key) or "").strip() == uid:
            del pins[key]
            changed = True
    if changed:
        data["user_pins"] = pins
        _write_app_settings(data)
    mem_key_matches = [k for k, v in _session_user_ids.items() if v == uid]
    for k in mem_key_matches:
        _session_user_ids.pop(k, None)


def active_users(workspace_root: Path | None) -> list[StudioUser]:
    return [u for u in read_roster(workspace_root) if u.active]


# --------------------------------------------------------------------------- #
# Device fingerprint (auto-resolve)
# --------------------------------------------------------------------------- #
def _machine_guid() -> str:
    """Stable per-OS-install id (Windows registry); empty on non-Windows/failure."""
    try:
        import winreg  # type: ignore

        flags = winreg.KEY_READ | getattr(winreg, "KEY_WOW64_64KEY", 0)
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Cryptography",
            0,
            flags,
        ) as key:
            value, _ = winreg.QueryValueEx(key, "MachineGuid")
            return str(value or "").strip()
    except Exception:
        return ""


def device_fingerprint() -> str:
    """Stable, hashed device token. Not a security measure (trust-based)."""
    host = ""
    try:
        host = socket.gethostname() or ""
    except OSError:
        host = ""
    raw = f"{_machine_guid()}|{host}".strip("|")
    if not raw:
        try:
            raw = getuser() or "unknown"
        except Exception:
            raw = "unknown"
    return "d_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def device_label() -> str:
    try:
        host = socket.gethostname() or "?"
    except OSError:
        host = "?"
    try:
        user = getuser() or "?"
    except Exception:
        user = "?"
    return f"{host} / {user}"


def find_user_by_device(workspace_root: Path | None, fp: str | None = None) -> StudioUser | None:
    """Return the single active user bound to ``fp`` (None if 0 or >1 — ambiguous)."""
    token = (fp or device_fingerprint()).strip()
    if not token:
        return None
    matches = [
        u
        for u in read_roster(workspace_root)
        if u.active and any(d.fp == token for d in u.devices)
    ]
    return matches[0] if len(matches) == 1 else None


def register_device(
    workspace_root: Path,
    user_id: str,
    *,
    fp: str | None = None,
    label: str | None = None,
) -> bool:
    """Bind this device to ``user_id`` EXCLUSIVELY (remove fp from any other user).

    Returns False if the roster could not be written (e.g. Dropbox file lock).
    """
    token = (fp or device_fingerprint()).strip()
    if not token:
        return True
    lbl = label if label is not None else device_label()
    roster = read_roster(workspace_root)
    out: list[StudioUser] = []
    for u in roster:
        kept = tuple(d for d in u.devices if d.fp != token)
        if u.id == user_id:
            kept = kept + (DeviceBinding(fp=token, label=lbl, added_at=_utc_now_iso()),)
        if kept != u.devices:
            u = _replace_user(u, devices=kept)
        out.append(u)
    try:
        write_roster(workspace_root, out)
    except OSError:
        return False
    return True


def forget_device(
    workspace_root: Path,
    *,
    user_id: str | None = None,
    fp: str | None = None,
) -> None:
    """Remove this device's fp from the roster (shared). Optional user_id to scope."""
    token = (fp or device_fingerprint()).strip()
    if not token:
        return
    roster = read_roster(workspace_root)
    changed = False
    out: list[StudioUser] = []
    for u in roster:
        if (user_id is None or u.id == user_id) and any(d.fp == token for d in u.devices):
            u = _replace_user(u, devices=tuple(d for d in u.devices if d.fp != token))
            changed = True
        out.append(u)
    if changed:
        write_roster(workspace_root, out)


# --------------------------------------------------------------------------- #
# Local pin (per-machine, per-workspace) — app_settings.json
# --------------------------------------------------------------------------- #
def _app_settings_path() -> Path:
    return get_app_settings_path()


def _read_app_settings() -> dict:
    migrate_app_settings_if_needed()
    try:
        data = json.loads(_app_settings_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write_app_settings(data: dict) -> None:
    atomic_write_text(_app_settings_path(), json.dumps(data, indent=2, ensure_ascii=False))


def update_app_settings(updates: dict) -> None:
    """Shallow-merge ``updates`` into app_settings.json (preserves unrelated keys like user_pins)."""
    if not updates:
        return
    data = _read_app_settings()
    data.update(updates)
    _write_app_settings(data)


def app_settings_path() -> Path:
    """Public path to shared local app config (geometry, user_pins, …)."""
    return _app_settings_path()


def _pin_key(workspace_root: Path | None) -> str:
    if workspace_root is None:
        return "_global"
    try:
        return str(Path(workspace_root).resolve()).replace("\\", "/").lower()
    except OSError:
        return str(workspace_root).replace("\\", "/").lower()


def get_current_user_id(workspace_root: Path | None = None) -> str | None:
    pins = _read_app_settings().get("user_pins")
    if not isinstance(pins, dict):
        return None
    val = pins.get(_pin_key(workspace_root))
    return str(val).strip() if val else None


def set_current_user_id(user_id: str | None, workspace_root: Path | None = None) -> None:
    data = _read_app_settings()
    pins = data.get("user_pins")
    if not isinstance(pins, dict):
        pins = {}
    key = _pin_key(workspace_root)
    if user_id:
        pins[key] = str(user_id).strip()
    else:
        pins.pop(key, None)
    data["user_pins"] = pins
    _write_app_settings(data)


def _set_last_signed_in_user_id(user_id: str, workspace_root: Path) -> None:
    """Remember the last successful sign-in for sign-in pre-select (not auto-login)."""
    data = _read_app_settings()
    last = data.get("last_signed_in")
    if not isinstance(last, dict):
        last = {}
    last[_pin_key(workspace_root)] = str(user_id).strip()
    data["last_signed_in"] = last
    _write_app_settings(data)


def get_last_signed_in_user_id(workspace_root: Path | None) -> str | None:
    last = _read_app_settings().get("last_signed_in")
    if not isinstance(last, dict):
        return None
    val = last.get(_pin_key(workspace_root))
    return str(val).strip() if val else None


# --------------------------------------------------------------------------- #
# Resolution (used everywhere)
# --------------------------------------------------------------------------- #
# In-memory session for "shared machine — don't remember" (lost on app close).
_session_user_ids: dict[str, str] = {}


def session_sign_in(
    workspace_root: Path,
    user_id: str,
    *,
    remember: bool = True,
    register_device_too: bool = True,
) -> str | None:
    """Mark ``user_id`` as signed in on this machine after a successful login.

    Returns a short warning if sign-in succeeded but device binding could not be saved.
    """
    key = _pin_key(workspace_root)
    uid = str(user_id).strip()
    _session_user_ids[key] = uid
    _set_last_signed_in_user_id(uid, workspace_root)
    if remember:
        set_current_user_id(uid, workspace_root)
    else:
        set_current_user_id(None, workspace_root)
    # Device binding follows the last successful sign-in (even without Remember),
    # so the sign-in picker does not keep highlighting a previous account.
    if register_device_too and not register_device(workspace_root, uid):
        return (
            "Signed in, but could not update the shared roster on disk "
            "(file may be locked by Dropbox). This device may still be linked to another account."
        )
    return None


def session_sign_out(workspace_root: Path | None) -> None:
    """Log out: clear in-memory + persisted session. Keeps device binding."""
    _session_user_ids.pop(_pin_key(workspace_root), None)
    set_current_user_id(None, workspace_root)


def get_current_user(workspace_root: Path | None) -> StudioUser | None:
    """Resolve who I am: in-memory session -> persisted session -> None."""
    if workspace_root is None:
        return None
    mem = _session_user_ids.get(_pin_key(workspace_root))
    if mem:
        u = get_user(workspace_root, mem)
        if u is not None and u.active:
            return u
    saved = get_current_user_id(workspace_root)
    if saved:
        u = get_user(workspace_root, saved)
        if u is not None and u.active:
            return u
    return None


def _os_user_name() -> str:
    try:
        return (getuser() or "").strip()
    except Exception:
        return ""


def get_current_user_display_name(workspace_root: Path | None = None) -> str:
    """Friendly name for the person using this machine (never empty)."""
    user = get_current_user(workspace_root)
    if user is not None and user.name.strip():
        return user.name.strip()
    return _os_user_name() or "Artist"


def resolve_user_name(
    workspace_root: Path | None,
    user_id: str | None,
    *,
    fallback: str = "",
) -> str:
    """Display name for a stored user_id (falls back to cached/OS name)."""
    if user_id:
        u = get_user(workspace_root, user_id)
        if u is not None and u.name.strip():
            return u.name.strip()
    return (fallback or "").strip() or _os_user_name() or "Artist"


# --------------------------------------------------------------------------- #
# Passwords (PBKDF2-HMAC-SHA256, stdlib only). Soft gate, not real security.
# --------------------------------------------------------------------------- #
def hash_password(password: str) -> str:
    pw = (password or "").encode("utf-8")
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw, salt, _PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${_PBKDF2_ITERATIONS}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iters_s, salt_hex, hash_hex = (stored or "").split("$", 3)
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac(
            "sha256", (password or "").encode("utf-8"),
            bytes.fromhex(salt_hex), int(iters_s),
        )
        return hmac.compare_digest(dk.hex(), hash_hex)
    except (ValueError, TypeError):
        return False


def has_password(user: StudioUser | None) -> bool:
    return bool(user is not None and (user.pwd_hash or "").strip())


def set_password(workspace_root: Path, user_id: str, password: str) -> None:
    roster = read_roster(workspace_root)
    changed = False
    for i, u in enumerate(roster):
        if u.id == user_id:
            roster[i] = _replace_user(u, pwd_hash=hash_password(password))
            changed = True
            break
    if changed:
        write_roster(workspace_root, roster)


def change_password(
    workspace_root: Path,
    user_id: str,
    current_password: str,
    new_password: str,
) -> str | None:
    """Change password after verifying the current one. Returns None on success."""
    user = get_user(workspace_root, user_id)
    if user is None:
        return "User not found."
    if has_password(user) and not verify_password(current_password, user.pwd_hash):
        return "Current password is incorrect."
    if not (new_password or "").strip():
        return "New password cannot be empty."
    set_password(workspace_root, user_id, new_password)
    return None


def user_color_choices() -> tuple[str, ...]:
    """Preset accent colors for initials / avatar fallback."""
    return _USER_COLORS


def studio_role_choices() -> tuple[str, ...]:
    """Preset studio roles for roster users (admin-editable in Team management)."""
    return STUDIO_ROLES


def normalize_studio_role(role: str | None) -> str:
    """Normalize role id; unknown custom values are kept lowercased."""
    raw = (role or "").strip().lower()
    return raw or "artist"


def studio_role_label(role: str) -> str:
    """Human label for UI (Artist, Lead, …)."""
    r = normalize_studio_role(role)
    return r.replace("_", " ").title()


def set_user_role(workspace_root: Path, user_id: str, role: str) -> StudioUser | None:
    """Set roster role for ``user_id`` (admin team management)."""
    user = get_user(workspace_root, user_id)
    if user is None:
        return None
    updated = _replace_user(user, role=normalize_studio_role(role))
    upsert_user(workspace_root, updated)
    return updated


def update_user_profile(
    workspace_root: Path,
    user_id: str,
    *,
    name: str | None = None,
    email: str | None = None,
    color_hex: str | None = None,
    avatar: str | None = None,
    clear_avatar: bool = False,
) -> StudioUser | None:
    """Update editable profile fields for ``user_id`` (self-service)."""
    user = get_user(workspace_root, user_id)
    if user is None:
        return None
    changes: dict = {}
    if name is not None:
        clean = (name or "").strip()
        if clean:
            changes["name"] = clean
    if email is not None:
        changes["email"] = (email or "").strip()
    if color_hex is not None:
        c = (color_hex or "").strip()
        if c:
            changes["color_hex"] = c
    if clear_avatar:
        changes["avatar"] = ""
    elif avatar is not None:
        changes["avatar"] = (avatar or "").strip()
    if not changes:
        return user
    updated = _replace_user(user, **changes)
    upsert_user(workspace_root, updated)
    return updated


# --------------------------------------------------------------------------- #
# Avatars (image files, shared under <ws>/.monostudio/avatars/)
# --------------------------------------------------------------------------- #
def avatars_dir(workspace_root: Path) -> Path:
    return Path(workspace_root) / ".monostudio" / "avatars"


def avatar_path(workspace_root: Path | None, user: StudioUser | None) -> Path | None:
    if workspace_root is None or user is None or not user.avatar.strip():
        return None
    p = avatars_dir(workspace_root) / user.avatar.strip()
    try:
        return p if p.is_file() else None
    except OSError:
        return None


def set_user_avatar(workspace_root: Path, user_id: str, filename: str) -> None:
    roster = read_roster(workspace_root)
    changed = False
    for i, u in enumerate(roster):
        if u.id == user_id:
            roster[i] = _replace_user(u, avatar=(filename or "").strip())
            changed = True
            break
    if changed:
        write_roster(workspace_root, roster)


# --------------------------------------------------------------------------- #
# Account requests (admin approval workflow)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class AccountRequest:
    id: str
    name: str
    email: str = ""
    pwd_hash: str = ""
    avatar: str = ""
    requested_at: str = ""
    note: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id, "name": self.name, "email": self.email,
            "pwd_hash": self.pwd_hash, "avatar": self.avatar,
            "requested_at": self.requested_at, "note": self.note,
        }


def requests_file(workspace_root: Path) -> Path:
    return Path(workspace_root) / ".monostudio" / "account_requests.json"


def read_requests(workspace_root: Path | None) -> list[AccountRequest]:
    if workspace_root is None:
        return []
    try:
        data = json.loads(requests_file(workspace_root).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, dict):
        return []
    out: list[AccountRequest] = []
    for raw in data.get("requests") or []:
        if not isinstance(raw, dict):
            continue
        rid = str(raw.get("id") or "").strip()
        name = str(raw.get("name") or "").strip()
        if not rid or not name:
            continue
        out.append(
            AccountRequest(
                id=rid, name=name,
                email=str(raw.get("email") or "").strip(),
                pwd_hash=str(raw.get("pwd_hash") or "").strip(),
                avatar=str(raw.get("avatar") or "").strip(),
                requested_at=str(raw.get("requested_at") or "").strip(),
                note=str(raw.get("note") or "").strip(),
            )
        )
    return out


def _write_requests(workspace_root: Path, reqs: list[AccountRequest]) -> None:
    payload = {
        "schema": REQUESTS_SCHEMA,
        "updated_at": _utc_now_iso(),
        "requests": [r.to_dict() for r in reqs],
    }
    atomic_write_text(
        requests_file(workspace_root),
        json.dumps(payload, indent=2, ensure_ascii=False),
    )


def submit_request(
    workspace_root: Path,
    name: str,
    *,
    email: str = "",
    pwd_hash: str = "",
    avatar: str = "",
    note: str = "",
) -> AccountRequest:
    req = AccountRequest(
        id="r_" + uuid.uuid4().hex[:6],
        name=(name or "").strip() or "Unknown",
        email=(email or "").strip(),
        pwd_hash=(pwd_hash or "").strip(),
        avatar=(avatar or "").strip(),
        requested_at=_utc_now_iso(),
        note=(note or "").strip(),
    )
    reqs = read_requests(workspace_root)
    reqs.append(req)
    _write_requests(workspace_root, reqs)
    return req


def reject_request(workspace_root: Path, req_id: str) -> None:
    reqs = [r for r in read_requests(workspace_root) if r.id != req_id]
    _write_requests(workspace_root, reqs)


def approve_request(
    workspace_root: Path,
    req_id: str,
    *,
    role: str = "artist",
) -> StudioUser | None:
    """Promote a pending request into a real roster user, then drop the request."""
    target: AccountRequest | None = None
    remaining: list[AccountRequest] = []
    for r in read_requests(workspace_root):
        if r.id == req_id and target is None:
            target = r
        else:
            remaining.append(r)
    if target is None:
        return None
    user = new_user(target.name, email=target.email, role=normalize_studio_role(role))
    user = _replace_user(user, pwd_hash=target.pwd_hash, avatar=target.avatar)
    upsert_user(workspace_root, user)
    _write_requests(workspace_root, remaining)
    return user


def roster_has_active_users(workspace_root: Path | None) -> bool:
    """Bootstrap helper: False means it is safe to create the first account directly."""
    return any(u.active for u in read_roster(workspace_root))
