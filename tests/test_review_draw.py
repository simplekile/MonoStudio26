"""Tests for review draw layers."""

from __future__ import annotations

from pathlib import Path

from monostudio.core.review_draw import (
    ReviewDrawClip,
    ReviewDrawLayer,
    ReviewDrawStroke,
    apply_eraser_to_strokes,
    delete_keyframe_on_layer,
    delete_layer_from_document,
    draw_visible_at,
    ensure_keyframe_on_layer,
    ensure_layer_in_document,
    hold_frames_for_keyframe,
    holding_keyframe_on_layer,
    keyframe_hold_end,
    layers_content_equal,
    load_draw_layers_for_preview,
    make_draw_layer,
    make_layer_keyframe,
    migrate_clips_to_layers,
    move_keyframe_on_layer,
    onion_has_neighbors,
    onion_strokes_next,
    onion_strokes_prev,
    save_sequence_draw_sidecar,
    set_keyframe_hold,
)


def test_holding_keyframe_step_hold_on_layer() -> None:
    layer = make_draw_layer()
    kf10, _ = ensure_keyframe_on_layer(layer, 10)
    kf20, _ = ensure_keyframe_on_layer(layer, 20)
    assert holding_keyframe_on_layer(layer, 10) is kf10
    assert holding_keyframe_on_layer(layer, 15) is kf10
    assert holding_keyframe_on_layer(layer, 20) is kf20
    assert holding_keyframe_on_layer(layer, 5) is None


def test_one_keyframe_per_frame_per_layer() -> None:
    layer = make_draw_layer()
    kf_a, created_a = ensure_keyframe_on_layer(layer, 12)
    kf_b, created_b = ensure_keyframe_on_layer(layer, 12)
    assert created_a
    assert not created_b
    assert kf_a is kf_b
    assert len(layer.keyframes) == 1


def test_draw_sidecar_v3_roundtrip(tmp_path: Path) -> None:
    from monostudio.core.review_sidecar import sequence_review_sidecar_path

    folder = tmp_path / "seq"
    folder.mkdir()
    stroke = ReviewDrawStroke("arrow", "#ef4444", 3.0, [(0.1, 0.2), (0.5, 0.5)])
    layer = make_draw_layer(name="Notes", keyframes=[make_layer_keyframe(10, strokes=[stroke])])
    save_sequence_draw_sidecar(folder, [layer])
    assert sequence_review_sidecar_path(folder).is_file()
    pub, work, from_local = load_draw_layers_for_preview(folder, sequence=True, total_frames=100)
    assert not from_local
    assert len(pub) == 1
    assert pub[0].keyframes[0].strokes[0].tool == "arrow"
    assert pub[0].keyframes[0].hold_frames == 1
    assert layers_content_equal(pub, work)


def test_v2_sidecar_migrates_to_layers(tmp_path: Path) -> None:
    folder = tmp_path / "seq"
    folder.mkdir()
    sidecar = folder / ".monos.draw.json"
    sidecar.write_text(
        """{
  "version": 2,
  "keyframes": [
    {
      "frame": 10,
      "hold_frames": 1,
      "layers": [
        {"id": "a", "name": "Notes", "visible": true, "strokes": [
          {"tool": "pen", "color": "#fafafa", "width_px": 2.0, "points": [[0,0],[1,1]]}
        ]}
      ]
    },
    {
      "frame": 20,
      "hold_frames": 1,
      "layers": [
        {"id": "b", "name": "Notes", "visible": true, "strokes": [
          {"tool": "arrow", "color": "#ef4444", "width_px": 3.0, "points": [[0.1,0.2],[0.5,0.5]]}
        ]}
      ]
    }
  ]
}""",
        encoding="utf-8",
    )
    pub, work, _ = load_draw_layers_for_preview(folder, sequence=True, total_frames=100)
    assert len(pub) == 1
    assert len(pub[0].keyframes) == 2
    assert int(pub[0].keyframes[0].frame) == 10
    assert int(pub[0].keyframes[1].frame) == 20
    assert layers_content_equal(pub, work)


def test_draw_visible_only_on_exact_keyframe() -> None:
    stroke = ReviewDrawStroke("pen", "#fafafa", 2.0, [(0.0, 0.0), (1.0, 1.0)])
    layer = make_draw_layer(keyframes=[make_layer_keyframe(10, strokes=[stroke])])
    layers = [layer]
    assert draw_visible_at(layers, 10)
    assert not draw_visible_at(layers, 11)
    assert not draw_visible_at(layers, 15)


def test_draw_hold_extends_visibility() -> None:
    stroke = ReviewDrawStroke("pen", "#fafafa", 2.0, [(0.0, 0.0), (1.0, 1.0)])
    kf = make_layer_keyframe(10, strokes=[stroke])
    set_keyframe_hold(kf, 5)
    layer = make_draw_layer(keyframes=[kf])
    layers = [layer]
    assert draw_visible_at(layers, 10)
    assert draw_visible_at(layers, 12)
    assert draw_visible_at(layers, 14)
    assert not draw_visible_at(layers, 15)
    assert keyframe_hold_end(kf, layer.keyframes, total_frames=100) == 14


def test_onion_neighbors_use_adjacent_keyframes_on_layer() -> None:
    stroke_a = ReviewDrawStroke("pen", "#ef4444", 2.0, [(0.1, 0.1), (0.2, 0.2)])
    stroke_b = ReviewDrawStroke("pen", "#22c55e", 2.0, [(0.5, 0.5), (0.6, 0.6)])
    layer = make_draw_layer(
        keyframes=[
            make_layer_keyframe(10, strokes=[stroke_a]),
            make_layer_keyframe(30, strokes=[stroke_b]),
        ]
    )
    layers = [layer]
    assert len(onion_strokes_prev(layers, 20, span=15, active_layer_id=layer.id)) == 1
    assert onion_strokes_prev(layers, 20, span=15, active_layer_id=layer.id)[0].color == "#ef4444"
    assert len(onion_strokes_next(layers, 20, span=15, active_layer_id=layer.id)) == 1
    assert onion_strokes_next(layers, 20, span=15, active_layer_id=layer.id)[0].color == "#22c55e"
    assert not onion_strokes_prev(layers, 20, span=5, active_layer_id=layer.id)
    assert onion_has_neighbors(layers, 20, span=15, active_layer_id=layer.id)
    assert not onion_has_neighbors(layers, 20, span=5, active_layer_id=layer.id)


def test_move_and_delete_keyframe_on_layer() -> None:
    stroke = ReviewDrawStroke("pen", "#fafafa", 2.0, [(0.0, 0.0), (1.0, 1.0)])
    layer = make_draw_layer(keyframes=[make_layer_keyframe(10, strokes=[stroke])])
    assert move_keyframe_on_layer(layer, 10, 20)
    assert layer.keyframes[0].frame == 20
    assert delete_keyframe_on_layer(layer, 20)
    assert not layer.keyframes


def test_delete_layer_from_document() -> None:
    layer_a = make_draw_layer(name="A")
    layer_b = make_draw_layer(name="B")
    layers = [layer_a, layer_b]
    assert delete_layer_from_document(layers, layer_b.id)
    assert len(layers) == 1
    assert layers[0].id == layer_a.id
    assert delete_layer_from_document(layers, layer_a.id)
    assert layers == []


def test_ensure_layer_in_document_creates_layer_one_when_empty() -> None:
    layers: list = []
    layer = ensure_layer_in_document(layers, None)
    assert len(layers) == 1
    assert layer.name == "Layer 1"


def test_new_keyframe_uses_layer_default_hold() -> None:
    layer = make_draw_layer()
    layer.default_hold_frames = 5
    kf, created = ensure_keyframe_on_layer(layer, 12)
    assert created
    assert hold_frames_for_keyframe(kf) == 5


def test_eraser_removes_intersecting_strokes() -> None:
    pen = ReviewDrawStroke("pen", "#ef4444", 3.0, [(0.2, 0.2), (0.8, 0.8)])
    arrow = ReviewDrawStroke("arrow", "#22c55e", 3.0, [(0.1, 0.1), (0.3, 0.3)])
    strokes = [pen, arrow]
    erased = apply_eraser_to_strokes(strokes, [(0.5, 0.5), (0.55, 0.55)], 8.0)
    assert pen not in erased
    assert arrow in erased


def test_migrate_v1_clips_to_layers() -> None:
    stroke = ReviewDrawStroke("pen", "#fafafa", 2.0, [(0.0, 0.0), (1.0, 1.0)])
    clip = ReviewDrawClip("c1", 5, 20, "note", [stroke], created_at="1", author=None)
    layers = migrate_clips_to_layers([clip])
    assert len(layers) == 1
    assert layers[0].keyframes[0].frame == 5
    assert draw_visible_at(layers, 5)
    assert not draw_visible_at(layers, 100)
