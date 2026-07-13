"""Parse and build monostudio:// deep links (navigation + Discord assign)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse, urlencode

SCHEME = "monostudio"
ASSIGN_HOST = "assign"
OPEN_HOST = "open"

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
    entity: str | None = None  # posix path relative to project root
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


def resolve_project_root_in_workspace(workspace_root: Path, project_id: str) -> Path | None:
    from monostudio.core.discord_inbox_debounce import resolve_project_root

    return resolve_project_root(workspace_root, project_id)


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
    department = (qs.get("dept") or qs.get("department") or [""])[0].strip() or None
    type_id = (qs.get("type") or qs.get("type_id") or [""])[0].strip() or None
    trash_id = (qs.get("trash") or qs.get("trash_id") or [""])[0].strip() or None
    return OpenDeepLink(
        project_id=project_id,
        page=page,
        entity=entity,
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
) -> str:
    pid = (project_id or "").strip()
    normalized = normalize_page_name(page)
    if not pid or normalized is None:
        raise ValueError("project_id and page are required")
    params: dict[str, str] = {"project": pid, "page": normalized}
    ent = (entity or "").strip().replace("\\", "/").strip("/")
    if ent:
        params["entity"] = ent
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
        return token.split()[0]

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
