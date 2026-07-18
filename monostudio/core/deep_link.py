"""Parse and build monostudio:// deep links (navigation + Discord assign)."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse, urlencode

SCHEME = "monostudio"
ASSIGN_HOST = "assign"
OPEN_HOST = "open"

# Short query keys / hash length for entity links (no server-side map).
ENTITY_ID_PARAM = "e"
ENTITY_SHORT_ID_LEN = 10

PAGE_ALIASES: dict[str, str] = {
    "assets": "Assets",
    "asset": "Assets",
    "shots": "Shots",
    "shot": "Shots",
    "inbox": "Inbox",
    "guide": "Project Guide",
    "project_guide": "Project Guide",
    "project guide": "Project Guide",
    "delivery": "Delivery",
    "outbox": "Delivery",
    "internal_check": "Internal check",
    "internal check": "Internal check",
    "internal-check": "Internal check",
    "trash": "Trash",
    "schedule": "Schedule",
    "dashboard": "Dashboard",
}

# Compact page token when building open links (parse still accepts full names).
PAGE_BUILD_ALIAS: dict[str, str] = {
    "Assets": "assets",
    "Shots": "shots",
    "Inbox": "inbox",
    "Project Guide": "guide",
    "Delivery": "delivery",
    "Internal check": "internal_check",
    "Trash": "trash",
    "Schedule": "schedule",
    "Dashboard": "dashboard",
}

OPEN_PAGES: frozenset[str] = frozenset(
    {
        "Assets",
        "Shots",
        "Inbox",
        "Project Guide",
        "Delivery",
        "Internal check",
        "Trash",
        "Schedule",
        "Dashboard",
    }
)


@dataclass(frozen=True)
class AssignDeepLink:
    inbox_id: str
    action: str = "open"  # open | confirm


@dataclass(frozen=True)
class OpenDeepLink:
    project_id: str
    page: str
    entity: str | None = None  # posix path relative to project root (legacy long form)
    entity_id: str | None = None  # sha256 prefix of normalized entity path
    department: str | None = None
    type_id: str | None = None
    trash_id: str | None = None


def extract_deep_link_from_argv(argv: list[str]) -> str | None:
    for arg in argv[1:]:
        text = (arg or "").strip()
        if text.lower().startswith(f"{SCHEME}://"):
            return text
    return None


def normalize_page_name(raw: str) -> str | None:
    text = unquote((raw or "").strip())
    if not text:
        return None
    key = text.casefold()
    if key in PAGE_ALIASES:
        return PAGE_ALIASES[key]
    for page in OPEN_PAGES:
        if page.casefold() == key:
            return page
    return None


def project_relative_path(project_root: Path, path: Path) -> str | None:
    try:
        return Path(path).resolve().relative_to(Path(project_root).resolve()).as_posix()
    except (OSError, ValueError):
        return None


def normalize_entity_rel(entity_rel: str | None) -> str:
    """Canonical project-relative path for hashing / compare."""
    return (entity_rel or "").strip().replace("\\", "/").strip("/")


def entity_path_short_id(entity_rel: str | None) -> str:
    """Deterministic short id from entity path (sha256 hex prefix)."""
    rel = normalize_entity_rel(entity_rel)
    if not rel:
        return ""
    digest = hashlib.sha256(rel.encode("utf-8")).hexdigest()
    return digest[:ENTITY_SHORT_ID_LEN]


def resolve_project_root_in_workspace(workspace_root: Path, project_id: str) -> Path | None:
    from monostudio.core.discord_inbox_debounce import resolve_project_root

    return resolve_project_root(workspace_root, project_id)


def page_entity_scan_root(project_root: Path, page: str) -> Path | None:
    """Top-level folder to scan when resolving a short entity id for ``page``."""
    from monostudio.core.structure_registry import StructureRegistry

    root = Path(project_root)
    page_name = normalize_page_name(page) or (page or "").strip()
    struct = StructureRegistry.for_project(root)
    if page_name == "Assets":
        return root / struct.get_folder("assets")
    if page_name == "Shots":
        return root / struct.get_folder("shots")
    if page_name == "Inbox":
        return root / struct.get_folder("inbox")
    if page_name == "Project Guide":
        return root / struct.get_folder("project_guide")
    if page_name == "Delivery":
        return root / struct.get_folder("outbox")
    if page_name == "Internal check":
        from monostudio.core.internal_check_reader import get_internal_check_root

        return get_internal_check_root(root)
    return None


def _should_skip_scan_name(name: str) -> bool:
    n = (name or "").strip()
    if not n or n in (".", ".."):
        return True
    if n.startswith("."):
        return True
    return False


def iter_entity_rels_for_page(project_root: Path, page: str):
    """Yield project-relative posix paths that may be deep-linked on ``page``."""
    root = Path(project_root).resolve()
    page_name = normalize_page_name(page) or (page or "").strip()
    scan = page_entity_scan_root(root, page_name)
    if scan is None or not scan.is_dir():
        return
    # Assets: type/name. Shots: flat shot folders (same as build_project_index).
    if page_name == "Shots":
        try:
            for entity in scan.iterdir():
                if not entity.is_dir() or _should_skip_scan_name(entity.name):
                    continue
                try:
                    yield entity.resolve().relative_to(root).as_posix()
                except (OSError, ValueError):
                    continue
        except OSError:
            return
        return
    if page_name == "Assets":
        try:
            for mid in scan.iterdir():
                if not mid.is_dir() or _should_skip_scan_name(mid.name):
                    continue
                for entity in mid.iterdir():
                    if not entity.is_dir() or _should_skip_scan_name(entity.name):
                        continue
                    try:
                        yield entity.resolve().relative_to(root).as_posix()
                    except (OSError, ValueError):
                        continue
        except OSError:
            return
        return

    # Tree pages: files and folders under the page root.
    try:
        for dirpath, dirnames, filenames in os.walk(scan, topdown=True):
            dirnames[:] = [d for d in dirnames if not _should_skip_scan_name(d)]
            base = Path(dirpath)
            try:
                if base.resolve() != scan.resolve():
                    yield base.resolve().relative_to(root).as_posix()
            except (OSError, ValueError):
                pass
            for name in filenames:
                if _should_skip_scan_name(name):
                    continue
                path = base / name
                try:
                    yield path.resolve().relative_to(root).as_posix()
                except (OSError, ValueError):
                    continue
    except OSError:
        return


def resolve_entity_short_id(
    project_root: Path,
    page: str,
    entity_id: str,
) -> str | None:
    """Find project-relative path whose short id matches ``entity_id`` (or None)."""
    eid = (entity_id or "").strip().casefold()
    if not eid or len(eid) < 8:
        return None
    for rel in iter_entity_rels_for_page(project_root, page):
        if entity_path_short_id(rel).casefold() == eid:
            return normalize_entity_rel(rel)
    return None


def parse_assign_deep_link(url: str) -> AssignDeepLink | None:
    raw = (url or "").strip()
    if not raw.lower().startswith(f"{SCHEME}://"):
        return None
    try:
        parsed = urlparse(raw)
    except ValueError:
        return None
    if (parsed.scheme or "").lower() != SCHEME:
        return None
    if (parsed.netloc or "").lower() != ASSIGN_HOST and not (parsed.path or "").startswith("/assign"):
        return None
    qs = parse_qs(parsed.query, keep_blank_values=False)
    inbox = (qs.get("inbox") or qs.get("inbox_id") or [""])[0].strip()
    if not inbox:
        return None
    action = (qs.get("action") or ["open"])[0].strip().lower() or "open"
    if action not in ("open", "confirm"):
        action = "open"
    return AssignDeepLink(inbox_id=inbox, action=action)


def parse_open_deep_link(url: str) -> OpenDeepLink | None:
    raw = (url or "").strip()
    if not raw.lower().startswith(f"{SCHEME}://"):
        return None
    try:
        parsed = urlparse(raw)
    except ValueError:
        return None
    if (parsed.scheme or "").lower() != SCHEME:
        return None
    host = (parsed.netloc or "").lower()
    path = (parsed.path or "").strip("/").lower()
    if host != OPEN_HOST and path != OPEN_HOST:
        return None
    qs = parse_qs(parsed.query, keep_blank_values=False)
    project_id = (qs.get("project") or qs.get("project_id") or [""])[0].strip()
    page_raw = (qs.get("page") or [""])[0].strip()
    page = normalize_page_name(page_raw)
    if not project_id or page is None:
        return None
    entity = (qs.get("entity") or qs.get("path") or [""])[0].strip()
    entity = unquote(entity).replace("\\", "/").strip("/") or None
    entity_id = (qs.get(ENTITY_ID_PARAM) or qs.get("entity_id") or [""])[0].strip() or None
    if entity_id:
        entity_id = entity_id.casefold()
    department = (qs.get("dept") or qs.get("department") or [""])[0].strip() or None
    type_id = (qs.get("type") or qs.get("type_id") or [""])[0].strip() or None
    trash_id = (qs.get("trash") or qs.get("trash_id") or [""])[0].strip() or None
    return OpenDeepLink(
        project_id=project_id,
        page=page,
        entity=entity,
        entity_id=entity_id,
        department=department,
        type_id=type_id,
        trash_id=trash_id,
    )


def build_assign_deep_link(
    inbox_id: str,
    *,
    action: str = "open",
) -> str:
    iid = (inbox_id or "").strip()
    act = (action or "open").strip().lower()
    if act not in ("open", "confirm"):
        act = "open"
    query = urlencode({"inbox": iid, "action": act})
    return f"{SCHEME}://{ASSIGN_HOST}?{query}"


def build_open_deep_link(
    project_id: str,
    page: str,
    *,
    entity: str | None = None,
    department: str | None = None,
    type_id: str | None = None,
    trash_id: str | None = None,
    legacy_entity_path: bool = False,
) -> str:
    pid = (project_id or "").strip()
    normalized = normalize_page_name(page)
    if not pid or normalized is None:
        raise ValueError("project_id and page are required")
    page_token = PAGE_BUILD_ALIAS.get(normalized, normalized)
    params: dict[str, str] = {"project": pid, "page": page_token}
    ent = normalize_entity_rel(entity)
    if ent:
        if legacy_entity_path:
            params["entity"] = ent
        else:
            short = entity_path_short_id(ent)
            if short:
                params[ENTITY_ID_PARAM] = short
    dept = (department or "").strip()
    if dept:
        params["dept"] = dept
    typ = (type_id or "").strip()
    if typ:
        params["type"] = typ
    tid = (trash_id or "").strip()
    if tid:
        params["trash"] = tid
    return f"{SCHEME}://{OPEN_HOST}?{urlencode(params, quote_via=quote)}"


def build_page_deep_link(project_id: str, page: str) -> str:
    return build_open_deep_link(project_id, page)


def build_entity_deep_link(
    project_id: str,
    *,
    page: str,
    entity_rel: str,
    department: str | None = None,
    type_id: str | None = None,
) -> str:
    return build_open_deep_link(
        project_id,
        page,
        entity=entity_rel,
        department=department,
        type_id=type_id,
    )


def build_trash_entry_deep_link(project_id: str, trash_id: str) -> str:
    return build_open_deep_link(project_id, "Trash", trash_id=trash_id)


LABEL_SEP = " · "


def build_monos_link_label(
    *,
    page: str,
    primary: str | None = None,
    department: str | None = None,
    project: str | None = None,
) -> str:
    """Short label for chat, e.g. ``hero · Assets · Modelling`` or ``My Film · Assets``."""
    page_name = normalize_page_name(page) or (page or "").strip() or "MONOS"
    parts: list[str] = []
    proj = (project or "").strip()
    if proj:
        parts.append(proj)
    prim = (primary or "").strip()
    if prim:
        parts.append(prim)
    if not prim:
        parts.append(page_name)
    elif page_name.casefold() not in {p.casefold() for p in parts}:
        parts.append(page_name)
    dept = (department or "").strip()
    if dept and dept.casefold() not in {p.casefold() for p in parts}:
        parts.append(dept)
    return LABEL_SEP.join(parts)


def primary_label_from_entity_path(entity_rel: str | None, *, max_parts: int = 3) -> str:
    """Tail of a project-relative path for tree/file links."""
    rel = (entity_rel or "").strip().replace("\\", "/").strip("/")
    if not rel:
        return ""
    parts = [p for p in rel.split("/") if p]
    if len(parts) > max_parts:
        return "/".join(parts[-max_parts:])
    return rel


def format_monos_link_clipboard(url: str, label: str | None = None) -> str:
    """Clipboard text: human label on line 1, ``monostudio://`` URL on line 2."""
    link = (url or "").strip()
    if not link:
        return ""
    lab = (label or "").strip()
    if not lab:
        return link
    return f"{lab}\n{link}"


def extract_monos_deep_link_from_text(text: str) -> str | None:
    """Return the first ``monostudio://`` URL in clipboard or pasted text."""
    raw = (text or "").strip()
    if not raw:
        return None
    prefix = f"{SCHEME}://"
    prefix_cf = prefix.casefold()

    def _trim_token(candidate: str) -> str:
        token = (candidate or "").strip()
        if not token:
            return ""
        token = token.split()[0]
        # Cut before HTML / markdown delimiters (e.g. href="monostudio://…">).
        for i, ch in enumerate(token):
            if ch in "\"')]}>":
                token = token[:i]
                break
        while token and token[-1] in ".,;":
            token = token[:-1]
        return token

    for line in raw.splitlines():
        candidate = _trim_token(line)
        if candidate.casefold().startswith(prefix_cf):
            return candidate

    if raw.casefold().startswith(prefix_cf):
        return _trim_token(raw)

    pos = raw.casefold().find(prefix_cf)
    if pos >= 0:
        return _trim_token(raw[pos:])
    return None


def monos_link_label_from_clipboard_text(text: str) -> str | None:
    """Human label from :func:`format_monos_link_clipboard` (line 1 before the URL)."""
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    if len(lines) < 2:
        return None
    if extract_monos_deep_link_from_text(lines[0]):
        return None
    if not extract_monos_deep_link_from_text(lines[1]):
        return None
    return lines[0]


def monos_link_display_label_from_url(url: str) -> str | None:
    """Fallback label when clipboard only contains the URL."""
    open_link = parse_open_deep_link(url)
    if open_link is not None:
        primary: str | None = None
        if open_link.trash_id:
            primary = open_link.trash_id
        elif open_link.entity:
            primary = primary_label_from_entity_path(open_link.entity)
        elif open_link.entity_id:
            primary = open_link.entity_id
        return build_monos_link_label(
            page=open_link.page,
            primary=primary,
            department=open_link.department,
            project=open_link.project_id,
        )
    assign = parse_assign_deep_link(url)
    if assign is not None:
        return build_monos_link_label(page="Inbox", primary=assign.inbox_id)
    return None


def resolve_monos_link_paste_label(clipboard_text: str, url: str) -> str | None:
    lab = monos_link_label_from_clipboard_text(clipboard_text)
    if lab:
        return lab
    return monos_link_display_label_from_url(url)


def extract_monos_deep_link_from_clipboard(clipboard) -> tuple[str | None, str]:
    """Find a ``monostudio://`` URL in a Qt clipboard (plain, HTML, or URI list).

    Returns ``(url, text_for_label)``. ``text_for_label`` prefers plain text so
    Discord/HTML pastes still resolve the human label when present.
    """
    plain = ""
    try:
        plain = clipboard.text() or ""
    except Exception:
        plain = ""
    url = extract_monos_deep_link_from_text(plain)
    if url:
        return url, plain

    html = ""
    urls_blob = ""
    try:
        md = clipboard.mimeData()
    except Exception:
        md = None
    if md is not None:
        try:
            if md.hasHtml():
                html = md.html() or ""
        except Exception:
            html = ""
        try:
            if md.hasUrls():
                urls_blob = "\n".join(u.toString() for u in md.urls() if u is not None)
        except Exception:
            urls_blob = ""

    for blob in (html, urls_blob):
        url = extract_monos_deep_link_from_text(blob)
        if url:
            # Keep plain label (if any) above the URL for resolve_monos_link_paste_label.
            label_src = plain.strip()
            if label_src and not extract_monos_deep_link_from_text(label_src):
                return url, f"{label_src}\n{url}"
            return url, blob
    return None, plain
