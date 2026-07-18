# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Marco Sumari Tellez and IngeCAD contributors.
"""IngeCAD main window — classic pre-ribbon layout.

Menu bar + (from Phase 3) dockable toolbars, command line at the bottom, and a
status bar with coordinate readout and mode toggles. The ribbon does not exist
and will never exist here.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QSettings, Qt, QThread, Signal
from PySide6.QtGui import QAction, QActionGroup, QKeySequence
from PySide6.QtWidgets import (
    QDockWidget,
    QFileDialog,
    QLabel,
    QMainWindow,
    QMenuBar,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from core import i18n
from core.actions import Dispatcher, Prompt
from core.commands import History
from core.document import Document, DocumentError
from core.i18n import tr
from views.command_line import CommandLine
from views.title_bar import TitleBar
from core.version import __version__
from views.viewport import Viewport


class _OpenWorker(QObject):
    """Loads and regens a drawing off the UI thread.

    Real plans take seconds (a colleague's 4.5 MB pavement sheet froze the UI
    for minutes before the hatch density cap) — the window must stay alive.
    Only plain Python/ezdxf objects cross the thread boundary.
    """

    done = Signal(object, object)   # Document, Scene
    failed = Signal(str)            # error text

    def __init__(self, path: Path) -> None:
        super().__init__()
        self._path = path

    def run(self) -> None:
        from formats.dwg_bridge import DwgBridgeError, load_dwg
        from render.backend import build_scene

        try:
            if self._path.suffix.lower() == ".dwg":
                # Transparent conversion: the user never sees the temp DXF.
                document = load_dwg(self._path)
            else:
                document = Document.load(self._path)
            scene = build_scene(document)
        except (DocumentError, DwgBridgeError) as exc:
            self.failed.emit(str(exc))
        except Exception as exc:  # a malformed file must never crash the app
            self.failed.emit(f"{type(exc).__name__}: {exc}")
        else:
            self.done.emit(document, scene)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.document: Document | None = None
        self._layers_dock = None
        self._layers_panel = None
        self._open_thread: QThread | None = None
        self._open_worker: _OpenWorker | None = None
        self._opening_name = ""
        self.setWindowTitle(f"IngeCAD — {tr('Untitled')}")
        self.resize(1280, 800)
        # Own the title bar: the system one follows the desktop's light theme
        # on GNOME/Wayland and cannot be forced dark (see views/title_bar.py).
        self.setWindowFlag(Qt.FramelessWindowHint, True)

        self.viewport = Viewport(self)
        self.setCentralWidget(self.viewport)

        self._menu_bar = QMenuBar(self)
        header = QWidget(self)
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(0)
        header_layout.addWidget(TitleBar(self))
        header_layout.addWidget(self._menu_bar)
        self.setMenuWidget(header)

        self._build_menus()
        self._build_status_bar()
        self._build_command_line()
        self._build_sidebar()
        self._build_toolbars()
        self.viewport.cursorMoved.connect(self._on_cursor_moved)

        # Frameless windows have no system resize borders; an app-wide filter
        # turns presses on the outer margin into native resizes, wherever the
        # child widget under the cursor is. The status bar's size grip still
        # works as usual.
        from PySide6.QtWidgets import QApplication

        QApplication.instance().installEventFilter(self)

    RESIZE_MARGIN = 8

    def _edges_at(self, x: int, y: int):
        edges = Qt.Edges()
        m = self.RESIZE_MARGIN
        if x <= m:
            edges |= Qt.LeftEdge
        elif x >= self.width() - m:
            edges |= Qt.RightEdge
        if y <= m:
            edges |= Qt.TopEdge
        elif y >= self.height() - m:
            edges |= Qt.BottomEdge
        return edges

    def keyPressEvent(self, event) -> None:
        # Global Esc fallback: whatever widget holds focus, Esc must cancel
        # the active tool / clear the selection (AutoCAD reflex).
        if event.key() == Qt.Key_Escape:
            self._on_prompt_cancelled()
            return
        super().keyPressEvent(event)

    def eventFilter(self, obj, event) -> bool:
        # AutoCAD feel: typing over the canvas lands in the command line.
        if (
            event.type() == QEvent.KeyPress
            and obj is getattr(self, "viewport", None)
            and not event.modifiers() & (Qt.ControlModifier | Qt.AltModifier)
            and (event.text().strip() or event.key() in (
                Qt.Key_Return, Qt.Key_Enter, Qt.Key_Escape, Qt.Key_Backspace))
        ):
            self.command_line.type_ahead(event)
            return True
        if (
            event.type() == QEvent.MouseButtonPress
            and event.button() == Qt.LeftButton
            and isinstance(obj, QWidget)
            and obj.window() is self
            and not self.isMaximized()
        ):
            pos = self.mapFromGlobal(event.globalPosition().toPoint())
            edges = self._edges_at(pos.x(), pos.y())
            handle = self.windowHandle()
            if edges and handle is not None and handle.startSystemResize(edges):
                return True
        return super().eventFilter(obj, event)

    # -- chrome ---------------------------------------------------------------
    def _build_menus(self) -> None:
        menu_bar = self._menu_bar
        menu_bar.clear()

        def item(menu, label, slot, shortcut=None):
            act = QAction(label, self)
            if shortcut is not None:
                act.setShortcut(shortcut)
            act.triggered.connect(slot)
            menu.addAction(act)
            return act

        def cmd_item(menu, label, name, icon=True):
            from views.icons import command_icon
            act = QAction(label, self)
            if icon:
                act.setIcon(command_icon(name))
            act.triggered.connect(lambda _=False, n=name: self._invoke_command(n))
            menu.addAction(act)
            return act

        # -- File -------------------------------------------------------------
        file_menu = menu_bar.addMenu(tr("File"))
        item(file_menu, tr("New"), self.new_document, QKeySequence.New)
        item(file_menu, tr("Open..."), self._open_dialog, QKeySequence.Open)
        item(file_menu, tr("Save As..."), self._save_as_dialog, QKeySequence.SaveAs)
        file_menu.addSeparator()
        item(file_menu, tr("Quit"), self.close, QKeySequence.Quit)

        # -- Edit -------------------------------------------------------------
        edit_menu = menu_bar.addMenu(tr("Edit"))
        item(edit_menu, tr("Undo"), self._cmd_undo, QKeySequence.Undo)
        item(edit_menu, tr("Redo"), self._cmd_redo, QKeySequence.Redo)
        edit_menu.addSeparator()
        cmd_item(edit_menu, tr("Erase"), "ERASE")
        cmd_item(edit_menu, tr("Move"), "MOVE")
        cmd_item(edit_menu, tr("Copy"), "COPY")

        # -- View -------------------------------------------------------------
        view_menu = menu_bar.addMenu(tr("View"))
        item(view_menu, tr("Zoom Extents"), self.viewport.zoom_extents)
        item(view_menu, tr("Zoom Window"),
             lambda: self._invoke_command("ZOOM"))
        item(view_menu, tr("Regenerate"), self.regen_in_memory)
        view_menu.addSeparator()
        item(view_menu, tr("Layers panel"), self.toggle_layers_panel)

        # -- Insert -----------------------------------------------------------
        insert_menu = menu_bar.addMenu(tr("Insert"))
        cmd_item(insert_menu, tr("Block..."), "INSERT")

        # -- Format -----------------------------------------------------------
        format_menu = menu_bar.addMenu(tr("Format"))
        item(format_menu, tr("Layers..."), self.toggle_layers_panel)
        cmd_item(format_menu, tr("Linetype..."), "LINETYPE", icon=False)

        # -- Draw -------------------------------------------------------------
        draw_menu = menu_bar.addMenu(tr("Draw"))
        for label, name in ((tr("Line"), "LINE"), (tr("Polyline"), "PLINE"),
                            (tr("Circle"), "CIRCLE"), (tr("Arc"), "ARC"),
                            (tr("Rectangle"), "RECTANG"), (tr("Polygon"), "POLYGON")):
            cmd_item(draw_menu, label, name)
        draw_menu.addSeparator()
        cmd_item(draw_menu, tr("Hatch"), "HATCH")

        # -- Dimension --------------------------------------------------------
        dim_menu = menu_bar.addMenu(tr("Dimension"))
        cmd_item(dim_menu, tr("Area"), "AREA", icon=False)

        # -- Modify -----------------------------------------------------------
        modify_menu = menu_bar.addMenu(tr("Modify"))
        for label, name in ((tr("Move"), "MOVE"), (tr("Copy"), "COPY"),
                            (tr("Rotate"), "ROTATE"), (tr("Scale"), "SCALE"),
                            (tr("Mirror"), "MIRROR"), (tr("Offset"), "OFFSET")):
            cmd_item(modify_menu, label, name)
        modify_menu.addSeparator()
        for label, name in ((tr("Trim"), "TRIM"), (tr("Extend"), "EXTEND"),
                            (tr("Fillet"), "FILLET"), (tr("Erase"), "ERASE")):
            cmd_item(modify_menu, label, name)

        # -- Tools ------------------------------------------------------------
        tools_menu = menu_bar.addMenu(tr("Tools"))
        lang_menu = tools_menu.addMenu(tr("Language"))
        lang_group = QActionGroup(self)
        # Each language is listed in its own name — recognizable no matter
        # which language is currently active.
        for code, native_name in (("en", "English"), ("es", "Español")):
            act = QAction(native_name, self)
            act.setCheckable(True)
            act.setChecked(i18n.current_language() == code)
            act.triggered.connect(lambda _=False, c=code: self._set_language(c))
            lang_group.addAction(act)
            lang_menu.addAction(act)

        # -- Window / Help ----------------------------------------------------
        window_menu = menu_bar.addMenu(tr("Window"))
        item(window_menu, tr("Layers panel"), self.toggle_layers_panel)
        item(window_menu, tr("Command line"),
             lambda: self.command_line.input.setFocus())

        help_menu = menu_bar.addMenu(tr("Help"))
        item(help_menu, tr("About IngeCAD"), self._show_about)

    def _show_about(self) -> None:
        from PySide6.QtWidgets import QMessageBox
        from core.version import __version__

        QMessageBox.about(
            self, tr("About IngeCAD"),
            f"IngeCAD {__version__}\n"
            + tr("Free 2D CAD for Linux in the spirit of classic AutoCAD.")
            + "\nGPL-3.0-or-later · Marco Sumari Tellez")

    def _set_language(self, code: str) -> None:
        """Switch the UI language, persist it, and retranslate live."""
        if code == i18n.current_language():
            return
        QSettings().setValue("language", code)
        i18n.set_language(code)
        self._retranslate()

    def _retranslate(self) -> None:
        name = self.document.name if self.document else tr("Untitled")
        self.setWindowTitle(f"IngeCAD — {name}")
        self._build_menus()

    # -- command line -----------------------------------------------------------
    def _build_command_line(self) -> None:
        self.command_line = CommandLine(self)
        dock = QDockWidget(tr("Command"), self)
        dock.setObjectName("command_dock")
        dock.setWidget(self.command_line)
        dock.setFeatures(QDockWidget.DockWidgetFloatable | QDockWidget.DockWidgetMovable)
        dock.setTitleBarWidget(QWidget(dock))  # slim: no dock title bar
        self.addDockWidget(Qt.BottomDockWidgetArea, dock)

        self.history = History()
        self.dispatcher = Dispatcher(echo=self.command_line.echo)

        from views.tool_controller import ToolController

        self.tools = ToolController(self)
        self.viewport.tool_delegate = self.tools
        self.tools.changed.connect(self.viewport.update)

        self._register_commands()
        self.command_line.set_completions(self.dispatcher.known_names())
        self.command_line.submitted.connect(self._on_command_submitted)
        self.command_line.cancelled.connect(self._on_prompt_cancelled)
        self.command_line.echo(tr("IngeCAD — type a command (L, C, Z, ...)"))
        self._build_mode_toggles()

    def _on_command_submitted(self, text: str) -> None:
        self.command_line.echo_input(text)
        if self.tools.on_text(text):
            return
        self.dispatcher.submit(text)

    def _on_prompt_cancelled(self) -> None:
        # tools.cancel() handles both cases: active tool, or idle selection
        self.tools.cancel()
        self.dispatcher.cancel()

    # -- drafting mode toggles (F3/F8/F10, classic status bar) ------------------
    def _build_mode_toggles(self) -> None:
        from PySide6.QtGui import QShortcut

        self._mode_labels: dict[str, QLabel] = {}
        for key, name, label in (("osnap", "F3", "REFENT"),
                                 ("ortho", "F8", "ORTO"),
                                 ("polar", "F10", "POLAR")):
            widget = QLabel(label)
            widget.setToolTip(name)
            self._mode_labels[key] = widget
            self.statusBar().addPermanentWidget(widget)
            QShortcut(QKeySequence(name), self,
                      lambda k=key: self._toggle_mode(k))
        self._update_mode_labels()

    def _toggle_mode(self, which: str) -> None:
        value = self.tools.toggle(which)
        self._update_mode_labels()
        names = {"osnap": tr("Object snap"), "ortho": tr("Ortho"),
                 "polar": tr("Polar")}
        state = tr("on") if value else tr("off")
        self.statusBar().showMessage(f"{names[which]}: {state}", 2000)

    def _update_mode_labels(self) -> None:
        for key, widget in self._mode_labels.items():
            active = getattr(self.tools, f"{key}_on")
            widget.setStyleSheet(
                "color: #e8e8e8; font-weight: bold;" if active
                else "color: #6a6a6a;")

    # -- document plumbing for the tools ---------------------------------------
    def new_document(self) -> None:
        self.document = Document.new()
        self.viewport.set_scene(None)
        self.tools.attach_document(self.document)
        if self._layers_panel is not None:
            self._layers_panel.refresh()
        self.setWindowTitle(f"IngeCAD — {tr('Untitled')}")

    # -- classic toolbars (Draw left, Modify top) ------------------------------
    def _build_toolbars(self) -> None:
        from PySide6.QtWidgets import QToolBar

        from views.icons import command_icon

        draw = [("LINE", tr("Line")), ("PLINE", tr("Polyline")),
                ("CIRCLE", tr("Circle")), ("ARC", tr("Arc")),
                ("RECTANG", tr("Rectangle")), ("POLYGON", tr("Polygon"))]
        modify = [("ERASE", tr("Erase")), ("MOVE", tr("Move")),
                  ("COPY", tr("Copy")), ("ROTATE", tr("Rotate")),
                  ("SCALE", tr("Scale")), ("MIRROR", tr("Mirror")),
                  ("OFFSET", tr("Offset")), ("TRIM", tr("Trim")),
                  ("EXTEND", tr("Extend")), ("FILLET", tr("Fillet"))]

        self._draw_toolbar = QToolBar(tr("Draw"), self)
        self._draw_toolbar.setObjectName("draw_toolbar")
        self._draw_toolbar.setOrientation(Qt.Vertical)
        self._draw_toolbar.setMovable(True)
        for name, label in draw:
            act = QAction(command_icon(name), label, self)
            act.setToolTip(f"{label} ({name})")
            act.triggered.connect(lambda _=False, n=name: self._invoke_command(n))
            self._draw_toolbar.addAction(act)
        self.addToolBar(Qt.LeftToolBarArea, self._draw_toolbar)

        self._modify_toolbar = QToolBar(tr("Modify"), self)
        self._modify_toolbar.setObjectName("modify_toolbar")
        self._modify_toolbar.setMovable(True)
        for name, label in modify:
            act = QAction(command_icon(name), label, self)
            act.setToolTip(f"{label} ({name})")
            act.triggered.connect(lambda _=False, n=name: self._invoke_command(n))
            self._modify_toolbar.addAction(act)
        self.addToolBar(Qt.TopToolBarArea, self._modify_toolbar)
        self._build_props_toolbar()

    def _build_props_toolbar(self) -> None:
        """BricsCAD-style quick Layer + Properties bar on top."""
        from PySide6.QtWidgets import QComboBox, QLabel, QToolBar

        bar = QToolBar(tr("Properties"), self)
        bar.setObjectName("props_toolbar")
        self.insertToolBarBreak(self._modify_toolbar)  # new row under Modify...
        self.addToolBarBreak(Qt.TopToolBarArea)

        # Compact popups: a drawing can carry hundreds of layers, so cap the
        # visible rows (scroll for the rest) and tighten the row height.
        combo_style = "QComboBox { font-size: 11px; } " \
                      "QComboBox QAbstractItemView::item { min-height: 18px; }"

        self._layer_combo = QComboBox(self)
        self._layer_combo.setMinimumWidth(130)
        self._layer_combo.setMaximumWidth(200)
        self._layer_combo.setMaxVisibleItems(18)
        self._layer_combo.setStyleSheet(combo_style)
        self._layer_combo.setToolTip(tr("Current layer"))
        self._layer_combo.activated.connect(self._on_layer_combo)
        bar.addWidget(self._layer_combo)

        self._color_combo = QComboBox(self)
        self._color_combo.setFixedWidth(96)
        self._color_combo.setMaxVisibleItems(12)
        self._color_combo.setStyleSheet(combo_style)
        self._color_combo.setToolTip(tr("Color"))
        self._color_combo.activated.connect(self._on_prop_color)
        bar.addSeparator()
        bar.addWidget(self._color_combo)
        self.addToolBar(Qt.TopToolBarArea, bar)
        self._props_toolbar = bar
        if self._layers_panel is not None:
            self._layers_panel.changed.connect(self._refresh_props_toolbar)
        self.tools.changed.connect(self._refresh_props_toolbar)
        self._refresh_props_toolbar()

    def _refresh_props_toolbar(self) -> None:
        from core import layers as layer_ops
        from views.layers_panel import fill_color_combo, swatch_icon

        self._props_loading = True
        self._layer_combo.clear()
        self._color_combo.clear()
        if self.document is not None:
            for info in layer_ops.layer_list(self.document):
                # small colour chip beside each layer name (BricsCAD look)
                self._layer_combo.addItem(swatch_icon(info.color), info.name)
            current = layer_ops.current_layer_name(self.document)
            idx = self._layer_combo.findText(current)
            if idx >= 0:
                self._layer_combo.setCurrentIndex(idx)
        fill_color_combo(self._color_combo)
        self._props_loading = False

    def _on_layer_combo(self, index: int) -> None:
        if getattr(self, "_props_loading", False) or self.document is None:
            return
        from core import layers as layer_ops

        name = self._layer_combo.itemText(index)
        selection = self.tools._selection_entities() if self.tools else []
        if selection:
            from core import actions
            self.history.execute(actions.SetPropertyCommand(selection, "layer", name))
            self.regen_in_memory()
        else:
            layer_ops.set_current_layer(self.document, name)
        self._sync_panels()

    def _on_prop_color(self, index: int) -> None:
        if getattr(self, "_props_loading", False) or self.document is None:
            return
        selection = self.tools._selection_entities() if self.tools else []
        if not selection:
            return
        from core import actions
        self.history.execute(actions.SetPropertyCommand(
            selection, "color", self._color_combo.itemData(index)))
        self.regen_in_memory()
        self._sync_panels()

    def _sync_panels(self) -> None:
        if self._layers_panel is not None:
            self._layers_panel.refresh()
        if getattr(self, "_properties_panel", None) is not None:
            self._properties_panel.refresh()
        self._refresh_props_toolbar()

    def _invoke_command(self, name: str) -> None:
        """A toolbar button runs a command like typing it: any running tool
        is cancelled first (AutoCAD interrupts the current command)."""
        if self.tools.active():
            self.tools.cancel()
        self.command_line.echo(f"{tr('Command')}: {name}")
        self.dispatcher.submit(name)
        self.viewport.setFocus()

    def _build_sidebar(self) -> None:
        """Persistent right sidebar: Layers | Properties tabs (bottom tabs)."""
        from PySide6.QtWidgets import QTabWidget

        from views.layers_panel import LayersPanel
        from views.properties_panel import PropertiesPanel

        self._layers_panel = LayersPanel(self)
        self._layers_panel.changed.connect(self.viewport.update)
        self._properties_panel = PropertiesPanel(self)
        self.tools.changed.connect(self._properties_panel.refresh)

        from PySide6.QtWidgets import QHBoxLayout, QToolButton

        tabs = QTabWidget(self)
        tabs.setObjectName("sidebar_tabs")
        tabs.setTabPosition(QTabWidget.South)   # tabs at the bottom (IngeTrazo)
        tabs.addTab(self._layers_panel, tr("Layers"))
        tabs.addTab(self._properties_panel, tr("Properties"))
        collapse_btn = QToolButton(tabs)
        collapse_btn.setText("›")
        collapse_btn.setToolTip(tr("Collapse"))
        collapse_btn.clicked.connect(self._collapse_sidebar)
        tabs.setCornerWidget(collapse_btn, Qt.TopRightCorner)
        self._sidebar_tabs = tabs
        self._sidebar_collapsed = False

        # Thin expand strip shown when collapsed.
        self._sidebar_strip = QToolButton(self)
        self._sidebar_strip.setText("‹")
        self._sidebar_strip.setToolTip(tr("Expand"))
        self._sidebar_strip.clicked.connect(self._expand_sidebar)
        self._sidebar_strip.setVisible(False)
        self._sidebar_strip.setFixedWidth(20)

        container = QWidget(self)
        clay = QHBoxLayout(container)
        clay.setContentsMargins(0, 0, 0, 0)
        clay.setSpacing(0)
        clay.addWidget(self._sidebar_strip)
        clay.addWidget(tabs)

        dock = QDockWidget(self)
        dock.setObjectName("sidebar_dock")
        dock.setTitleBarWidget(QWidget(dock))   # no dock chrome
        dock.setFeatures(QDockWidget.NoDockWidgetFeatures)  # fixed, always there
        dock.setWidget(container)
        dock.setMinimumWidth(250)
        self.addDockWidget(Qt.RightDockWidgetArea, dock)
        self.resizeDocks([dock], [280], Qt.Horizontal)
        self._layers_dock = dock

    def _collapse_sidebar(self) -> None:
        self._sidebar_collapsed = True
        self._sidebar_tabs.setVisible(False)
        self._sidebar_strip.setVisible(True)
        self._layers_dock.setMinimumWidth(20)
        self._layers_dock.setFixedWidth(20)

    def _expand_sidebar(self) -> None:
        self._sidebar_collapsed = False
        self._sidebar_strip.setVisible(False)
        self._sidebar_tabs.setVisible(True)
        self._layers_dock.setFixedWidth(280)
        self._layers_dock.setMinimumWidth(250)
        self._layers_dock.setMaximumWidth(16777215)

    def toggle_layers_panel(self) -> None:
        # LA / Format>Layers focuses the Layers tab and refreshes it.
        if self._layers_panel is None:
            return
        if self._sidebar_collapsed:
            self._expand_sidebar()
        self._sidebar_tabs.setCurrentWidget(self._layers_panel)
        self._layers_panel.refresh()

    def regen_in_memory(self) -> None:
        """Full regen of the in-memory document (edits included)."""
        if self.document is None:
            return
        from render.backend import build_scene

        scene = build_scene(self.document)
        self.viewport.set_scene(scene)
        self.tools.mark_scene_merged()

    def _register_commands(self) -> None:
        d = self.dispatcher
        d.register("ZOOM", self._cmd_zoom)
        d.register("PAN", lambda *a: self.command_line.echo(
            tr("PAN: drag with the middle mouse button")))
        d.register("REGEN", self._cmd_regen)
        d.register("U", self._cmd_undo)
        d.register("UNDO", self._cmd_undo)
        d.register("REDO", self._cmd_redo)
        d.register("OPEN", lambda *a: self._open_dialog())
        d.register("SAVEAS", lambda *a: self._save_as_dialog())
        d.register("QUIT", lambda *a: self.close())
        d.register("EXIT", lambda *a: self.close())
        d.register("LAYER", lambda *a: self.toggle_layers_panel())
        # Phase 4 drawing + Phase 5 editing tools.
        for name in ("LINE", "CIRCLE", "ARC", "PLINE", "RECTANG", "POLYGON",
                     "ERASE", "MOVE", "COPY", "ROTATE", "SCALE", "MIRROR",
                     "OFFSET", "TRIM", "EXTEND", "FILLET"):
            d.register(name, lambda *a, n=name: self.tools.start_tool(n))
        # In-scope commands that land in later phases: answer honestly.
        for name, phase in (
            ("DIST", 4), ("EXPLODE", 6),
            ("BLOCK", 6), ("INSERT", 6), ("HATCH", 6), ("LINETYPE", 6),
            ("AREA", 7), ("LIST", 7),
        ):
            d.register_future(name, phase)

    # ZOOM [Extents/Window/Previous]
    def _cmd_zoom(self, *args) -> Prompt | None:
        if args:
            return self._zoom_option(args[0])
        return Prompt(tr("ZOOM [Extents/Window/Previous] <Extents>:"),
                      self._zoom_option)

    def _zoom_option(self, option: str) -> None:
        opt = option.strip().upper() or "E"
        if opt in ("E", "EXTENTS"):
            self.viewport.zoom_extents()
        elif opt in ("W", "WINDOW"):
            self.viewport.start_zoom_window()
            self.command_line.echo(tr("Drag a window in the viewport"))
        elif opt in ("P", "PREVIOUS"):
            if not self.viewport.zoom_previous():
                self.command_line.echo(tr("No previous view"))
        else:
            self.command_line.echo(tr('Unknown ZOOM option "{name}".', name=opt))

    def _cmd_regen(self, *args) -> None:
        if self.document is None:
            self.command_line.echo(tr("Nothing to regenerate"))
            return
        self.regen_in_memory()
        self.command_line.echo(tr("Regenerated."))

    def _cmd_undo(self, *args) -> None:
        command = self.history.undo()
        self.command_line.echo(
            tr("Undo: {name}", name=command.name) if command else tr("Nothing to undo"))
        if command is not None:
            self.tools.after_history_change()

    def _cmd_redo(self, *args) -> None:
        command = self.history.redo()
        self.command_line.echo(
            tr("Redo: {name}", name=command.name) if command else tr("Nothing to redo"))
        if command is not None:
            self.tools.after_history_change()

    def _build_status_bar(self) -> None:
        # Coordinate readout, bottom-left — the classic AutoCAD tracker.
        self._coords_label = QLabel("0.0000, 0.0000")
        self._coords_label.setMinimumWidth(220)
        self.statusBar().addWidget(self._coords_label)
        self.statusBar().addPermanentWidget(QLabel(f"IngeCAD {__version__}"))

    def _on_cursor_moved(self, wx: float, wy: float) -> None:
        self._coords_label.setText(f"{wx:.4f}, {wy:.4f}")

    # -- documents -------------------------------------------------------------
    def _open_dialog(self) -> None:
        filename, _filter = QFileDialog.getOpenFileName(
            self,
            tr("Open Drawing"),
            "",
            tr("Drawings (*.dwg *.dxf);;All files (*)"),
        )
        if filename:
            self.open_path(Path(filename))

    def _save_as_dialog(self) -> None:
        if self.document is None:
            self.statusBar().showMessage(tr("Nothing to save yet"), 4000)
            return
        filename, selected = QFileDialog.getSaveFileName(
            self,
            tr("Save Drawing As"),
            self.document.name,
            tr("DWG r2000 (*.dwg);;DXF (*.dxf)"),
        )
        if not filename:
            return
        path = Path(filename)
        if path.suffix.lower() not in (".dwg", ".dxf"):
            path = path.with_suffix(".dwg" if "dwg" in selected.lower() else ".dxf")
        try:
            self.document.save_as(path)
        except Exception as exc:
            QMessageBox.warning(
                self,
                tr("Save Drawing As"),
                tr("Cannot save {name}: {error}", name=path.name, error=str(exc)),
            )
            return
        self.setWindowTitle(f"IngeCAD — {self.document.name}")
        self.statusBar().showMessage(tr("Saved {name}", name=path.name), 5000)

    def open_path(self, path: Path) -> None:
        """OS file associations, argv[1], and File > Open land here."""
        if path.suffix.lower() == ".dwg":
            from formats.dwg_bridge import have_dwg_support

            if not have_dwg_support():
                QMessageBox.warning(
                    self,
                    tr("Open Drawing"),
                    tr("DWG support needs the LibreDWG converter (dwg2dxf), "
                       "which was not found."),
                )
                return
        if self._open_thread is not None:
            self.statusBar().showMessage(tr("Still opening the previous drawing..."), 4000)
            return
        self._opening_name = path.name
        self.statusBar().showMessage(tr("Opening {name}...", name=path.name))
        thread = QThread(self)
        worker = _OpenWorker(path)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.done.connect(self._on_open_done)
        worker.failed.connect(self._on_open_failed)
        worker.done.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(self._on_open_thread_finished)
        self._open_thread = thread
        self._open_worker = worker  # keep alive while the thread runs
        thread.start()

    def _on_open_done(self, document: Document, scene) -> None:
        self.document = document
        self.viewport.set_scene(scene)
        self.viewport.zoom_extents()
        self.tools.attach_document(document, flatten=scene.flatten)
        if self._layers_panel is not None:
            self._layers_panel.refresh()   # show the opened drawing's layers
        if getattr(self, "_props_toolbar", None) is not None:
            self._refresh_props_toolbar()
        self.setWindowTitle(f"IngeCAD — {document.name}")
        if scene.layout_name:
            self.statusBar().showMessage(
                tr("Opened {name} — showing layout \"{layout}\" (model space is empty)",
                   name=document.name, layout=scene.layout_name),
                10000,
            )
        elif scene.skipped:
            self.statusBar().showMessage(
                tr("Opened {name} — {count} damaged entities could not be drawn",
                   name=document.name, count=len(scene.skipped)),
                10000,
            )
        else:
            self.statusBar().showMessage(tr("Opened {name}", name=document.name), 5000)

    def _on_open_failed(self, error: str) -> None:
        self.statusBar().clearMessage()
        QMessageBox.warning(
            self,
            tr("Open Drawing"),
            tr("Cannot open {name}: {error}", name=self._opening_name, error=error),
        )

    def _on_open_thread_finished(self) -> None:
        if self._open_thread is not None:
            self._open_thread.deleteLater()
        self._open_thread = None
        self._open_worker = None
