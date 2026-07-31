"""Tests for image-sequence PNG proxy cache."""

from __future__ import annotations

from pathlib import Path

import pytest

from monostudio.core.sequence_proxy_cache import (
    collect_sequence_frame_stats,
    is_heavy_plate_sequence,
    lookup_sequence_proxy,
    sequence_proxy_paths,
    write_sequence_proxy_manifest,
    SequenceFrameStat,
    SequenceProxyManifest,
)


def test_is_heavy_plate_sequence() -> None:
    assert is_heavy_plate_sequence([Path("a.exr"), Path("b.exr")])
    assert is_heavy_plate_sequence([Path("a.dpx"), Path("b.dpx")])
    assert not is_heavy_plate_sequence([Path("a.png"), Path("b.png")])
    assert not is_heavy_plate_sequence([])


def test_collect_sequence_frame_stats_stable_digest(tmp_path: Path) -> None:
    f0 = tmp_path / "shot.0001.exr"
    f1 = tmp_path / "shot.0002.exr"
    f0.write_bytes(b"exr0")
    f1.write_bytes(b"exr1")
    d1, stats1 = collect_sequence_frame_stats([f0, f1])
    d2, stats2 = collect_sequence_frame_stats([f0, f1])
    assert d1 == d2
    assert len(stats1) == 2
    assert stats1 == stats2
    assert stats1[0].proxy_name == "000000.png"


def test_lookup_sequence_proxy_validates_png_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from monostudio.core import sequence_proxy_cache as mod

    cache_root = tmp_path / "cache"
    monkeypatch.setattr(mod, "sequence_proxy_cache_dir", lambda: cache_root)

    f0 = tmp_path / "src" / "a.0001.exr"
    f1 = tmp_path / "src" / "a.0002.exr"
    f0.parent.mkdir(parents=True)
    f0.write_bytes(b"x")
    f1.write_bytes(b"y")
    frames = [f0, f1]
    digest, stats = collect_sequence_frame_stats(frames)
    ocio = "off"
    proxy_root, manifest_path = sequence_proxy_paths(digest, scale=0.5, ocio_token=ocio)
    frames_dir = proxy_root / "frames"
    frames_dir.mkdir(parents=True)
    for stat in stats:
        (frames_dir / stat.proxy_name).write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 64)
    manifest = SequenceProxyManifest(
        sequence_folder=str(f0.parent),
        frame_count=2,
        scale=0.5,
        ocio_token=ocio,
        proxy_dir=str(proxy_root),
        frames=stats,
        width=960,
        height=540,
    )
    write_sequence_proxy_manifest(manifest_path, manifest)
    got = lookup_sequence_proxy(frames, scale=0.5, ocio_token=ocio)
    assert got is not None
    assert len(got.proxy_frame_paths()) == 2

    # Legacy v1 manifests are rejected after cache bump.
    manifest_path.write_text(
        manifest_path.read_text(encoding="utf-8").replace('"version": 2', '"version": 1'),
        encoding="utf-8",
    )
    assert lookup_sequence_proxy(frames, scale=0.5, ocio_token=ocio) is None

    write_sequence_proxy_manifest(manifest_path, manifest)
    got = lookup_sequence_proxy(frames, scale=0.5, ocio_token=ocio)
    assert got is not None

    f0.write_bytes(b"changed")
    assert lookup_sequence_proxy(frames, scale=0.5, ocio_token=ocio) is None
