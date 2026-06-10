"""Discord webhook test scenarios (Settings → Test notifications)."""

from __future__ import annotations

import socket
import time
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable

from monostudio.core.integrations_config import (
    discord_defaults,
    is_valid_discord_webhook_url,
    load_integrations,
)
from monostudio.core.notification_copy import pick_copy
from monostudio.core.notification_preferences import read_notification_vietnamese


@dataclass(frozen=True)
class DiscordTestScenario:
    id: str
    label_vi: str
    label_en: str
    description_vi: str
    description_en: str


_SCENARIOS: tuple[DiscordTestScenario, ...] = (
    DiscordTestScenario(
        "connection",
        "Kiểm tra kết nối",
        "Connection test",
        "Webhook cơ bản — xác nhận URL hoạt động.",
        "Basic webhook — confirms the URL works.",
    ),
    DiscordTestScenario(
        "mention",
        "@mention trong ghi chú",
        "@mention in notes",
        "Ai đó nhắc bạn trong ghi chú pipeline.",
        "Someone @mentioned you in a pipeline note.",
    ),
    DiscordTestScenario(
        "note_done",
        "Ghi chú đánh dấu xong",
        "Note marked done",
        "Ghi chú được đánh dấu hoàn thành.",
        "A note was marked done.",
    ),
    DiscordTestScenario(
        "inbox_received",
        "Inbox — thêm tệp",
        "Inbox — file drop",
        "Tệp mới vào Inbox (client).",
        "New files dropped into Inbox (client).",
    ),
    DiscordTestScenario(
        "outbox_received",
        "Outbox — thêm tệp",
        "Outbox — file drop",
        "Tệp mới vào Outbox.",
        "New files dropped into Outbox.",
    ),
    DiscordTestScenario(
        "inbox_distributed",
        "Inbox — phân phối",
        "Inbox — distributed",
        "Tệp Inbox được phân phối vào pipeline.",
        "Inbox files distributed into the pipeline.",
    ),
    DiscordTestScenario(
        "schedule_due_overdue",
        "Lịch — chỉ quá hạn",
        "Schedule — overdue only",
        "Task trễ deadline (🔴).",
        "Overdue tasks only (🔴).",
    ),
    DiscordTestScenario(
        "schedule_due_today",
        "Lịch — chỉ đến hạn hôm nay",
        "Schedule — due today only",
        "Task đến hạn hôm nay (⚠️).",
        "Tasks due today only (⚠️).",
    ),
    DiscordTestScenario(
        "schedule_due_mixed",
        "Lịch — quá hạn + đến hạn hôm nay",
        "Schedule — overdue + due today",
        "Cả hai nhóm trong một thông báo.",
        "Both groups in one notification.",
    ),
    DiscordTestScenario(
        "schedule_assigned_pending",
        "Giao việc — chờ xác nhận",
        "Assignment — pending",
        "Giao việc mới với nút Thao tác.",
        "New assignment with action links.",
    ),
    DiscordTestScenario(
        "schedule_assigned_confirmed",
        "Giao việc — đã xác nhận",
        "Assignment — confirmed",
        "Giao việc đã được xác nhận.",
        "Assignment confirmed.",
    ),
)


def list_discord_test_scenarios() -> list[DiscordTestScenario]:
    return list(_SCENARIOS)


def scenario_label(scenario: DiscordTestScenario, *, vietnamese: bool | None = None) -> str:
    return pick_copy(scenario.label_vi, scenario.label_en, vietnamese=vietnamese)


def scenario_description(scenario: DiscordTestScenario, *, vietnamese: bool | None = None) -> str:
    return pick_copy(scenario.description_vi, scenario.description_en, vietnamese=vietnamese)


def _test_project_name() -> str:
    return "demo_project"


def _schedule_due_items_overdue_only(today: date) -> list[dict[str, Any]]:
    past = (today - timedelta(days=5)).isoformat()
    return [
        {
            "entity_kind": "asset",
            "entity_name": "char_ZephysProp",
            "entity_rel": "assets/character/char_ZephysProp",
            "department": "grooming",
            "department_label": "Grooming",
            "due": past,
            "overdue": True,
        },
        {
            "entity_kind": "asset",
            "entity_name": "veh_TruckContainer",
            "entity_rel": "assets/vehicle/veh_TruckContainer",
            "department": "retopo",
            "department_label": "Retopo",
            "due": (today - timedelta(days=3)).isoformat(),
            "overdue": True,
        },
        {
            "entity_kind": "asset",
            "entity_name": "char_Aoi",
            "entity_rel": "assets/character/char_Aoi",
            "department": "retopo",
            "department_label": "Retopo",
            "due": (today - timedelta(days=1)).isoformat(),
            "overdue": True,
        },
    ]


def _schedule_due_items_today_only(today: date) -> list[dict[str, Any]]:
    due = today.isoformat()
    return [
        {
            "entity_kind": "asset",
            "entity_name": "prop_TestProp",
            "entity_rel": "assets/prop/prop_TestProp",
            "department": "lookdev",
            "department_label": "Lookdev",
            "due": due,
            "overdue": False,
        },
        {
            "entity_kind": "shot",
            "entity_name": "sh_010",
            "entity_rel": "shots/sh_010",
            "department": "anim",
            "department_label": "Anim",
            "due": due,
            "overdue": False,
        },
    ]


def _schedule_due_items_mixed(today: date) -> list[dict[str, Any]]:
    return [
        _schedule_due_items_overdue_only(today)[0],
        _schedule_due_items_today_only(today)[0],
    ]


def _scenario_payload(
    scenario_id: str,
    *,
    user_name: str,
    today: date | None = None,
) -> dict[str, Any] | None:
    ref = today or date.today()
    project = _test_project_name()
    actor = (user_name or "").strip() or "Test User"
    sid = (scenario_id or "").strip()

    if sid == "connection":
        return None

    if sid == "mention":
        return {
            "from_name": actor,
            "from_user_id": "test-user-id",
            "to_user_ids": ["test-target-id"],
            "item_display": "char_TestAsset",
            "snippet": "Please review the latest sculpt pass before EOD.",
            "project_name": project,
            "department": "sculpt",
            "department_label": "Sculpt",
        }

    if sid == "note_done":
        return {
            "from_name": actor,
            "item_display": "char_TestAsset",
            "snippet": "Retopo notes addressed — ready for review.",
            "project_name": project,
            "department": "retopo",
            "department_label": "Retopo",
        }

    if sid == "inbox_received":
        return {
            "actor_name": actor,
            "count": 3,
            "project_name": project,
            "source": "client",
            "date_str": ref.isoformat(),
            "file_names": ["concept_v03.psd", "ref_photo.jpg", "notes.txt"],
            "last_added_at": ref.isoformat(),
        }

    if sid == "outbox_received":
        return {
            "actor_name": actor,
            "count": 2,
            "project_name": project,
            "source": "freelancer",
            "date_str": ref.isoformat(),
            "file_names": ["delivery_v02.fbx", "textures.zip"],
            "last_added_at": ref.isoformat(),
        }

    if sid == "inbox_distributed":
        return {
            "actor_name": actor,
            "count": 4,
            "dest_label": "Reference / char_TestAsset",
            "project_name": project,
            "source": "client",
            "entity_names": ["char_TestAsset", "prop_Crate"],
        }

    if sid == "schedule_due_overdue":
        items = _schedule_due_items_overdue_only(ref)
        return {
            "project_name": project,
            "items": items,
            "overdue_count": len(items),
            "due_today_count": 0,
        }

    if sid == "schedule_due_today":
        items = _schedule_due_items_today_only(ref)
        return {
            "project_name": project,
            "items": items,
            "overdue_count": 0,
            "due_today_count": len(items),
        }

    if sid == "schedule_due_mixed":
        items = _schedule_due_items_mixed(ref)
        return {
            "project_name": project,
            "items": items,
            "overdue_count": sum(1 for i in items if i.get("overdue")),
            "due_today_count": sum(1 for i in items if not i.get("overdue")),
        }

    if sid == "schedule_assigned_pending":
        return {
            "from_user_id": "test-assigner-id",
            "from_name": actor,
            "to_user_ids": ["test-assignee-id"],
            "item_rel": "assets/character/char_TestAsset",
            "item_display": "char_TestAsset",
            "entity_kind": "asset",
            "department": "retopo",
            "department_label": "Retopo",
            "due": (ref + timedelta(days=7)).isoformat(),
            "start": ref.isoformat(),
            "project_name": project,
            "allocation_id": "test-allocation-id",
            "assign_inbox_ids": ["test-assign-inbox-id"],
        }

    if sid == "schedule_assigned_confirmed":
        return {
            "from_user_id": "test-assigner-id",
            "from_name": actor,
            "to_user_ids": ["test-assignee-id"],
            "item_rel": "assets/character/char_TestAsset",
            "item_display": "char_TestAsset",
            "entity_kind": "asset",
            "department": "lookdev",
            "department_label": "Lookdev",
            "due": (ref + timedelta(days=5)).isoformat(),
            "start": ref.isoformat(),
            "project_name": project,
            "allocation_id": "test-allocation-id",
            "assign_inbox_ids": ["test-assign-inbox-id-confirmed"],
        }

    return None


def build_discord_test_body(
    scenario_id: str,
    *,
    workspace_root: Path | str | None,
    user_name: str = "",
    vietnamese: bool | None = None,
) -> dict[str, Any] | None:
    from monostudio.core.discord_webhook import (
        build_inbox_distributed_body,
        build_inbox_received_body,
        build_mention_body,
        build_note_done_body,
        build_outbox_received_body,
        build_schedule_assigned_body,
        build_schedule_due_body,
        build_test_body,
    )

    use_vi = read_notification_vietnamese() if vietnamese is None else vietnamese
    defaults = {"username": "MONOS", "avatar_url": ""}
    if workspace_root is not None:
        defaults = discord_defaults(load_integrations(workspace_root))

    sid = (scenario_id or "").strip()
    if sid == "connection":
        return build_test_body(
            user_name=user_name,
            machine=socket.gethostname(),
            defaults=defaults,
            vietnamese=use_vi,
        )

    payload = _scenario_payload(sid, user_name=user_name)
    if payload is None:
        return None

    if sid == "mention":
        return build_mention_body(
            payload, defaults, workspace_root=workspace_root, vietnamese=use_vi
        )
    if sid == "note_done":
        return build_note_done_body(
            payload, defaults, workspace_root=workspace_root, vietnamese=use_vi
        )
    if sid == "inbox_received":
        return build_inbox_received_body(
            payload, defaults, workspace_root=workspace_root, vietnamese=use_vi
        )
    if sid == "outbox_received":
        return build_outbox_received_body(
            payload, defaults, workspace_root=workspace_root, vietnamese=use_vi
        )
    if sid == "inbox_distributed":
        return build_inbox_distributed_body(
            payload, defaults, workspace_root=workspace_root, vietnamese=use_vi
        )
    if sid.startswith("schedule_due"):
        return build_schedule_due_body(
            payload, defaults, workspace_root=workspace_root, vietnamese=use_vi
        )
    if sid == "schedule_assigned_pending":
        return build_schedule_assigned_body(
            payload,
            defaults,
            workspace_root=workspace_root,
            vietnamese=use_vi,
            confirmed=False,
            include_components=True,
        )
    if sid == "schedule_assigned_confirmed":
        return build_schedule_assigned_body(
            payload,
            defaults,
            workspace_root=workspace_root,
            vietnamese=use_vi,
            confirmed=True,
            confirmer_name=user_name or "Test User",
            include_components=False,
        )
    return None


def resolve_discord_test_webhook_url(
    workspace_root: Path | str | None,
    *,
    url_override: str = "",
) -> str:
    url = (url_override or "").strip()
    if is_valid_discord_webhook_url(url):
        return url
    if workspace_root is None:
        return ""
    config = load_integrations(workspace_root)
    discord = config.get("discord")
    if not isinstance(discord, dict):
        return ""
    for wh in discord.get("webhooks") or []:
        if isinstance(wh, dict):
            candidate = str(wh.get("url") or "").strip()
            if is_valid_discord_webhook_url(candidate):
                return candidate
    return ""


def send_discord_test_scenario(
    workspace_root: Path | str | None,
    scenario_id: str,
    *,
    url_override: str = "",
    user_name: str = "",
) -> tuple[bool, str]:
    """POST one test scenario directly (bypasses dispatch queue and dedupe)."""
    from monostudio.core.discord_webhook import post_webhook

    url = resolve_discord_test_webhook_url(workspace_root, url_override=url_override)
    if not is_valid_discord_webhook_url(url):
        return False, "Enter a valid Discord webhook URL."

    body = build_discord_test_body(
        scenario_id,
        workspace_root=workspace_root,
        user_name=user_name,
    )
    if body is None:
        return False, f"Unknown test scenario: {scenario_id}"

    ok, err = post_webhook(url, body)
    if ok:
        return True, ""
    return False, err or "Webhook test failed."


def send_all_discord_test_scenarios(
    workspace_root: Path | str | None,
    *,
    url_override: str = "",
    user_name: str = "",
    delay_seconds: float = 0.4,
    on_progress: Callable[[str, bool, str], None] | None = None,
) -> tuple[int, int, list[tuple[str, str]]]:
    """Send every scenario in order. Returns (ok_count, fail_count, errors)."""
    ok_count = 0
    fail_count = 0
    errors: list[tuple[str, str]] = []
    scenarios = list_discord_test_scenarios()
    for index, scenario in enumerate(scenarios):
        if index > 0 and delay_seconds > 0:
            time.sleep(delay_seconds)
        ok, err = send_discord_test_scenario(
            workspace_root,
            scenario.id,
            url_override=url_override,
            user_name=user_name,
        )
        if ok:
            ok_count += 1
        else:
            fail_count += 1
            errors.append((scenario.id, err or "Failed"))
        if on_progress is not None:
            on_progress(scenario.id, ok, err)
    return ok_count, fail_count, errors
