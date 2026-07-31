"""Tests for workspace integrations config (Discord webhooks)."""

from __future__ import annotations

from monostudio.core.integrations_config import (
    build_integrations_from_webhooks,
    is_event_enabled,
    webhook_urls_for_event,
)

_TEST_URL_A = "https://discord.com/api/webhooks/111111111/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
_TEST_URL_B = "https://discord.com/api/webhooks/222222222/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"


def test_build_integrations_from_webhooks_preserves_multiple_channels() -> None:
    config = build_integrations_from_webhooks(
        enabled=True,
        webhooks=[
            {
                "id": "wh_a",
                "label": "#general",
                "url": _TEST_URL_A,
                "events": {"mention": True, "note_done": False},
            },
            {
                "id": "wh_b",
                "label": "#inbox",
                "url": _TEST_URL_B,
                "events": {"mention": False, "inbox_received": True},
            },
        ],
    )
    webhooks = config["discord"]["webhooks"]
    assert len(webhooks) == 2
    assert webhooks[0]["url"] == _TEST_URL_A
    assert webhooks[1]["url"] == _TEST_URL_B
    assert webhooks[0]["events"]["mention"] is True
    assert webhooks[1]["events"]["mention"] is False
    assert webhooks[1]["events"]["inbox_received"] is True


def test_webhook_urls_for_event_filters_per_channel() -> None:
    config = build_integrations_from_webhooks(
        enabled=True,
        webhooks=[
            {
                "url": _TEST_URL_A,
                "events": {"mention": True},
            },
            {
                "url": _TEST_URL_B,
                "events": {"mention": False, "note_done": True},
            },
        ],
    )
    assert webhook_urls_for_event(config, "mention") == [_TEST_URL_A]
    assert webhook_urls_for_event(config, "note_done") == [_TEST_URL_B]


def test_is_event_enabled_any_channel() -> None:
    config = build_integrations_from_webhooks(
        enabled=True,
        webhooks=[
            {"url": _TEST_URL_A, "events": {"mention": False}},
            {"url": _TEST_URL_B, "events": {"schedule_due": True}},
        ],
    )
    assert is_event_enabled(config, "mention") is False
    assert is_event_enabled(config, "schedule_due") is True


def test_webhook_urls_for_fusion_render_finished() -> None:
    config = build_integrations_from_webhooks(
        enabled=True,
        webhooks=[
            {"url": _TEST_URL_A, "events": {"fusion_render_finished": True}},
            {"url": _TEST_URL_B, "events": {"fusion_render_finished": False, "mention": True}},
        ],
    )
    assert webhook_urls_for_event(config, "fusion_render_finished") == [_TEST_URL_A]
