"""
Per-item notes / feedback for assets and shots.

Stored at <item_root>/.monostudio/item_comments.json
Schema v3+: rich body_html, mentions, author_id, department; inline images under note_media/
"""
from __future__ import annotations

import json
import re
import shutil
import time
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from getpass import getuser
from html import unescape
from pathlib import Path
from typing import Callable

from monostudio.core.atomic_write import atomic_write_text

COMMENTS_FILENAME = "item_comments.json"
COMMENTS_SCHEMA = 3
NOTE_MEDIA_DIR = "note_media"
_MAX_ENTRIES = 200
_MAX_TEXT_LEN = 4000
_MAX_HTML_LEN = 200_000
_WRITE_RETRIES = 5
_IMAGE_PLACEHOLDER = "[image]"

_MENTION_DATA_RE = re.compile(r'data-user-id="([^"]+)"', re.IGNORECASE)
# Qt QTextDocument.toHtml() drops custom attributes; mentions use anchor href instead.
_MENTION_HREF_RE = re.compile(r'href="mention:([^"]+)"', re.IGNORECASE)
_MENTION_HASH_HREF_RE = re.compile(r'href="#mention-([^"]+)"', re.IGNORECASE)
_MENTION_TOKEN_RE = re.compile(r"@([a-zA-Z0-9_]+)")

MENTION_HREF_PREFIX = "mention:"


def is_mention_note_href(href: str) -> bool:
    """True for @mention anchor hrefs stored in note HTML (not navigable documents)."""
    h = (href or "").strip()
    return h.startswith(MENTION_HREF_PREFIX) or h.startswith("#mention-")


def user_id_from_mention_href(href: str) -> str | None:
    """Extract roster user id from a mention anchor href."""
    h = (href or "").strip()
    if h.startswith(MENTION_HREF_PREFIX):
        uid = h[len(MENTION_HREF_PREFIX) :].strip()
        return uid or None
    if h.startswith("#mention-"):
        uid = h[len("#mention-") :].strip()
        return uid or None
    return None
_TAG_RE = re.compile(r"<[^>]+>")
_STYLE_BLOCK_RE = re.compile(r"(?is)<style[^>]*>.*?</style>")
_HEAD_BLOCK_RE = re.compile(r"(?is)<head[^>]*>.*?</head>")
_SCRIPT_BLOCK_RE = re.compile(r"(?is)<script[^>]*>.*?</script>")


@dataclass(frozen=True)
class NoteEditRevision:
    """Snapshot stored when a note is edited."""

    at: str
    editor: str
    text: str
    body_html: str = ""
    editor_id: str | None = None

    def to_dict(self) -> dict:
        d: dict = {
            "at": self.at,
            "editor": self.editor,
            "text": self.text,
        }
        if self.body_html:
            d["body_html"] = self.body_html
        if self.editor_id:
            d["editor_id"] = self.editor_id
        return d


_MAX_EDIT_HISTORY = 30
_MAX_SEEN_BY = 50


@dataclass(frozen=True)
class NoteSeenBy:
    """Read receipt: a signed-in user opened the full note."""

    user_id: str
    at: str
    name: str = ""

    def to_dict(self) -> dict:
        d = {"user_id": self.user_id, "at": self.at}
        n = (self.name or "").strip()
        if n:
            d["name"] = n[:200]
        return d


@dataclass(frozen=True)
class ItemCommentEntry:
    id: str
    at: str
    author: str
    text: str
    done: bool = False
    done_at: str | None = None
    author_id: str | None = None
    body_html: str = ""
    mentions: tuple[str, ...] = ()
    department: str = ""  # department id (sidebar); empty = legacy / general
    edit_history: tuple[NoteEditRevision, ...] = ()
    seen_by: tuple[NoteSeenBy, ...] = ()

    def to_dict(self) -> dict:
        d: dict = {
            "id": self.id,
            "at": self.at,
            "author": self.author,
            "text": self.text,
            "done": bool(self.done),
        }
        if self.done_at:
            d["done_at"] = self.done_at
        if self.author_id:
            d["author_id"] = self.author_id
        if self.body_html:
            d["body_html"] = self.body_html
        if self.mentions:
            d["mentions"] = list(self.mentions)
        dept = normalize_note_department_id(self.department)
        if dept:
            d["department"] = dept
        if self.edit_history:
            d["edit_history"] = [r.to_dict() for r in self.edit_history]
        if self.seen_by:
            d["seen_by"] = [s.to_dict() for s in self.seen_by]
        return d


def note_media_root(item_root: Path) -> Path:
    return Path(item_root) / ".monostudio" / NOTE_MEDIA_DIR


def note_media_entry_dir(item_root: Path, entry_id: str) -> Path:
    return note_media_root(item_root) / (entry_id or "").strip()


def delete_note_media(item_root: Path, entry_id: str) -> None:
    """Remove all media files for a note entry (best-effort)."""
    d = note_media_entry_dir(item_root, entry_id)
    if not d.is_dir():
        return
    try:
        shutil.rmtree(d)
    except OSError:
        pass


def strip_html_preview(html: str, *, max_len: int = _MAX_TEXT_LEN) -> str:
    """Plain text for previews/search; images become [image]."""
    s = html or ""
    if not s.strip():
        return ""
    s = _STYLE_BLOCK_RE.sub(" ", s)
    s = _HEAD_BLOCK_RE.sub(" ", s)
    s = _SCRIPT_BLOCK_RE.sub(" ", s)
    s = re.sub(r"(?is)<!DOCTYPE[^>]*>", " ", s)
    s = re.sub(r"(?is)</?(?:html|body)[^>]*>", " ", s)
    s = re.sub(r"<img\b[^>]*>", f" {_IMAGE_PLACEHOLDER} ", s, flags=re.IGNORECASE)
    s = _TAG_RE.sub(" ", s)
    s = unescape(s)
    s = re.sub(r"\s+", " ", s).strip()
    if len(s) > max_len:
        s = s[: max_len - 1].rstrip() + "…"
    return s


def _initials_from_display_name(name: str) -> str:
    parts = [p for p in (name or "").replace("_", " ").split() if p]
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


@dataclass(frozen=True)
class NoteAuthorVisual:
    """Avatar + label data for a note author row."""

    name: str
    initials: str
    color_hex: str = "#52525b"
    image_path: Path | None = None
    user_id: str | None = None


def normalize_note_department_id(department_id: str | None) -> str:
    return (department_id or "").strip()


def entry_matches_department(entry: ItemCommentEntry, department_id: str | None) -> bool:
    """Whether a note belongs to the sidebar department view.

    Legacy notes (no ``department``) appear in every department view; the general
    bucket (no department selected) shows only legacy notes.
    """
    entry_dept = normalize_note_department_id(entry.department)
    filter_dept = normalize_note_department_id(department_id)
    if not entry_dept:
        return True
    if not filter_dept:
        return False
    return entry_dept == filter_dept


def entry_author_display(
    entry: ItemCommentEntry,
    workspace_root: Path | None = None,
    *,
    unknown: str = "Unknown",
) -> str:
    """Display name for who wrote a note (roster via author_id, then stored author)."""
    if entry.author_id and workspace_root is not None:
        from monostudio.core.user_identity import get_user

        u = get_user(workspace_root, entry.author_id)
        if u is not None and u.name.strip():
            return u.name.strip()
    author = (entry.author or "").strip()
    if author:
        return author
    return unknown


def entry_author_visual(
    entry: ItemCommentEntry,
    workspace_root: Path | None = None,
    *,
    unknown: str = "Unknown",
) -> NoteAuthorVisual:
    """Resolve roster avatar/initials for a note author."""
    if entry.author_id and workspace_root is not None:
        from monostudio.core.user_identity import avatar_path, get_user

        u = get_user(workspace_root, entry.author_id)
        if u is not None:
            name = u.name.strip() or entry_author_display(entry, workspace_root, unknown=unknown)
            return NoteAuthorVisual(
                name=name,
                initials=u.initials,
                color_hex=u.color_hex or "#3b82f6",
                image_path=avatar_path(workspace_root, u),
                user_id=entry.author_id,
            )
    name = entry_author_display(entry, workspace_root, unknown=unknown)
    return NoteAuthorVisual(
        name=name,
        initials=_initials_from_display_name(name),
        color_hex="#52525b",
        image_path=None,
        user_id=(entry.author_id or "").strip() or None,
    )


def _parse_seen_by(raw: object) -> NoteSeenBy | None:
    if not isinstance(raw, dict):
        return None
    uid = str(raw.get("user_id") or "").strip()
    at = str(raw.get("at") or "").strip()
    if not uid or not at:
        return None
    return NoteSeenBy(
        user_id=uid,
        at=at,
        name=str(raw.get("name") or "").strip()[:200],
    )


def seen_by_display_name(
    seen: NoteSeenBy,
    workspace_root: Path | None = None,
    *,
    unknown: str = "Someone",
) -> str:
    uid = (seen.user_id or "").strip()
    if uid and workspace_root is not None:
        from monostudio.core.user_identity import get_user

        u = get_user(workspace_root, uid)
        if u is not None and u.name.strip():
            return u.name.strip()
    stored = (seen.name or "").strip()
    if stored:
        return stored
    return unknown


def seen_by_visual(
    seen: NoteSeenBy,
    workspace_root: Path | None = None,
    *,
    unknown: str = "Someone",
) -> NoteAuthorVisual:
    """Avatar + label data for a read-receipt row."""
    uid = (seen.user_id or "").strip()
    if uid and workspace_root is not None:
        from monostudio.core.user_identity import avatar_path, get_user

        u = get_user(workspace_root, uid)
        if u is not None:
            name = u.name.strip() or seen_by_display_name(seen, workspace_root, unknown=unknown)
            return NoteAuthorVisual(
                name=name,
                initials=u.initials,
                color_hex=u.color_hex or "#3b82f6",
                image_path=avatar_path(workspace_root, u),
                user_id=uid,
            )
    name = seen_by_display_name(seen, workspace_root, unknown=unknown)
    return NoteAuthorVisual(
        name=name,
        initials=_initials_from_display_name(name),
        color_hex="#52525b",
        image_path=None,
        user_id=uid or None,
    )


def format_seen_by_line(
    entry: ItemCommentEntry,
    workspace_root: Path | None = None,
) -> str:
    """Human-readable read receipt, e.g. ``Seen by Alice and Bob``."""
    if not entry.seen_by:
        return ""
    names = [
        seen_by_display_name(s, workspace_root)
        for s in entry.seen_by
        if (s.user_id or "").strip()
    ]
    if not names:
        return ""
    if len(names) == 1:
        return f"Seen by {names[0]}"
    if len(names) == 2:
        return f"Seen by {names[0]} and {names[1]}"
    return f"Seen by {', '.join(names[:-1])}, and {names[-1]}"


def entry_preview_text(entry: ItemCommentEntry, *, max_chars: int = _MAX_TEXT_LEN) -> str:
    """Human-readable one-line preview for cards, dashboard, list metadata."""
    if (entry.body_html or "").strip():
        preview = strip_html_preview(entry.body_html, max_len=max_chars)
        if preview:
            return preview
    return (entry.text or "").replace("\n", " ").strip()[:max_chars]


def mention_href_for_user(user_id: str) -> str:
    """Anchor href stored in note HTML (survives QTextDocument.toHtml)."""
    return f"{MENTION_HREF_PREFIX}{(user_id or '').strip()}"


def parse_mentions_from_html(html: str) -> tuple[str, ...]:
    """Extract roster user ids from mention anchors / legacy data-user-id spans."""
    ids: list[str] = []
    seen: set[str] = set()
    src = html or ""

    def _add(uid: str) -> None:
        u = uid.strip()
        if u and u not in seen:
            seen.add(u)
            ids.append(u)

    for pattern in (_MENTION_HREF_RE, _MENTION_HASH_HREF_RE, _MENTION_DATA_RE):
        for m in pattern.finditer(src):
            _add(m.group(1))
    return tuple(ids)


def new_entry_id() -> str:
    return uuid.uuid4().hex[:16]


def _comments_path(item_root: Path) -> Path:
    return Path(item_root) / ".monostudio" / COMMENTS_FILENAME


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_edit_revision(raw: object) -> NoteEditRevision | None:
    if not isinstance(raw, dict):
        return None
    at = str(raw.get("at") or "").strip()
    editor = str(raw.get("editor") or "").strip()
    text = str(raw.get("text") or "").strip()
    body_html = str(raw.get("body_html") or "").strip()
    if not at or not (text or body_html):
        return None
    editor_id_raw = raw.get("editor_id")
    editor_id = str(editor_id_raw).strip()[:64] if editor_id_raw else None
    return NoteEditRevision(
        at=at,
        editor=editor or "Someone",
        text=text[:_MAX_TEXT_LEN],
        body_html=body_html[:_MAX_HTML_LEN],
        editor_id=editor_id,
    )


def _parse_entry(raw: object) -> ItemCommentEntry | None:
    if not isinstance(raw, dict):
        return None
    eid = str(raw.get("id") or "").strip()
    at = str(raw.get("at") or "").strip()
    if not eid or not at:
        return None
    body_html = str(raw.get("body_html") or "").strip()[:_MAX_HTML_LEN]
    text_raw = str(raw.get("text") or "").strip()
    if body_html:
        text = strip_html_preview(body_html) or text_raw[:_MAX_TEXT_LEN]
    else:
        text = text_raw[:_MAX_TEXT_LEN]
    if not text and not body_html:
        return None
    author = str(raw.get("author") or "").strip()[:200]
    author_id_raw = raw.get("author_id")
    author_id = str(author_id_raw).strip()[:64] if author_id_raw else None
    done = bool(raw.get("done"))
    done_at = raw.get("done_at")
    done_at_s = str(done_at).strip()[:64] if done_at else None
    if done and not done_at_s:
        done_at_s = at
    if not done:
        done_at_s = None
    mentions_raw = raw.get("mentions")
    mentions: tuple[str, ...] = ()
    if isinstance(mentions_raw, list):
        mentions = tuple(str(x).strip() for x in mentions_raw if str(x).strip())
    elif body_html:
        mentions = parse_mentions_from_html(body_html)
    department = normalize_note_department_id(str(raw.get("department") or ""))
    history_raw = raw.get("edit_history")
    edit_history: tuple[NoteEditRevision, ...] = ()
    if isinstance(history_raw, list):
        revs: list[NoteEditRevision] = []
        for h in history_raw:
            r = _parse_edit_revision(h)
            if r is not None:
                revs.append(r)
        if revs:
            edit_history = tuple(revs[:_MAX_EDIT_HISTORY])
    seen_raw = raw.get("seen_by")
    seen_by: tuple[NoteSeenBy, ...] = ()
    if isinstance(seen_raw, list):
        seen_rows: list[NoteSeenBy] = []
        for s in seen_raw:
            p = _parse_seen_by(s)
            if p is not None:
                seen_rows.append(p)
        if seen_rows:
            seen_by = tuple(seen_rows[:_MAX_SEEN_BY])
    return ItemCommentEntry(
        id=eid,
        at=at,
        author=author,
        text=text,
        done=done,
        done_at=done_at_s,
        author_id=author_id,
        body_html=body_html,
        mentions=mentions,
        department=department,
        edit_history=edit_history,
        seen_by=seen_by,
    )


def _read_raw(item_root: Path) -> dict | None:
    path = _comments_path(item_root)
    try:
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _newer(a: ItemCommentEntry, b: ItemCommentEntry) -> ItemCommentEntry:
    return b if b.at >= a.at else a


def _normalize_entries(entries: list[ItemCommentEntry]) -> list[ItemCommentEntry]:
    by_id: dict[str, ItemCommentEntry] = {}
    for e in entries:
        prev = by_id.get(e.id)
        by_id[e.id] = e if prev is None else _newer(prev, e)
    out = sorted(by_id.values(), key=lambda x: x.at)
    if len(out) > _MAX_ENTRIES:
        out = out[-_MAX_ENTRIES:]
    return out


def department_for_note_id(item_root: Path, note_id: str) -> str:
    """Department id for a note entry, or '' if missing / legacy."""
    nid = (note_id or "").strip()
    if not nid:
        return ""
    for entry in read_item_comments(item_root):
        if entry.id == nid:
            return normalize_note_department_id(entry.department)
    return ""


def read_item_comments(item_root: Path) -> list[ItemCommentEntry]:
    data = _read_raw(item_root)
    if not data:
        return []
    raw_list = data.get("entries")
    if not isinstance(raw_list, list):
        return []
    out: list[ItemCommentEntry] = []
    for raw in raw_list:
        p = _parse_entry(raw)
        if p:
            out.append(p)
    return _normalize_entries(out)


def read_item_comments_for_department(
    item_root: Path,
    department_id: str | None,
) -> list[ItemCommentEntry]:
    return [
        e
        for e in read_item_comments(item_root)
        if entry_matches_department(e, department_id)
    ]


def write_item_comments_for_department(
    item_root: Path,
    department_id: str | None,
    entries: list[ItemCommentEntry],
) -> None:
    """Replace notes for one department; other departments and legacy notes are kept."""
    dept = normalize_note_department_id(department_id)
    all_entries = read_item_comments(item_root)
    kept = [
        e for e in all_entries if normalize_note_department_id(e.department) != dept
    ]
    stamped: list[ItemCommentEntry] = []
    for e in entries:
        if dept and not normalize_note_department_id(e.department):
            stamped.append(replace(e, department=dept))
        else:
            stamped.append(e)
    _write_payload(Path(item_root), kept + stamped)


def _entries_for_department_view(
    item_root: Path,
    department_id: str | None,
) -> list[ItemCommentEntry]:
    return read_item_comments_for_department(item_root, department_id)


def count_open_notes(item_root: Path, department_id: str | None = None) -> int:
    return sum(1 for e in _entries_for_department_view(item_root, department_id) if not e.done)


def notes_badge_visual_mode(
    item_root: Path,
    department_id: str | None = None,
) -> tuple[int, str]:
    entries = _entries_for_department_view(item_root, department_id)
    if not entries:
        return (0, "empty")
    open_n = sum(1 for e in entries if not e.done)
    if open_n > 0:
        return (open_n, "open")
    return (0, "all_done")


def latest_note_preview_line(
    item_root: Path,
    department_id: str | None = None,
    *,
    max_chars: int = 96,
) -> tuple[str, bool]:
    """Preview for main-view tile meta: most recent open note only (skips completed)."""
    entries = _entries_for_department_view(Path(item_root), department_id)
    open_entries = [e for e in entries if not e.done]
    if not open_entries:
        return ("", False)
    last = open_entries[-1]
    text = entry_preview_text(last, max_chars=max_chars)
    if not text:
        return ("", False)
    if len(text) > max_chars:
        text = text[: max_chars - 1].rstrip() + "…"
    return (text, False)


def new_comment_entry(
    text: str,
    *,
    author: str | None = None,
    author_id: str | None = None,
    body_html: str = "",
    mentions: tuple[str, ...] | None = None,
    entry_id: str | None = None,
    department: str | None = None,
) -> ItemCommentEntry:
    """In-memory entry for new note (before save batch)."""
    html = (body_html or "").strip()[:_MAX_HTML_LEN]
    plain = (text or "").strip()
    if html and not plain:
        plain = strip_html_preview(html)
    if not plain and not html:
        raise ValueError("comment text must be non-empty")
    plain = plain[:_MAX_TEXT_LEN]
    auth = (author or "").strip() or (getuser() or "").strip()
    aid = (author_id or "").strip() or None
    mids = mentions if mentions is not None else parse_mentions_from_html(html)
    return ItemCommentEntry(
        id=(entry_id or new_entry_id()),
        at=_utc_now_iso(),
        author=auth,
        text=plain,
        done=False,
        done_at=None,
        author_id=aid,
        body_html=html,
        mentions=tuple(mids),
        department=normalize_note_department_id(department),
    )


def write_item_comments(item_root: Path, entries: list[ItemCommentEntry]) -> None:
    _write_payload(Path(item_root), entries)


def _write_payload(item_root: Path, entries: list[ItemCommentEntry]) -> None:
    payload = {
        "schema": COMMENTS_SCHEMA,
        "entries": [e.to_dict() for e in _normalize_entries(entries)],
    }
    content = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    atomic_write_text(_comments_path(item_root), content, encoding="utf-8")


def _atomic_update(
    item_root: Path,
    updater: Callable[[list[ItemCommentEntry]], list[ItemCommentEntry]],
) -> None:
    root = Path(item_root)
    last_exc: OSError | None = None
    for attempt in range(_WRITE_RETRIES):
        fresh = read_item_comments(root)
        proposed = _normalize_entries(updater(list(fresh)))
        if proposed == fresh:
            return
        try:
            _write_payload(root, proposed)
            return
        except OSError as ex:
            last_exc = ex
            time.sleep(0.05 * (attempt + 1))
    if last_exc:
        raise last_exc


def append_item_comment(item_root: Path, text: str, *, author: str | None = None) -> None:
    new_entry = new_comment_entry(text, author=author)

    def updater(cur: list[ItemCommentEntry]) -> list[ItemCommentEntry]:
        if any(e.id == new_entry.id for e in cur):
            return cur
        return cur + [new_entry]

    _atomic_update(Path(item_root), updater)


def delete_item_comment(item_root: Path, comment_id: str) -> None:
    cid = (comment_id or "").strip()
    if not cid:
        raise ValueError("comment_id required")
    root = Path(item_root)
    if not any(e.id == cid for e in read_item_comments(root)):
        raise ValueError("comment not found")
    delete_note_media(root, cid)

    def updater(cur: list[ItemCommentEntry]) -> list[ItemCommentEntry]:
        return [e for e in cur if e.id != cid]

    _atomic_update(root, updater)


def record_note_seen(
    item_root: Path,
    comment_id: str,
    *,
    user_id: str,
    user_name: str = "",
) -> ItemCommentEntry | None:
    """Persist a read receipt when a signed-in user opens the full note."""
    cid = (comment_id or "").strip()
    uid = (user_id or "").strip()
    if not cid or not uid:
        return None
    root = Path(item_root)
    updated: ItemCommentEntry | None = None

    def updater(cur: list[ItemCommentEntry]) -> list[ItemCommentEntry]:
        nonlocal updated
        out: list[ItemCommentEntry] = []
        changed = False
        for e in cur:
            if e.id != cid:
                out.append(e)
                continue
            author_id = (e.author_id or "").strip()
            if author_id and author_id == uid:
                out.append(e)
                continue
            if any(s.user_id == uid for s in e.seen_by):
                out.append(e)
                continue
            new_seen = e.seen_by + (
                NoteSeenBy(
                    user_id=uid,
                    at=_utc_now_iso(),
                    name=(user_name or "").strip()[:200],
                ),
            )
            if len(new_seen) > _MAX_SEEN_BY:
                new_seen = new_seen[-_MAX_SEEN_BY:]
            entry = replace(e, seen_by=new_seen)
            updated = entry
            out.append(entry)
            changed = True
        return out if changed else cur

    try:
        _atomic_update(root, updater)
    except OSError:
        return None
    return updated


def set_item_comment_done(item_root: Path, comment_id: str, done: bool) -> None:
    cid = (comment_id or "").strip()
    if not cid:
        raise ValueError("comment_id required")
    done_flag = bool(done)
    root = Path(item_root)
    if not any(e.id == cid for e in read_item_comments(root)):
        raise ValueError("comment not found")

    def updater(cur: list[ItemCommentEntry]) -> list[ItemCommentEntry]:
        now = _utc_now_iso()
        out: list[ItemCommentEntry] = []
        for e in cur:
            if e.id != cid:
                out.append(e)
                continue
            if bool(e.done) == done_flag:
                return cur
            if done_flag:
                out.append(replace(e, done=True, done_at=now))
            else:
                out.append(replace(e, done=False, done_at=None))
        return out

    _atomic_update(root, updater)
