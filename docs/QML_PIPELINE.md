# MONOS — Pipeline QML (v1)

Qt Quick module cho **Main View grid/list**. Shell app vẫn Qt Widgets.

**Plan:** `plan_main_view_engine_v2.mdc`  
**Tokens:** `DESIGN_SYSTEM_MAP.md`  
**Harness:** `python scripts/test_pipeline_qml.py`

---

## Module

```
monostudio/ui_qt/qml/Monos/Pipeline/
  qmldir
  PipelineTheme.qml      # singleton
  PipelineCard.qml
  PipelineGridView.qml
  PipelineGridHarness.qml
  ThumbImage.qml, BadgeChip.qml, StatusPill.qml, DccStack.qml
```

Import: `import Monos.Pipeline 1.0`

Python bridge: `monostudio/ui_qt/pipeline_qml_theme.py`

---

## Embed (production pattern)

```python
from PySide6.QtQuickWidgets import QQuickWidget
from monostudio.ui_qt.pipeline_qml_theme import configure_pipeline_qml_engine, pipeline_harness_qml_url

view = QQuickWidget(parent)
configure_pipeline_qml_engine(view.engine())
view.setSource(pipeline_harness_qml_url("PipelineGridView.qml"))
```

Model: `PipelinePresentationModel` (Sprint 1) thay `ListModel` stub.

---

## Theme sync

1. `MONOS_COLORS` (`style.py`) — source
2. `build_pipeline_theme_map()` — Python flat map
3. `PipelineTheme.qml` — QML singleton (must match)

Verify: `python scripts/test_pipeline_qml.py --verify-theme`

---

## Card layout

See plan §6.1 — type TL, alert TR, DCC BR, status pill + name below 16:9 thumb.

---

*Scaffold Phase D — expand in Sprint 2–5.*
