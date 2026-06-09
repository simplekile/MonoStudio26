"""Parse and build monostudio:// deep links (Discord → desktop app)."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse, urlencode


SCHEME = "monostudio"
ASSIGN_HOST = "assign"


@dataclass(frozen=True)
class AssignDeepLink:
    inbox_id: str
    action: str = "open"  # open | confirm


def extract_deep_link_from_argv(argv: list[str]) -> str | None:
    for arg in argv[1:]:
        text = (arg or "").strip()
        if text.lower().startswith(f"{SCHEME}://"):
            return text
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
