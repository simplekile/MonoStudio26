"""Parse and patch Fusion .comp managed Saver paths (text-based, v1)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Literal

from monostudio.core.comp_render_paths import (
    MANAGED_SAVER_NODE_NAME,
    CompSaverSpec,
    saver_paths_match,
)

_TOOLS_MARKERS = (
    "Tools = orderedlist {",
    "Tools = OrderedList {",
    "Tools = ordered() {",
    "Tools = {",
)
_CLIP_FILENAME_RE = re.compile(
    r"Clip\s*=\s*Input\s*\{[\s\S]*?Value\s*=\s*Clip\s*\{[\s\S]*?Filename\s*=\s*\"((?:[^\"\\]|\\.)*)\"",
    re.IGNORECASE,
)
_CLIP_FILENAME_REPLACE_RE = re.compile(
    r"(Clip\s*=\s*Input\s*\{[\s\S]*?Value\s*=\s*Clip\s*\{[\s\S]*?Filename\s*=\s*\")((?:[^\"\\]|\\.)*)(\")",
    re.IGNORECASE,
)
_FILENAME_VALUE_RE = re.compile(
    r"Filename\s*=\s*Input\s*\{[^}]*?Value\s*=\s*\"((?:[^\"\\]|\\.)*)\"",
    re.DOTALL | re.IGNORECASE,
)
_LEGACY_FILENAME_RE = re.compile(
    r"Filename\s*=\s*\"((?:[^\"\\]|\\.)*)\"",
    re.IGNORECASE,
)
_SAVER_TOOL_ASSIGN_RE = re.compile(
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*Saver\s*\{",
    re.IGNORECASE,
)
_NAME_FIELD_RE = re.compile(
    r"Name\s*=\s*\"([^\"]+)\"",
    re.IGNORECASE,
)
_COMPOSITION_START_RE = re.compile(r"\bComposition\s*\{", re.IGNORECASE)
_TOP_LEVEL_TOOLS_RE = re.compile(
    r"(?m)^\tTools = (?:orderedlist |OrderedList |ordered\(\) )?\{",
)
_VIEWINFO_POS_RE = re.compile(r"Pos\s*=\s*\{\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*\}")
_TOOL_ASSIGN_RE = re.compile(
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?P<type>[A-Za-z0-9_.]+)\s*\{",
)
_SAVER_INPUT_CONN_RE = re.compile(
    r"\t\t\t\tInput = Input \{[\s\S]*?\},\n",
    re.MULTILINE,
)
_SAVER_INPUT_SOURCE_OP_RE = re.compile(
    r"Input\s*=\s*Input\s*\{[^}]*SourceOp\s*=\s*\"([^\"]+)\"",
    re.IGNORECASE | re.DOTALL,
)
_END_RENDER_SCRIPT_RE = re.compile(
    r"EndRenderScript\s*=\s*Input\s*\{\s*Value\s*=\s*\"((?:[^\"\\]|\\.)*)\"\s*,?\s*\}",
    re.IGNORECASE,
)
_END_RENDER_SCRIPTS_ENABLE_RE = re.compile(
    r"EndRenderScripts\s*=\s*Input\s*\{\s*Value\s*=\s*1\s*,?\s*\}",
    re.IGNORECASE,
)
_SAVER_VIEW_GAP_X = 200.0
_DEFAULT_SAVER_POS = (200.0, 0.0)
_MANAGED_SAVER_ASSIGN_RE = re.compile(
    rf"{re.escape(MANAGED_SAVER_NODE_NAME)}\s*=\s*Saver\s*\{{",
    re.IGNORECASE,
)


class CompSaverAuditStatus(str, Enum):
    OK = "ok"
    MISMATCH = "mismatch"
    MISSING_MANAGED = "missing_managed"
    UNREADABLE = "unreadable"


@dataclass(frozen=True)
class CompSaverAudit:
    comp_path: Path
    status: CompSaverAuditStatus
    expected_path: str
    current_path: str | None
    managed_tool_var: str | None
    message: str = ""
    has_end_render_script: bool = False


def _unescape_comp_path(path: str) -> str:
    return (path or "").replace("\\\\", "\\")


def _escape_for_comp_string(path: str) -> str:
    return (path or "").replace("\\", "\\\\").replace('"', '\\"')


def _extract_braced_block(text: str, open_brace_index: int) -> tuple[str, int]:
    if open_brace_index < 0 or open_brace_index >= len(text) or text[open_brace_index] != "{":
        return "", open_brace_index
    depth = 0
    i = open_brace_index
    while i < len(text):
        ch = text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[open_brace_index : i + 1], i + 1
        i += 1
    return text[open_brace_index:], len(text)


def trim_comp_text_to_valid_composition(comp_text: str) -> str:
    """Remove trailing garbage appended after the top-level Composition block."""
    m = _COMPOSITION_START_RE.search(comp_text)
    if not m:
        return comp_text
    brace = comp_text.find("{", m.start())
    if brace < 0:
        return comp_text
    _block, end = _extract_braced_block(comp_text, brace)
    if end <= 0 or end > len(comp_text):
        return comp_text
    trimmed = comp_text[:end].rstrip() + "\n"
    return trimmed


def _tools_section_bounds_any(comp_text: str) -> tuple[int, int] | None:
    for marker in _TOOLS_MARKERS:
        idx = comp_text.find(marker)
        if idx < 0:
            continue
        brace = comp_text.find("{", idx)
        if brace < 0:
            continue
        _block, end = _extract_braced_block(comp_text, brace)
        if end > brace:
            return brace, end
    return None


def _tools_section_bounds(comp_text: str) -> tuple[int, int] | None:
    """Bounds of the composition root flow-graph Tools block (not nested viewer Tools)."""
    m = _TOP_LEVEL_TOOLS_RE.search(comp_text)
    if m is None:
        return _tools_section_bounds_any(comp_text)
    brace = comp_text.find("{", m.end() - 1)
    if brace < 0:
        return None
    _block, end = _extract_braced_block(comp_text, brace)
    if end > brace:
        return brace, end
    return None


def _iter_managed_saver_spans(comp_text: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for m in _MANAGED_SAVER_ASSIGN_RE.finditer(comp_text):
        brace = comp_text.find("{", m.end() - 1)
        if brace < 0:
            continue
        _block, end = _extract_braced_block(comp_text, brace)
        if end <= brace:
            continue
        trail = end
        while trail < len(comp_text) and comp_text[trail] in " \t\r\n":
            trail += 1
        if trail < len(comp_text) and comp_text[trail] == ",":
            trail += 1
        spans.append((m.start(), trail))
    return spans


def _remove_text_span(comp_text: str, start: int, end: int) -> str:
    lead = start
    while lead > 0 and comp_text[lead - 1] in " \t":
        lead -= 1
    if lead > 0 and comp_text[lead - 1] == ",":
        lead -= 1
    while lead > 0 and comp_text[lead - 1] in " \t\r\n":
        lead -= 1
    return comp_text[:lead] + comp_text[end:]


def strip_misplaced_managed_savers(comp_text: str) -> str:
    """Remove MONOS_Output Saver nodes outside the root composition Tools block."""
    root_bounds = _tools_section_bounds(comp_text)
    out = comp_text
    for start, end in reversed(_iter_managed_saver_spans(comp_text)):
        if root_bounds is not None and root_bounds[0] <= start < root_bounds[1]:
            continue
        out = _remove_text_span(out, start, end)
    return out


def _filename_from_saver_block(block: str) -> str | None:
    m = _CLIP_FILENAME_RE.search(block)
    if m:
        return _unescape_comp_path(m.group(1))
    m = _FILENAME_VALUE_RE.search(block)
    if m:
        return _unescape_comp_path(m.group(1))
    m = _LEGACY_FILENAME_RE.search(block)
    if m:
        return _unescape_comp_path(m.group(1))
    return None


def _format_end_render_script_inputs(lua: str) -> str:
    escaped = escape_lua_for_comp_value(lua)
    return (
        f"\t\t\t\tEndRenderScripts = Input {{ Value = 1, }},\n"
        f'\t\t\t\tEndRenderScript = Input {{ Value = "{escaped}", }},\n'
    )


def escape_lua_for_comp_value(lua: str) -> str:
    return lua.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _ensure_field_comma_before(block: str, idx: int) -> str:
    """Ensure the Inputs field line before *idx* ends with ',' when more fields follow."""
    prefix = block[:idx]
    stripped = prefix.rstrip(" \t\r\n")
    if not stripped or stripped.endswith(","):
        return block
    trailing = prefix[len(stripped) :]
    return stripped + "," + trailing + block[idx:]


def apply_end_render_script_to_saver_block(block: str, lua: str) -> str:
    """Insert or replace Saver EndRenderScript fields."""
    escaped = escape_lua_for_comp_value(lua)
    script_line = f'\t\t\t\tEndRenderScript = Input {{ Value = "{escaped}", }},\n'
    if _END_RENDER_SCRIPT_RE.search(block):
        out = _END_RENDER_SCRIPT_RE.sub(
            f'EndRenderScript = Input {{ Value = "{escaped}", }}',
            block,
            count=1,
        )
        if not _END_RENDER_SCRIPTS_ENABLE_RE.search(out):
            enable = "\t\t\t\tEndRenderScripts = Input { Value = 1, },\n"
            m = _END_RENDER_SCRIPT_RE.search(out)
            if m:
                out = out[: m.start()] + enable + out[m.start() :]
        return out
    enable_m = _END_RENDER_SCRIPTS_ENABLE_RE.search(block)
    if enable_m:
        insert_at = enable_m.end()
        line_end = block.find("\n", insert_at)
        if line_end < 0:
            line_end = len(block)
        return block[:line_end] + "\n" + script_line + block[line_end:]
    fields = _format_end_render_script_inputs(lua)
    marker = "\t\t\t},"
    idx = block.rfind(marker)
    if idx < 0:
        return block
    block = _ensure_field_comma_before(block, idx)
    return block[:idx] + fields + block[idx:]


def _replace_filename_in_block(block: str, new_path: str) -> str:
    escaped = _escape_for_comp_string(new_path)
    if _CLIP_FILENAME_REPLACE_RE.search(block):
        return _CLIP_FILENAME_REPLACE_RE.sub(
            lambda m: f"{m.group(1)}{escaped}{m.group(3)}",
            block,
            count=1,
        )
    val_re = re.compile(r'Value\s*=\s*"((?:[^"\\]|\\.)*)"')

    def _repl_input(m: re.Match[str]) -> str:
        return val_re.sub(lambda _vm: f'Value = "{escaped}"', m.group(0), count=1)

    if _FILENAME_VALUE_RE.search(block):
        return _FILENAME_VALUE_RE.sub(_repl_input, block, count=1)
    if _LEGACY_FILENAME_RE.search(block):
        return _LEGACY_FILENAME_RE.sub(lambda _m: f'Filename = "{escaped}"', block, count=1)
    return block


def _iter_saver_blocks(comp_text: str) -> list[tuple[str, str, int, int]]:
    """Return (tool_var, block_text, start, end) for each Saver inside Tools."""
    bounds = _tools_section_bounds(comp_text)
    if bounds is None:
        return []
    tools_start, tools_end = bounds
    section = comp_text[tools_start:tools_end]
    out: list[tuple[str, str, int, int]] = []
    for m in _SAVER_TOOL_ASSIGN_RE.finditer(section):
        name = m.group("name")
        brace_idx = section.find("{", m.end() - 1)
        if brace_idx < 0:
            continue
        block, end_idx = _extract_braced_block(section, brace_idx)
        if not block:
            continue
        out.append((name, block, tools_start + brace_idx, tools_start + end_idx))
    return out


def _managed_saver_block(comp_text: str) -> tuple[str, str, int, int] | None:
    """
    Resolve the Saver MonoStudio should manage:
    MONOS_Output → Name=MONOS_Output → Saver1 → sole Saver in Tools.
    """
    blocks = _iter_saver_blocks(comp_text)
    if not blocks:
        return None

    for tool_var, block, start, end in blocks:
        if tool_var == MANAGED_SAVER_NODE_NAME:
            return tool_var, block, start, end
    for tool_var, block, start, end in blocks:
        nm = _NAME_FIELD_RE.search(block)
        if nm and nm.group(1) == MANAGED_SAVER_NODE_NAME:
            return tool_var, block, start, end
    for tool_var, block, start, end in blocks:
        if tool_var == "Saver1":
            return tool_var, block, start, end
    if len(blocks) == 1:
        return blocks[0]
    return None


def read_comp_text(comp_path: Path) -> str:
    raw = comp_path.read_text(encoding="utf-8", errors="replace")
    trimmed = trim_comp_text_to_valid_composition(raw)
    return strip_misplaced_managed_savers(trimmed)


def _managed_saver_has_end_render_script(block: str) -> bool:
    """True when managed Saver has EndRenderScripts enabled and non-empty Lua."""
    if not _END_RENDER_SCRIPTS_ENABLE_RE.search(block):
        return False
    m = _END_RENDER_SCRIPT_RE.search(block)
    if not m:
        return False
    lua = m.group(1).replace("\\n", "\n").replace('\\"', '"').strip()
    return bool(lua)


def _audit_fields_from_managed_block(
    block: str,
    *,
    status: CompSaverAuditStatus,
    expected: str,
    current: str | None,
    tool_var: str | None,
    message: str = "",
) -> CompSaverAudit:
    return CompSaverAudit(
        comp_path=Path("."),
        status=status,
        expected_path=expected,
        current_path=current,
        managed_tool_var=tool_var,
        message=message,
        has_end_render_script=_managed_saver_has_end_render_script(block),
    )


def audit_comp_saver_text(comp_text: str, spec: CompSaverSpec) -> CompSaverAudit:
    """Audit in-memory comp text (comp_path placeholder for messages)."""
    expected = spec.saver_path_fusion
    hit = _managed_saver_block(comp_text)
    if hit is None:
        return CompSaverAudit(
            comp_path=Path("."),
            status=CompSaverAuditStatus.MISSING_MANAGED,
            expected_path=expected,
            current_path=None,
            managed_tool_var=None,
            message="No managed Saver found (MONOS_Output, Saver1, or sole Saver).",
        )
    tool_var, block, _start, _end = hit
    current = _filename_from_saver_block(block)
    if saver_paths_match(expected, current):
        return _audit_fields_from_managed_block(
            block,
            status=CompSaverAuditStatus.OK,
            expected=expected,
            current=current,
            tool_var=tool_var,
        )
    return _audit_fields_from_managed_block(
        block,
        status=CompSaverAuditStatus.MISMATCH,
        expected=expected,
        current=current,
        tool_var=tool_var,
        message="Saver output path does not match pipeline convention.",
    )


def audit_comp_saver(comp_path: Path, spec: CompSaverSpec) -> CompSaverAudit:
    expected = spec.saver_path_fusion
    try:
        if not comp_path.is_file():
            return CompSaverAudit(
                comp_path=comp_path,
                status=CompSaverAuditStatus.UNREADABLE,
                expected_path=expected,
                current_path=None,
                managed_tool_var=None,
                message="Comp file not found.",
            )
        text = read_comp_text(comp_path)
    except OSError as e:
        return CompSaverAudit(
            comp_path=comp_path,
            status=CompSaverAuditStatus.UNREADABLE,
            expected_path=expected,
            current_path=None,
            managed_tool_var=None,
            message=str(e),
        )

    inner = audit_comp_saver_text(text, spec)
    return CompSaverAudit(
        comp_path=comp_path,
        status=inner.status,
        expected_path=inner.expected_path,
        current_path=inner.current_path,
        managed_tool_var=inner.managed_tool_var,
        message=inner.message,
        has_end_render_script=inner.has_end_render_script,
    )


def _fmt_flow_coord(value: float) -> str:
    if float(value).is_integer():
        return str(int(value))
    return str(value)


def _iter_root_flow_tools(comp_text: str) -> list[tuple[str, str, str]]:
    """Return (name, type, block) for each direct child tool in the composition root Tools."""
    bounds = _tools_section_bounds(comp_text)
    if bounds is None:
        return []
    section = comp_text[bounds[0] : bounds[1]]
    out: list[tuple[str, str, str]] = []
    i = 1
    end_limit = len(section) - 1
    while i < end_limit:
        while i < end_limit and section[i] in " \t\r\n,":
            i += 1
        if i >= end_limit:
            break
        m = _TOOL_ASSIGN_RE.match(section, i)
        if not m:
            i += 1
            continue
        name, tool_type = m.group("name"), m.group("type")
        brace = section.find("{", m.end() - 1)
        if brace < 0:
            i += 1
            continue
        block, end = _extract_braced_block(section, brace)
        if not block:
            i += 1
            continue
        out.append((name, tool_type, block))
        i = end
    return out


def _rightmost_view_position(comp_text: str) -> tuple[float, float]:
    """Return flow position for a new Saver: to the right of the rightmost tool."""
    source = find_rightmost_flow_tool_name(comp_text)
    if source is None:
        return _DEFAULT_SAVER_POS
    for name, _tool_type, block in _iter_root_flow_tools(comp_text):
        if name != source:
            continue
        pos_m = _VIEWINFO_POS_RE.search(block)
        if pos_m:
            x = float(pos_m.group(1))
            y = float(pos_m.group(2))
            return x + _SAVER_VIEW_GAP_X, y
    return _DEFAULT_SAVER_POS


def find_rightmost_flow_tool_name(
    comp_text: str,
    *,
    exclude_names: tuple[str, ...] = (),
) -> str | None:
    """Name of the rightmost non-Saver tool in the root flow graph (by ViewInfo Pos X)."""
    exclude = {MANAGED_SAVER_NODE_NAME, *exclude_names}
    best_name: str | None = None
    best_x: float | None = None
    for name, tool_type, block in _iter_root_flow_tools(comp_text):
        if tool_type == "Saver" or name in exclude:
            continue
        pos_m = _VIEWINFO_POS_RE.search(block)
        if not pos_m:
            continue
        x = float(pos_m.group(1))
        if best_x is None or x > best_x:
            best_x = x
            best_name = name
    return best_name


def _format_saver_input_connection(source_op: str) -> str:
    return (
        f"\t\t\t\tInput = Input {{\n"
        f'\t\t\t\t\tSourceOp = "{source_op}",\n'
        f'\t\t\t\t\tSource = "Output",\n'
        f"\t\t\t\t}},\n"
    )


def saver_input_source_op(block: str) -> str | None:
    """SourceOp from a Saver block's flow Input, if present."""
    m = _SAVER_INPUT_SOURCE_OP_RE.search(block)
    if not m:
        return None
    name = (m.group(1) or "").strip()
    return name or None


def managed_saver_is_connected(comp_text: str) -> bool:
    """True when the managed Saver already has a flow Input connection."""
    hit = _managed_saver_block(comp_text)
    if hit is None:
        return False
    tool_var, block, _start, _end = hit
    source = saver_input_source_op(block)
    return bool(source) and source != tool_var


def _connect_saver_block(block: str, source_op: str) -> str:
    conn = _format_saver_input_connection(source_op)
    if _SAVER_INPUT_CONN_RE.search(block):
        return _SAVER_INPUT_CONN_RE.sub(conn, block, count=1)
    inputs_idx = block.find("Inputs = {")
    if inputs_idx < 0:
        return block
    insert_at = inputs_idx + len("Inputs = {")
    if insert_at < len(block) and block[insert_at] == "\n":
        insert_at += 1
    return block[:insert_at] + "\n" + conn + block[insert_at:]


def _connect_managed_saver(comp_text: str, source_op: str) -> str:
    hit = _managed_saver_block(comp_text)
    if hit is None:
        return comp_text
    _tool_var, block, start, end = hit
    updated = _connect_saver_block(block, source_op)
    if updated == block:
        return comp_text
    return comp_text[:start] + updated + comp_text[end:]


def _format_saver_inputs_inner(escaped_path: str, connect_source: str | None) -> str:
    parts: list[str] = []
    if connect_source:
        parts.append(_format_saver_input_connection(connect_source))
    parts.append(
        f"\t\t\t\tClip = Input {{\n"
        f"\t\t\t\t\tValue = Clip {{\n"
        f'\t\t\t\t\t\tFilename = "{escaped_path}",\n'
        f'\t\t\t\t\t\tFormatID = "OpenEXRFormat",\n'
        f"\t\t\t\t\t}},\n"
        f"\t\t\t\t}},\n"
    )
    return "".join(parts)


def _format_saver_viewinfo(pos_x: float, pos_y: float) -> str:
    return (
        f"\t\t\tViewInfo = OperatorInfo {{\n"
        f"\t\t\t\tPos = {{ {_fmt_flow_coord(pos_x)}, {_fmt_flow_coord(pos_y)} }},\n"
        f"\t\t\t\tFlags = {{\n"
        f"\t\t\t\t\tShowPic = true\n"
        f"\t\t\t\t}},\n"
        f"\t\t\t}},\n"
    )


def _inject_managed_saver(
    comp_text: str,
    saver_path: str,
    *,
    connect_source: str | None = None,
) -> str | None:
    """Insert MONOS_Output inside root Tools using Fusion 21-compatible Clip format."""
    comp_text = strip_misplaced_managed_savers(comp_text)
    if _managed_saver_block(comp_text) is not None:
        return comp_text
    escaped = _escape_for_comp_string(saver_path)
    pos_x, pos_y = _rightmost_view_position(comp_text)
    viewinfo = _format_saver_viewinfo(pos_x, pos_y)
    inputs_inner = _format_saver_inputs_inner(escaped, connect_source)
    insertion = (
        f"\t\t{MANAGED_SAVER_NODE_NAME} = Saver {{\n"
        f"\t\t\tInputs = {{\n"
        f"{inputs_inner}"
        f"\t\t\t}},\n"
        f'\t\t\tName = "{MANAGED_SAVER_NODE_NAME}",\n'
        f"{viewinfo}"
        f"\t\t}},\n"
    )
    bounds = _tools_section_bounds(comp_text)
    if bounds is None:
        return None
    _start, end = bounds
    # Insert before Tools closing brace: find last comma+newline before end.
    inner_end = end - 1
    while inner_end > _start and comp_text[inner_end] in " \t\r\n":
        inner_end -= 1
    if inner_end <= _start or comp_text[inner_end] != "}":
        return None
    return comp_text[:inner_end] + ",\n" + insertion + comp_text[inner_end:]


def apply_comp_saver_fix(
    comp_path: Path,
    spec: CompSaverSpec,
    *,
    create_if_missing: bool = False,
    connect_to_rightmost: bool = False,
    end_render_script: bool = False,
    project_root: Path | None = None,
    workspace_root: Path | None = None,
) -> Literal["updated", "unchanged", "failed"]:
    """Patch managed Saver path (and optionally inject MONOS_Output / EndRenderScript)."""
    try:
        text = read_comp_text(comp_path)
    except OSError:
        return "failed"

    audit = audit_comp_saver(comp_path, spec)
    if audit.status == CompSaverAuditStatus.OK and not connect_to_rightmost and not end_render_script:
        return "unchanged"

    new_text = text
    if audit.status == CompSaverAuditStatus.MISSING_MANAGED:
        if not create_if_missing:
            return "failed"
        source = (
            find_rightmost_flow_tool_name(new_text) if connect_to_rightmost else None
        )
        injected = _inject_managed_saver(
            new_text,
            spec.saver_path_fusion,
            connect_source=source,
        )
        if injected is None:
            return "failed"
        new_text = injected
    elif audit.status == CompSaverAuditStatus.MISMATCH:
        hit = _managed_saver_block(new_text)
        if hit is None:
            return "failed"
        _tool_var, block, start, end = hit
        updated_block = _replace_filename_in_block(block, spec.saver_path_fusion)
        if updated_block == block:
            return "failed"
        new_text = new_text[:start] + updated_block + new_text[end:]
    elif audit.status != CompSaverAuditStatus.OK:
        return "failed"

    if connect_to_rightmost and not managed_saver_is_connected(new_text):
        source = find_rightmost_flow_tool_name(new_text)
        if source:
            connected = _connect_managed_saver(new_text, source)
            new_text = connected

    if end_render_script:
        from monostudio.core.comp_fusion_scripts import (
            build_saver_end_render_lua,
            ensure_project_fusion_discord_script,
            find_project_root,
        )

        root = project_root or find_project_root(comp_path)
        if root is None:
            return "failed"
        discord_py = ensure_project_fusion_discord_script(root, workspace_root=workspace_root)
        lua = build_saver_end_render_lua(discord_py)
        hit = _managed_saver_block(new_text)
        if hit is None:
            return "failed"
        tool_var, block, start, end = hit
        updated_block = apply_end_render_script_to_saver_block(block, lua)
        if updated_block != block:
            new_text = new_text[:start] + updated_block + new_text[end:]

    if new_text == text:
        return "unchanged"

    post = audit_comp_saver_text(new_text, spec)
    if post.status != CompSaverAuditStatus.OK and not end_render_script:
        return "failed"

    try:
        comp_path.write_text(new_text, encoding="utf-8")
    except OSError:
        return "failed"

    try:
        spec.render_dir_absolute.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return "updated"


def repair_comp_file(comp_path: Path) -> bool:
    """Strip invalid trailing content and misplaced MONOS_Output; write backup when changed."""
    try:
        raw = comp_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    fixed = trim_comp_text_to_valid_composition(raw)
    fixed = strip_misplaced_managed_savers(fixed)
    if fixed == raw:
        return False
    backup = comp_path.with_suffix(comp_path.suffix + ".monos.bak")
    try:
        backup.write_text(raw, encoding="utf-8")
        comp_path.write_text(fixed, encoding="utf-8")
    except OSError:
        return False
    return True


def ensure_render_dir(spec: CompSaverSpec) -> None:
    try:
        spec.render_dir_absolute.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
