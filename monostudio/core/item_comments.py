"""
Per-item notes / feedback for assets and shots.

Stored at <item_root>/.monostudio/item_comments.json
Schema v3: rich body_html, mentions, author_id; inline images under note_media/
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
_TAG_RE = re.compile(r"<[^>]+>")
_STYLE_BLOCK_RE = re.compile(r"(?is)<style[^>]*>.*?</style>")
_HEAD_BLOCK_RE = re.compile(r"(?is)<head[^>]*>.*?</head>")
_SCRIPT_BLOCK_RE = re.compile(r"(?is)<script[^>]*>.*?</script>")


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


def count_open_notes(item_root: Path) -> int:
    return sum(1 for e in read_item_comments(item_root) if not e.done)


def notes_badge_visual_mode(item_root: Path) -> tuple[int, str]:
    entries = read_item_comments(item_root)
    if not entries:
        return (0, "empty")
    open_n = sum(1 for e in entries if not e.done)
    if open_n > 0:
        return (open_n, "open")
    return (0, "all_done")


def latest_note_preview_line(item_root: Path, *, max_chars: int = 96) -> tuple[str, bool]:
    entries = read_item_comments(Path(item_root))
    if not entries:
        return ("", False)
    last = entries[-1]
    done = bool(last.done)
    text = entry_preview_text(last, max_chars=max_chars)
    if not text:
        return ("", done)
    if len(text) > max_chars:
        text = text[: max_chars - 1].rstrip() + "…"
    return (text, done)


def new_comment_entry(
    text: str,
    *,
    author: str | None = None,
    author_id: str | None = None,
    body_html: str = "",
    mentions: tuple[str, ...] | None = None,
    entry_id: str | None = None,
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
