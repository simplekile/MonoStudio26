"""Permanent 68px navigation rail (project switcher + page icons)."""

from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import QEvent, QRectF, QSize, Qt, QSettings, QTimer, Signal
from PySide6.QtGui import QAction, QActionGroup, QColor, QCursor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsDropShadowEffect,
    QListWidget,
    QListWidgetItem,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from monostudio.core.workspace_reader import DiscoveredProject
from monostudio.ui_qt.lucide_icons import lucide_icon
from monostudio.ui_qt.nav_pill_widgets import UnreadDotBadge
from monostudio.ui_qt.nav_rail_expand_item import (
    COLLAPSED_W,
    ICON_INSET_X,
    NavRailExpandItem,
    NavRailFlyout,
    NavRailGroup,
    RAIL_SLOT_W,
)
from monostudio.ui_qt.popup_position import max_popup_height_for_anchor, position_popup_near_anchor
from monostudio.ui_qt.recent_tasks_store import RecentTask
from monostudio.ui_qt.sidebar import (
    Sidebar,
    SidebarContext,
    SidebarWidget,
    _populate_recent_tasks_flat,
    _sep_line,
    _sidebar_filter_list_container,
    _SidebarRecentTaskDelegate,
)
from monostudio.ui_qt.nav_quick_view import contexts_by_slot, format_nav_item_tooltip
from monostudio.ui_qt.style import MONOS_COLORS, MonosMenu, project_accent_color


class SidebarNavRail(QWidget):
    """
    Icon-only vertical nav rail (68px): project switcher, Home (Dashboard),
    Assets/Shots, Inbox/Guide/Outbox/Trash, filter popup trigger, recent tasks.
    Nav items expand on hover (Linear / Discord style) — rail width stays fixed.
    """

    context_changed = Signal(str)
    context_clicked = Signal(str)
    project_switch_requested = Signal(str)
    browse_projects_requested = Signal()
    new_project_requested = Signal()
    filter_requested = Signal()
    recent_task_clicked = Signal(object)
    recent_task_double_clicked = Signal(object)
    clear_recent_tasks_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("SidebarNavRail")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        self.setMinimumWidth(RAIL_SLOT_W)
        self.setMaximumWidth(RAIL_SLOT_W)

        self._scope_context: str = SidebarContext.ASSETS.value
        self._footer_context: str | None = None
        self._home_button: NavRailExpandItem | None = None
        self._home_group_buttons: dict[str, NavRailExpandItem] = {}
        self._scope_buttons: dict[str, NavRailExpandItem] = {}
        self._footer_buttons: dict[str, NavRailExpandItem] = {}
        self._filter_btn: NavRailExpandItem | None = None
        self._filter_popup_active = False
        self._recent_tasks_btn: NavRailExpandItem | None = None
        self._trash_btn: NavRailExpandItem | None = None
        self._last_context_text: str | None = None
        self._filter_source: SidebarWidget | None = None
        self._dashboard_unread_dot: UnreadDotBadge | None = None

        self._flyout_owner: NavRailExpandItem | None = None
        self._nav_flyout: NavRailFlyout | None = None
        self._flyout_hide_timer = QTimer(self)
        self._flyout_hide_timer.setSingleShot(True)
        self._flyout_hide_timer.setInterval(80)
        self._flyout_hide_timer.timeout.connect(self._hide_nav_flyout)

        self._project_menu_closed_at = 0.0
        self._project_menu = MonosMenu(self, rounded=False)
        self._project_menu.setObjectName("ProjectSwitchMenu")
        self._project_menu.setWindowOpacity(1.0)
        self._project_menu.aboutToHide.connect(self._on_project_menu_closed)
        _shadow = QGraphicsDropShadowEffect(self._project_menu)
        _shadow.setBlurRadius(15)
        _shadow.setOffset(0, 8)
        _shadow.setColor(QColor(0, 0, 0, int(255 * 0.40)))
        self._project_menu.setGraphicsEffect(_shadow)

        _RAIL_SECTION_GAP = 8
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(_RAIL_SECTION_GAP)

        _TOP_BAR_HEIGHT = 56
        top_block_56 = QWidget(self)
        top_block_56.setFixedHeight(_TOP_BAR_HEIGHT)
        top_block_56.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        top_block_56_layout = QVBoxLayout(top_block_56)
        top_block_56_layout.setContentsMargins(0, 0, 0, 0)
        top_block_56_layout.setSpacing(0)

        self._project_switch = QToolButton(top_block_56)
        self._project_switch.setObjectName("SidebarCompactProjectSwitch")
        self._project_switch.setToolButtonStyle(Qt.ToolButtonIconOnly)
        self._project_switch.setIcon(self._project_dot_icon("#71717a"))
        self._project_switch.setFixedSize(40, 40)
        self._project_switch.setCursor(Qt.PointingHandCursor)
        self._project_switch.setFocusPolicy(Qt.NoFocus)
        self._project_switch.setToolTip("Switch project")
        self._project_switch.setPopupMode(QToolButton.InstantPopup)
        self._project_switch.clicked.connect(self._show_project_menu)
        self._project_switch.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._project_switch.customContextMenuRequested.connect(self._on_project_switch_context_menu)

        self._project_context_menu = MonosMenu(self)
        browse_act = QAction("Browse all projects…", self._project_context_menu)
        browse_act.triggered.connect(self.browse_projects_requested.emit)
        self._project_context_menu.addAction(browse_act)
        new_act = QAction("New project…", self._project_context_menu)
        new_act.triggered.connect(self.new_project_requested.emit)
        self._project_context_menu.addAction(new_act)
        # Center in the 55px band above the 1px separator (aligned with top bar).
        top_block_56_layout.addStretch(1)
        top_block_56_layout.addWidget(self._project_switch, 0, Qt.AlignmentFlag.AlignHCenter)
        top_block_56_layout.addStretch(1)
        top_block_56_layout.addWidget(_sep_line(top_block_56), 0)
        root.addWidget(top_block_56, 0)

        scroll = QScrollArea(self)
        scroll.setObjectName("SidebarNavRailScroll")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)

        middle = QWidget(scroll)
        middle.setObjectName("SidebarNavRailMiddle")
        middle.setMinimumWidth(RAIL_SLOT_W)
        middle.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Minimum)
        middle_layout = QVBoxLayout(middle)
        middle_layout.setContentsMargins(0, 0, 0, 0)
        middle_layout.setSpacing(_RAIL_SECTION_GAP)
        scroll.setWidget(middle)

        home_group = NavRailGroup(middle, nav_group="home")
        _home_btn = self._make_expand_item("house", "Dashboard", nav_group="home")
        _home_btn.clicked.connect(lambda: self._on_home_clicked())
        self._home_button = home_group.add_item(_home_btn)
        self._dashboard_unread_dot = UnreadDotBadge(self._home_button)
        self._dashboard_unread_dot.move(max(0, self._home_button.width() - 14), 2)
        self._dashboard_unread_dot.hide()

        _guide_btn = self._make_expand_item(
            "folder-open",
            "Project Guide",
            nav_group="home",
        )
        _guide_btn.clicked.connect(
            lambda: self._on_page_clicked(SidebarContext.PROJECT_GUIDE.value)
        )
        self._home_group_buttons[SidebarContext.PROJECT_GUIDE.value] = home_group.add_item(_guide_btn)
        middle_layout.addWidget(home_group, 0, Qt.AlignmentFlag.AlignHCenter)

        middle_layout.addWidget(_sep_line(middle), 0)

        scope_group = NavRailGroup(middle, nav_group="scope")
        for ctx_name, icon_name, label in [
            (SidebarContext.ASSETS.value, "box", "Assets"),
            (SidebarContext.SHOTS.value, "clapperboard", "Shots"),
        ]:
            btn = self._make_expand_item(icon_name, label, nav_group="scope")
            btn.clicked.connect(lambda checked=False, c=ctx_name: self._on_scope_clicked(c))
            self._scope_buttons[ctx_name] = scope_group.add_item(btn)
        middle_layout.addWidget(scope_group, 0, Qt.AlignmentFlag.AlignHCenter)
        middle_layout.addWidget(_sep_line(middle), 0)

        workflow_group = NavRailGroup(middle, nav_group="workflow")
        for ctx_name, icon_name, label in [
            (SidebarContext.INBOX.value, "inbox", "Inbox"),
            (SidebarContext.OUTBOX.value, "send", "Outbox"),
        ]:
            btn = self._make_expand_item(icon_name, label, nav_group="workflow")
            btn.clicked.connect(lambda checked=False, c=ctx_name: self._on_page_clicked(c))
            self._footer_buttons[ctx_name] = workflow_group.add_item(btn)
        middle_layout.addWidget(workflow_group, 0, Qt.AlignmentFlag.AlignHCenter)
        middle_layout.addWidget(_sep_line(middle), 0)

        utility_group = NavRailGroup(middle, nav_group="utility")
        self._filter_btn = self._make_expand_item(
            "sliders-horizontal",
            "Filters",
            object_name="SidebarCompactFilterButton",
            nav_group="utility",
        )
        self._filter_btn.clicked.connect(self._on_filter_clicked)
        utility_group.add_item(self._filter_btn)
        middle_layout.addWidget(utility_group, 0, Qt.AlignmentFlag.AlignHCenter)
        middle_layout.addStretch(1)

        root.addWidget(scroll, 1)

        bottom_group = NavRailGroup(self, nav_group="bottom")
        self._recent_tasks_btn = self._make_expand_item(
            "calendar",
            "Recent tasks",
            object_name="SidebarCompactRecentTasksButton",
            nav_group="bottom",
        )
        self._recent_tasks_btn.clicked.connect(self._show_recent_tasks_popup)
        bottom_group.add_item(self._recent_tasks_btn)

        self._trash_btn = self._make_expand_item(
            "trash-2",
            "Trash",
            nav_group="trash",
        )
        self._trash_btn.clicked.connect(
            lambda: self._on_page_clicked(SidebarContext.TRASH.value)
        )
        bottom_group.add_item(self._trash_btn)

        bottom_wrap = QWidget(self)
        bottom_wrap.setObjectName("SidebarNavRailBottom")
        bottom_layout = QVBoxLayout(bottom_wrap)
        bottom_layout.setContentsMargins(0, 0, 0, 8)
        bottom_layout.setSpacing(0)
        bottom_layout.addWidget(bottom_group, 0, Qt.AlignmentFlag.AlignHCenter)
        root.addWidget(bottom_wrap, 0)

        self._recent_tasks_popup: QFrame | None = None
        self._recent_tasks_list: QListWidget | None = None
        self._recent_tasks: list[RecentTask] = []
        self._recent_tasks_popup_closed_at = 0.0

        self._sync_active_states()

    def _make_expand_item(
        self,
        icon_name: str,
        label: str,
        *,
        object_name: str | None = None,
        nav_group: str = "utility",
    ) -> NavRailExpandItem:
        return NavRailExpandItem(
            self,
            icon_name=icon_name,
            label=label,
            object_name=object_name or "NavRailExpandItem",
            nav_group=nav_group,
        )

    def _expand_host(self) -> QWidget:
        w = self.window()
        return w if w is not None else self

    def _ensure_nav_flyout(self) -> NavRailFlyout:
        if self._nav_flyout is None:
            flyout = NavRailFlyout(self._expand_host())
            flyout.clicked.connect(self._on_flyout_clicked)
            flyout.installEventFilter(self)
            self._nav_flyout = flyout
        return self._nav_flyout

    def _show_nav_flyout(self, item: NavRailExpandItem) -> None:
        self._flyout_hide_timer.stop()
        prev = self._flyout_owner
        if prev is not None and prev is not item:
            prev._icon.show()
        flyout = self._ensure_nav_flyout()
        self._flyout_owner = item
        flyout.set_content(
            icon_name=item._icon_name,
            label=item._label,
            active=item._active,
            nav_group=item._nav_group,
            hovered=True,
        )
        flyout.anchor_to(item, self._expand_host())
        item._icon.hide()
        if prev is not item:
            flyout.set_flyoutWidth(COLLAPSED_W)
        if not flyout.isVisible():
            flyout.show()
        flyout.raise_()
        flyout.animate_expand(True)

    def _schedule_hide_nav_flyout(self, item: NavRailExpandItem) -> None:
        if self._flyout_owner is item:
            self._flyout_hide_timer.start()

    def _hide_nav_flyout(self) -> None:
        flyout = self._nav_flyout
        owner = self._flyout_owner
        if flyout is not None and flyout.underMouse():
            return
        if owner is not None and owner.underMouse():
            return
        if flyout is not None:
            flyout.animate_expand(False)
        if owner is not None:
            owner._icon.show()
        self._flyout_owner = None

    def resync_pointer_hover(self) -> None:
        """Rebuild nav-rail hover after main-view click (flyout + manual item highlight)."""
        pos = QCursor.pos()
        self._flyout_hide_timer.stop()
        flyout = self._nav_flyout
        owner = self._flyout_owner
        if flyout is not None and flyout.isVisible():
            try:
                over_flyout = flyout.rect().contains(flyout.mapFromGlobal(pos))
            except Exception:
                over_flyout = False
            over_owner = False
            if owner is not None:
                try:
                    over_owner = owner.rect().contains(owner.mapFromGlobal(pos))
                except Exception:
                    over_owner = False
            if not over_flyout and not over_owner:
                flyout._width_anim.stop()
                flyout.hide()
                if owner is not None:
                    owner._icon.show()
                self._flyout_owner = None
        for item in self.findChildren(NavRailExpandItem):
            item.sync_hover_from_global(pos)

    def _on_flyout_clicked(self) -> None:
        owner = self._flyout_owner
        if owner is not None:
            owner.clicked.emit()

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        if watched is self._nav_flyout:
            if event.type() == QEvent.Type.Enter:
                self._flyout_hide_timer.stop()
            elif event.type() == QEvent.Type.Leave:
                self._schedule_hide_nav_flyout(self._flyout_owner or watched)
        return super().eventFilter(watched, event)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if self._nav_flyout is not None and self._flyout_owner is not None:
            self._nav_flyout.anchor_to(self._flyout_owner, self._expand_host())

    @staticmethod
    def _project_dot_icon(color_hex: str, *, diameter: int = 6) -> QIcon:
        try:
            dpr = float(QApplication.primaryScreen().devicePixelRatio())
        except Exception:
            dpr = 1.0
        canvas = max(16, diameter + 8)
        dev_w = int(round(canvas * dpr))
        pm = QPixmap(dev_w, dev_w)
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(color_hex))
        cx = canvas / 2.0
        cy = canvas / 2.0
        r = diameter / 2.0
        p.drawEllipse(QRectF(cx - r, cy - r, diameter, diameter))
        p.end()
        return QIcon(pm)

    def set_filter_source(self, filters: SidebarWidget | None) -> None:
        self._filter_source = filters

    def filters(self) -> SidebarWidget | None:
        return self._filter_source

    def set_dashboard_unread(self, has_unread: bool) -> None:
        if self._dashboard_unread_dot is not None:
            self._dashboard_unread_dot.setVisible(bool(has_unread))
            if has_unread:
                self._dashboard_unread_dot.raise_()

    def refresh_quick_view_tooltips(self, settings: QSettings) -> None:
        """Show assigned quick-view slot numbers on nav rail hovers."""
        from monostudio.ui_qt.app_hotkeys import format_nav_quick_hint

        by_ctx = contexts_by_slot(settings)
        hint = format_nav_quick_hint(settings)

        if self._home_button is not None:
            lines = [
                format_nav_item_tooltip("Dashboard", by_ctx.get(SidebarContext.DASHBOARD.value), include_hint=False),
            ]
            sched_slots = by_ctx.get(SidebarContext.SCHEDULE.value)
            if sched_slots:
                lines.append(
                    format_nav_item_tooltip("Schedule", sched_slots, include_hint=False),
                )
            lines.append(hint)
            self._home_button.setToolTip("\n".join(lines))

        _scope_labels = {
            SidebarContext.ASSETS.value: "Assets",
            SidebarContext.SHOTS.value: "Shots",
        }
        for ctx_name, btn in self._scope_buttons.items():
            btn.setToolTip(format_nav_item_tooltip(_scope_labels.get(ctx_name, ctx_name), by_ctx.get(ctx_name)))

        if SidebarContext.PROJECT_GUIDE.value in self._home_group_buttons:
            btn = self._home_group_buttons[SidebarContext.PROJECT_GUIDE.value]
            btn.setToolTip(
                format_nav_item_tooltip("Project Guide", by_ctx.get(SidebarContext.PROJECT_GUIDE.value))
            )

        _footer_labels = {
            SidebarContext.INBOX.value: "Inbox",
            SidebarContext.OUTBOX.value: "Outbox",
        }
        for ctx_name, btn in self._footer_buttons.items():
            btn.setToolTip(format_nav_item_tooltip(_footer_labels.get(ctx_name, ctx_name), by_ctx.get(ctx_name)))

        if self._trash_btn is not None:
            self._trash_btn.setToolTip(
                format_nav_item_tooltip("Trash", by_ctx.get(SidebarContext.TRASH.value))
            )

        if self._recent_tasks_btn is not None:
            self._recent_tasks_btn.setToolTip("Recent tasks")

    def current_context(self) -> str:
        if self._footer_context is not None:
            return self._footer_context
        return self._scope_context

    def set_current_context(self, context_name: str, *, force: bool = False) -> None:
        if not force and context_name == self.current_context():
            return
        if context_name in (SidebarContext.SHOTS.value, SidebarContext.ASSETS.value):
            self._footer_context = None
            self._scope_context = context_name
            self._last_context_text = context_name
            self._sync_active_states()
            self.context_changed.emit(context_name)
            return
        if context_name in (
            SidebarContext.DASHBOARD.value,
            SidebarContext.INBOX.value,
            SidebarContext.PROJECT_GUIDE.value,
            SidebarContext.SCHEDULE.value,
            SidebarContext.OUTBOX.value,
            SidebarContext.TRASH.value,
        ):
            self._footer_context = context_name
            self._last_context_text = context_name
            self._sync_active_states()
            self.context_changed.emit(context_name)

    def _sync_active_states(self) -> None:
        ctx = self.current_context()
        on_scope = self._footer_context is None
        home_active = ctx in (SidebarContext.DASHBOARD.value, SidebarContext.SCHEDULE.value)
        if self._home_button is not None:
            self._home_button.set_active(home_active)
        for name, btn in self._home_group_buttons.items():
            btn.set_active(name == ctx)
        for name, btn in self._scope_buttons.items():
            active = on_scope and name == ctx
            icon_name = "clapperboard" if name == SidebarContext.SHOTS.value else "box"
            btn.set_icon_name(icon_name)
            btn.set_active(active)
        _footer_icons = {
            SidebarContext.INBOX.value: "inbox",
            SidebarContext.OUTBOX.value: "send",
        }
        for name, btn in self._footer_buttons.items():
            active = name == ctx
            btn.set_icon_name(_footer_icons.get(name, "inbox"))
            btn.set_active(active)
        if self._trash_btn is not None:
            self._trash_btn.set_active(ctx == SidebarContext.TRASH.value)
        if self._filter_btn is not None:
            self._filter_btn.set_active(self._filter_popup_active)
        if self._recent_tasks_btn is not None:
            self._recent_tasks_btn.set_active(False)

    def set_filter_popup_active(self, active: bool) -> None:
        """Highlight Filters nav item while the compact filter popup is open."""
        self._filter_popup_active = bool(active)
        if self._filter_btn is not None:
            self._filter_btn.set_active(self._filter_popup_active)

    def _on_home_clicked(self) -> None:
        self._hide_nav_flyout()
        ctx = self.current_context()
        if ctx == SidebarContext.SCHEDULE.value:
            self.set_current_context(SidebarContext.DASHBOARD.value)
            return
        if ctx == SidebarContext.DASHBOARD.value:
            self.context_clicked.emit(SidebarContext.DASHBOARD.value)
            return
        self.set_current_context(SidebarContext.DASHBOARD.value)

    def _on_scope_clicked(self, context_name: str) -> None:
        if context_name == self.current_context():
            self.context_clicked.emit(context_name)
            return
        self.set_current_context(context_name)

    def _on_page_clicked(self, context_name: str) -> None:
        if context_name == self.current_context():
            self.context_clicked.emit(context_name)
            return
        self.set_current_context(context_name)

    def _on_filter_clicked(self) -> None:
        self.filter_requested.emit()

    def _clear_tool_button_hover(self, btn: QWidget) -> None:
        if isinstance(btn, QToolButton):
            QApplication.sendEvent(btn, QEvent(QEvent.Type.Leave))
            btn.setDown(False)
            try:
                st = btn.style()
                if st:
                    st.unpolish(btn)
                    st.polish(btn)
            except Exception:
                pass
            btn.update()
        elif isinstance(btn, NavRailExpandItem):
            self._hide_nav_flyout()
            btn._icon.show()

    def _show_project_menu(self) -> None:
        if self._project_menu.isVisible():
            self._project_menu.close()
            return
        if (time.monotonic() - self._project_menu_closed_at) < self._POPUP_REOPEN_GRACE:
            return
        pos = self._project_switch.mapToGlobal(self._project_switch.rect().bottomLeft())
        self._project_menu.popup(pos)

    def _on_project_menu_closed(self) -> None:
        self._project_menu_closed_at = time.monotonic()
        QTimer.singleShot(0, lambda: self._clear_tool_button_hover(self._project_switch))

    def _on_project_switch_context_menu(self, pos) -> None:
        if not self._project_switch.isEnabled():
            return
        self._project_context_menu.popup(self._project_switch.mapToGlobal(pos))

    def set_projects(
        self,
        projects: list[DiscoveredProject],
        *,
        current_root: Path | None,
        status_by_root: dict[str, str] | None = None,
    ) -> None:
        self._project_menu.clear()
        if not projects:
            self._project_switch.setEnabled(False)
            self._project_switch.setIcon(self._project_dot_icon("#52525b"))
            self._project_switch.setProperty("state", "disabled")
            if self._project_switch.style():
                self._project_switch.style().unpolish(self._project_switch)
                self._project_switch.style().polish(self._project_switch)
            return
        self._project_switch.setEnabled(True)
        current = str(current_root) if current_root else None
        if current_root is None:
            self._project_switch.setIcon(self._project_dot_icon("#71717a"))
            self._project_switch.setProperty("state", "empty")
        else:
            folder_name = current_root.name or ""
            accent = project_accent_color(folder_name)
            self._project_switch.setIcon(self._project_dot_icon(accent, diameter=8))
            self._project_switch.setProperty("state", "active")
        if self._project_switch.style():
            self._project_switch.style().unpolish(self._project_switch)
            self._project_switch.style().polish(self._project_switch)

        group = QActionGroup(self._project_menu)
        group.setExclusive(True)
        for proj in projects:
            label = proj.root.name
            accent = project_accent_color(label)
            is_current = current == str(proj.root)
            dot = self._project_dot_icon(accent, diameter=8 if is_current else 6)
            act = QAction(label, self._project_menu, checkable=True)
            act.setIcon(dot)
            act.setChecked(is_current)
            if is_current:
                f = act.font()
                f.setWeight(QFont.Weight.DemiBold)
                act.setFont(f)
            act.triggered.connect(lambda checked=False, p=str(proj.root): self.project_switch_requested.emit(p))
            group.addAction(act)
            self._project_menu.addAction(act)

    def set_recent_tasks(self, tasks: list[RecentTask]) -> None:
        self._recent_tasks = list(tasks) if tasks else []

    _POPUP_REOPEN_GRACE = 0.25

    def _show_recent_tasks_popup(self) -> None:
        if not self._recent_tasks:
            return
        if self._recent_tasks_popup is not None and self._recent_tasks_popup.isVisible():
            self._recent_tasks_popup.close()
            return
        if (time.monotonic() - self._recent_tasks_popup_closed_at) < self._POPUP_REOPEN_GRACE:
            return

        class _RecentTasksPopupFrame(QFrame):
            def __init__(self, parent, on_hide_cb):
                super().__init__(parent)
                self._on_hide_cb = on_hide_cb

            def hideEvent(self, event):
                self._on_hide_cb()
                super().hideEvent(event)

        def _on_recent_popup_hidden():
            self._recent_tasks_popup_closed_at = time.monotonic()
            self._recent_tasks_popup = None
            self._recent_tasks_list = None
            if self._recent_tasks_btn is not None:
                QTimer.singleShot(0, lambda: self._clear_tool_button_hover(self._recent_tasks_btn))

        popup = _RecentTasksPopupFrame(self, _on_recent_popup_hidden)
        popup.setObjectName("SidebarCompactRecentTasksPopup")
        popup.setWindowFlags(Qt.WindowType.Popup | Qt.FramelessWindowHint)
        popup.setAttribute(Qt.WA_TranslucentBackground, False)
        layout = QVBoxLayout(popup)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(4)
        lst_container = _sidebar_filter_list_container(popup)
        lst = QListWidget(lst_container)
        lst_container.layout().addWidget(lst)
        lst.setObjectName("SidebarRecentTasksList")
        lst.setItemDelegate(_SidebarRecentTaskDelegate(lst))
        lst.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        lst.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        lst.setMouseTracking(True)
        lst.setSpacing(2)
        lst.setMinimumWidth(220)
        anchor = self._recent_tasks_btn or self
        max_popup_h = max_popup_height_for_anchor(anchor, gap=4)
        list_max_h = max(80, max_popup_h - 56)
        _populate_recent_tasks_flat(lst, self._recent_tasks)
        Sidebar._apply_recent_tasks_list_height(lst, max_rows=10, max_height_px=list_max_h)
        if lst.count():
            lst.setCurrentRow(0)
        lst.itemClicked.connect(self._on_popup_task_clicked)
        lst.itemDoubleClicked.connect(self._on_popup_task_double_clicked)
        self._recent_tasks_list = lst
        layout.addWidget(lst_container)
        clear_btn = QToolButton(popup)
        clear_btn.setText("Clear")
        clear_btn.setCursor(Qt.PointingHandCursor)
        clear_btn.clicked.connect(lambda: (popup.close(), self.clear_recent_tasks_requested.emit()))
        layout.addWidget(clear_btn, 0, Qt.AlignmentFlag.AlignRight)
        self._recent_tasks_popup = popup
        position_popup_near_anchor(popup, anchor, gap=4, x_offset=ICON_INSET_X)
        popup.show()

    def _on_popup_task_clicked(self, item: QListWidgetItem) -> None:
        data = item.data(Qt.ItemDataRole.UserRole) if item else None
        if isinstance(data, dict):
            task = data.get("task")
            if isinstance(task, RecentTask):
                self.recent_task_clicked.emit(task)
                return
        if isinstance(data, RecentTask):
            self.recent_task_clicked.emit(data)

    def _on_popup_task_double_clicked(self, item: QListWidgetItem) -> None:
        data = item.data(Qt.ItemDataRole.UserRole) if item else None
        task = data.get("task") if isinstance(data, dict) else data
        if isinstance(task, RecentTask):
            self.recent_task_double_clicked.emit(task)
        if self._recent_tasks_popup:
            self._recent_tasks_popup.close()
