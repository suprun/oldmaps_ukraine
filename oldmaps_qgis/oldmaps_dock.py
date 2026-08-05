# -*- coding: utf-8 -*-

from qgis.core import QgsProject, QgsSettings
from qgis.PyQt.QtCore import QEvent, QSize, QTimer, Qt
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtNetwork import QNetworkAccessManager
from qgis.PyQt.QtWidgets import (
    QAbstractItemView,
    QDockWidget,
    QDoubleSpinBox,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QStyle,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .qt_compat import (
    QAction,
    Qt_AlignCenter,
    Qt_ArrowCursor,
    Qt_CopyAction,
    Qt_CustomContextMenu,
    Qt_Key_Escape,
    Qt_LeftDockWidgetArea,
    Qt_NoFocus,
    Qt_RightDockWidgetArea,
    get_enum,
)

from .dock.assets import RemoteCacheMixin
from .dock.catalog import CatalogMixin
from .dock.constants import *  # noqa: F401,F403
from .dock.constants import (
    LINK_ICON_PATH,
    NO_DUPLICATES_SETTING_KEY,
    OLDMAPS_LAYER_MIME,
    PLUGIN_DIR,
)
from .dock.coverage import CoverageMixin, OldMapsCoverageClickTool
from .dock.help import HelpMixin
from .dock.layers import LayersMixin
from .dock.tree import OldMapsLayerTreeView, TreeMixin


class OldMapsDockWidget(
    QDockWidget,
    CatalogMixin,
    RemoteCacheMixin,
    TreeMixin,
    CoverageMixin,
    LayersMixin,
    HelpMixin,
):
    def __init__(self, iface, tile_proxy=None, parent=None):
        super().__init__("OldMaps", parent or iface.mainWindow())
        self.iface = iface
        self.tile_proxy = tile_proxy
        self.settings = QgsSettings()
        self.data_path = None
        self.data_metadata = {}
        self.data_source = ""
        self.pages = []
        self.layer_count = 0
        self._counted_layer_urls = set()
        self._pending_search_text = ""
        self._search_filter_active = False
        self._saved_expansion_state = None
        self._filter_generation = 0
        self._filter_pending_state = None
        self._filter_page_index = 0
        self._updating_zoom_spin = False
        self._canvas_drop_targets = []
        self._layers_loaded = False
        self._layers_loading = False
        self._layers_load_generation = 0
        self._local_layers_candidate = None
        self._pending_pages = []
        self._pending_page_index = 0
        self._page_icon_cache = {}
        self._coverage_click_tool = None
        self._previous_map_tool = None
        self._coverage_layer = None
        self._coverage_geometry_cache = {}
        self._coverage_geometry_cache_loaded = False
        self._coverage_missing_geometry_urls = set()
        self._coverage_filter_tile_urls = None
        self._coverage_filter_point = None
        self._coverage_rubber = None
        self._coverage_all_rubbers = []
        self._coverage_all_rubber_by_tile_url = {}
        self._coverage_all_highlight_tile_urls = set()
        self._coverage_escape_filter_target = None
        self._borders_loading = False
        self._borders_loaded = False
        self._borders_path = None
        self._asset_reply = None
        self._asset_sync_generation = 0
        self._asset_sync_running = False
        self._asset_queue = []
        self._asset_current = None
        self._asset_request_kind = None
        self._asset_manifest = {"files": {}}
        self._asset_errors = []
        self._page_icon_files = None

        self.network_manager = QNetworkAccessManager(self)
        self.asset_timeout_timer = QTimer(self)
        self.asset_timeout_timer.setSingleShot(True)
        self.asset_timeout_timer.timeout.connect(self._on_remote_asset_timeout)
        self.all_borders_refresh_timer = QTimer(self)
        self.all_borders_refresh_timer.setSingleShot(True)
        self.all_borders_refresh_timer.setInterval(220)
        self.all_borders_refresh_timer.timeout.connect(
            self._refresh_all_coverage_geometries_now
        )

        self.setObjectName("OldMapsDockWidget")
        self.setAllowedAreas(Qt_LeftDockWidgetArea | Qt_RightDockWidgetArea)

        self.zoom_icon_button = QToolButton(self)
        self.zoom_icon_button.setIcon(
            self._qgis_icon_from_path(
                str(PLUGIN_DIR / "icons" / "mActionZoomTo.svg"),
                None,
                "/mActionZoomTo.svg",
            )
        )
        self.zoom_icon_button.setIconSize(QSize(18, 18))
        self.zoom_icon_button.setAutoRaise(True)
        self.zoom_icon_button.setCursor(Qt_ArrowCursor)
        self.zoom_icon_button.setFocusPolicy(Qt_NoFocus)
        self.zoom_icon_button.setStyleSheet(
            "QToolButton { border: 0; background: transparent; padding: 0; }"
            "QToolButton:hover { border: 0; background: transparent; }"
            "QToolButton:pressed { border: 0; background: transparent; }"
        )
        self.zoom_icon_button.setToolTip("Поточний рівень тайлів")
        self.zoom_icon_button.installEventFilter(self)
        zoom_widget = QWidget(self)
        zoom_layout = QHBoxLayout(zoom_widget)
        zoom_layout.setContentsMargins(0, 0, 0, 0)
        zoom_layout.setSpacing(4)
        zoom_layout.addWidget(self.zoom_icon_button)
        self.zoom_spin = QDoubleSpinBox(self)
        self.zoom_spin.setRange(0.0, 24.0)
        self.zoom_spin.setDecimals(1)
        self.zoom_spin.setSingleStep(1.0)
        self._update_zoom_spin_width()
        self.zoom_spin.setToolTip("Поточний рівень тайлів")
        self.zoom_spin.valueChanged.connect(self._on_zoom_spin_changed)
        zoom_layout.addWidget(self.zoom_spin)

        self.add_action = QAction(
            self._qgis_icon_from_path(
                ":/images/themes/default/mActionAdd.svg",
                get_enum(QStyle, "SP_FileDialogNewFolder", "StandardPixmap"),
                ":mActionAdd.svg",
            ),
            "",
            self,
        )
        self.add_action.setToolTip("Додати вибраний шар на карту")
        self.add_action.setEnabled(False)
        self.add_action.triggered.connect(self.add_selected_layer)

        self.lock_view_action = QAction(
            self._qgis_icon(
                ["/mActionLockExtent.svg", "/mActionLock.svg", "/mActionLockLayer.svg", "/mActionLockScale.svg"],
                get_enum(QStyle, "SP_DriveFDIcon", "StandardPixmap"),
            ),
            "",
            self,
        )
        if self.lock_view_action.icon().isNull():
            self.lock_view_action.setIcon(
                self._qgis_icon_from_path(
                    ":/images/themes/default/mActionLockExtent.svg",
                    get_enum(QStyle, "SP_DriveFDIcon", "StandardPixmap"),
                    ":mActionLockExtent.svg",
                )
            )
        self.lock_view_action.setCheckable(True)
        self.lock_view_action.setChecked(
            self._settings_bool(LOCK_VIEW_SETTING_KEY, False)
        )
        self.lock_view_action.toggled.connect(self._save_lock_view_setting)
        self.lock_view_action.toggled.connect(self._update_lock_view_tooltip)
        self._update_lock_view_tooltip(self.lock_view_action.isChecked())

        self.no_duplicates_action = QAction(
            QIcon(str(PLUGIN_DIR / "icons" / "noDuplicateLayers.svg")),
            "",
            self,
        )
        if self.no_duplicates_action.icon().isNull():
            self.no_duplicates_action.setIcon(
                self._qgis_icon_from_path(
                    ":mActionFilter2.svg",
                    get_enum(QStyle, "SP_DialogApplyButton", "StandardPixmap"),
                )
            )
        self.no_duplicates_action.setCheckable(True)
        self.no_duplicates_action.setChecked(
            self._settings_bool(NO_DUPLICATES_SETTING_KEY, True)
        )
        self.no_duplicates_action.toggled.connect(self._save_no_duplicates_setting)
        self.no_duplicates_action.toggled.connect(self._update_no_duplicates_tooltip)
        self._update_no_duplicates_tooltip(self.no_duplicates_action.isChecked())

        self.coverage_action = QAction(
            self._qgis_icon_from_path(
                ":/images/themes/default/cursors/mIdentify.svg",
                get_enum(QStyle, "SP_DialogApplyButton", "StandardPixmap"),
                ":mIdentify.svg",
            ),
            "",
            self,
        )
        self.coverage_action.setCheckable(True)
        self.coverage_action.setToolTip(
            "Знайти карти в точці\n"
            "Клацніть на карті, щоб відфільтрувати доступні шари в цій точці."
        )
        self.coverage_action.toggled.connect(self._toggle_coverage_mode)

        self.all_borders_action = QAction(
            self._qgis_icon(
                ["/mIconPolygonLayer.svg", "/mActionAddPolygon.svg"],
                get_enum(QStyle, "SP_FileDialogDetailedView", "StandardPixmap"),
            ),
            "",
            self,
        )
        self.all_borders_action.setCheckable(True)
        self.all_borders_action.setToolTip("Показати межі всіх карт")
        self.all_borders_action.toggled.connect(self._toggle_all_borders)

        self.zoom_to_selection_action = QAction(
            self._qgis_icon_from_path(
                ":/images/themes/default/mActionZoomFullExtent.svg",
                get_enum(QStyle, "SP_ArrowRight", "StandardPixmap"),
                ":mActionZoomFullExtent.svg",
            ),
            "",
            self,
        )
        self.zoom_to_selection_action.setToolTip("Збільшити до виділеного шару")
        self.zoom_to_selection_action.setEnabled(False)
        self.zoom_to_selection_action.triggered.connect(self.zoom_to_selected_item)

        self.help_action = QAction(
            self._qgis_icon_from_path(
                ":/images/themes/default/console/iconHelpConsole.svg",
                get_enum(QStyle, "SP_DialogHelpButton", "StandardPixmap"),
                ":iconHelpConsole.svg",
            ),
            "",
            self,
        )
        self.help_action.setToolTip("Довідка")
        self.help_action.triggered.connect(self.show_help)

        self.info_action = QAction(
            self._qgis_icon_from_path(
                ":/images/themes/default/mActionPropertiesWidget.svg",
                get_enum(QStyle, "SP_MessageBoxInformation", "StandardPixmap"),
                ":mActionPropertiesWidget.svg",
            ),
            "",
            self,
        )
        self.info_action.setToolTip("Атрибуція вибраного шару")
        self.info_action.setEnabled(False)
        self.info_action.triggered.connect(self.show_selected_attribution)

        self.search_action = QAction(
            self._qgis_icon_from_path(
                ":/images/themes/default/mIndicatorFilter.svg",
                get_enum(QStyle, "SP_FileDialogContentsView", "StandardPixmap"),
                ":mIndicatorFilter.svg",
            ),
            "",
            self,
        )
        self.search_action.setCheckable(True)
        self.search_action.setToolTip("Пошук")
        self.search_action.toggled.connect(self._toggle_search)

        self.expand_tree_icon = self._qgis_icon_from_path(
            ":/images/themes/default/mActionExpandTree.svg",
            get_enum(QStyle, "SP_ArrowDown", "StandardPixmap"),
            ":mActionExpandTree.svg",
        )
        self.collapse_tree_icon = self._qgis_icon_from_path(
            ":/images/themes/default/mActionCollapseTree.svg",
            get_enum(QStyle, "SP_ArrowUp", "StandardPixmap"),
            ":mActionCollapseTree.svg",
        )
        self.tree_expansion_action = QAction(self.expand_tree_icon, "", self)
        self.tree_expansion_action.setToolTip("Розгорнути все")
        self.tree_expansion_action.setEnabled(False)
        self.tree_expansion_action.triggered.connect(self.toggle_tree_expansion)

        link_icon = QIcon(str(LINK_ICON_PATH)) if LINK_ICON_PATH.exists() else QIcon()
        if link_icon.isNull():
            link_icon = self._qgis_icon(
                ["/mActionOpenURL.svg", "/mActionFileOpen.svg", "/mActionOpenTable.svg"],
                get_enum(QStyle, "SP_DirLinkIcon", "StandardPixmap"),
            )
        self.link_action = QAction(link_icon, "", self)
        self.link_action.setToolTip("Переглянути на сайті")
        self.link_action.triggered.connect(self.open_selected_page)

        toolbar = QToolBar(self)
        toolbar.setObjectName("OldMapsDockToolbar")
        toolbar.setIconSize(QSize(18, 18))
        toolbar.addWidget(zoom_widget)
        spacer = QWidget(self)
        spacer.setSizePolicy(
            get_enum(QSizePolicy, "Expanding", "Policy"),
            get_enum(QSizePolicy, "Preferred", "Policy"),
        )
        toolbar.addWidget(spacer)
        toolbar.addAction(self.coverage_action)
        toolbar.addAction(self.all_borders_action)
        toolbar.addSeparator()
        toolbar.addAction(self.lock_view_action)
        toolbar.addAction(self.no_duplicates_action)
        toolbar.addSeparator()
        toolbar.addAction(self.add_action)
        toolbar.addAction(self.info_action)
        toolbar.addAction(self.zoom_to_selection_action)
        #toolbar.addAction(self.link_action)
        toolbar.addSeparator()
        toolbar.addAction(self.search_action)
        toolbar.addAction(self.tree_expansion_action)
        toolbar.addSeparator()
        toolbar.addAction(self.help_action)

        self.tree_container = QWidget(self)
        tree_container_layout = QGridLayout(self.tree_container)
        tree_container_layout.setContentsMargins(0, 0, 0, 0)
        tree_container_layout.setSpacing(0)

        self.tree = OldMapsLayerTreeView(self, self.tree_container)
        self.tree.setObjectName("OldMapsLayerTree")
        self.tree.setColumnCount(2)
        self.tree.setHeaderLabels(["Шар", "max zoom"])
        self.tree.setUniformRowHeights(True)
        self.tree.setAlternatingRowColors(True)
        self.tree.setDragEnabled(True)
        self.tree.setMouseTracking(True)
        self.tree.setContextMenuPolicy(Qt_CustomContextMenu)
        self.tree.setDragDropMode(
            get_enum(QAbstractItemView, "DragOnly", "DragDropMode")
        )
        self.tree.setDefaultDropAction(Qt_CopyAction)
        self.tree.setStyleSheet(
            "QTreeView::item:selected:!active {"
            "background: palette(highlight);"
            "color: palette(highlighted-text);"
            "}"
        )
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(
            0,
            get_enum(QHeaderView, "Stretch", "ResizeMode"),
        )
        self.tree.header().setSectionResizeMode(
            1,
            get_enum(QHeaderView, "ResizeToContents", "ResizeMode"),
        )
        self.tree.currentItemChanged.connect(self._on_current_item_changed)
        self.tree.itemCollapsed.connect(self._on_tree_item_expansion_changed)
        self.tree.itemExpanded.connect(self._on_tree_item_expansion_changed)
        self.tree.itemDoubleClicked.connect(self._on_item_double_clicked)
        self.tree.itemEntered.connect(self._on_tree_item_entered)
        self.tree.customContextMenuRequested.connect(self._show_tree_context_menu)
        tree_container_layout.addWidget(self.tree, 0, 0)

        self.catalog_retry_overlay = QWidget(self.tree_container)
        self.catalog_retry_overlay.setObjectName("OldMapsCatalogRetryOverlay")
        self.catalog_retry_overlay.setVisible(False)
        self.catalog_retry_overlay.setStyleSheet(
            "QWidget#OldMapsCatalogRetryOverlay {"
            "background: palette(base);"
            "border: 1px solid palette(mid);"
            "padding: 10px;"
            "}"
        )
        catalog_retry_layout = QVBoxLayout(self.catalog_retry_overlay)
        catalog_retry_layout.setContentsMargins(14, 12, 14, 12)
        catalog_retry_layout.setSpacing(8)
        self.catalog_retry_title = QLabel(
            "Не вдалося завантажити каталог OldMaps",
            self.catalog_retry_overlay,
        )
        self.catalog_retry_title.setAlignment(Qt_AlignCenter)
        catalog_retry_layout.addWidget(self.catalog_retry_title)
        self.catalog_retry_detail = QLabel("", self.catalog_retry_overlay)
        self.catalog_retry_detail.setAlignment(Qt_AlignCenter)
        self.catalog_retry_detail.setWordWrap(True)
        self.catalog_retry_detail.setVisible(False)
        catalog_retry_layout.addWidget(self.catalog_retry_detail)
        self.catalog_retry_button = QPushButton(
            "Перезавантажити",
            self.catalog_retry_overlay,
        )
        self.catalog_retry_button.clicked.connect(self.reload_layers)
        catalog_retry_layout.addWidget(self.catalog_retry_button, 0, Qt_AlignCenter)
        tree_container_layout.addWidget(
            self.catalog_retry_overlay,
            0,
            0,
            Qt_AlignCenter,
        )

        self.search_edit = QLineEdit(self)
        self.search_edit.setObjectName("OldMapsSearchEdit")
        self.search_edit.setPlaceholderText("Пошук")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.setVisible(False)
        self.search_edit.textChanged.connect(self._schedule_search_filter)

        self.search_timer = QTimer(self)
        self.search_timer.setSingleShot(True)
        self.search_timer.setInterval(180)
        self.search_timer.timeout.connect(self._apply_pending_search_filter)

        self.coverage_status_widget = QWidget(self)
        self.coverage_status_widget.setObjectName("OldMapsCoverageStatus")
        self.coverage_status_widget.setVisible(False)
        self.coverage_status_widget.setStyleSheet(
            "QWidget#OldMapsCoverageStatus {"
            "background: palette(alternate-base);"
            "border: 1px solid palette(mid);"
            "}"
        )
        coverage_status_layout = QHBoxLayout(self.coverage_status_widget)
        coverage_status_layout.setContentsMargins(6, 3, 4, 3)
        coverage_status_layout.setSpacing(6)
        self.coverage_status_label = QLabel("", self.coverage_status_widget)
        coverage_status_layout.addWidget(self.coverage_status_label)
        coverage_status_layout.addStretch()
        self.coverage_reset_button = QPushButton("Скинути", self.coverage_status_widget)
        self.coverage_reset_button.setToolTip("Скинути фільтр пошуку в точці")
        self.coverage_reset_button.clicked.connect(self._reset_coverage_filter)
        coverage_status_layout.addWidget(self.coverage_reset_button)

        root = QWidget(self)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)
        layout.addWidget(toolbar)
        layout.addWidget(self.search_edit)
        layout.addWidget(self.coverage_status_widget)
        layout.addWidget(self.tree_container)
        self.setWidget(root)

        canvas = self.iface.mapCanvas()
        self._coverage_click_tool = OldMapsCoverageClickTool(canvas)
        self._coverage_click_tool.clicked.connect(self._on_coverage_map_click)
        self._coverage_click_tool.canceled.connect(self._exit_coverage_mode)
        self._coverage_click_tool.setAction(self.coverage_action)
        self._install_canvas_drop_filter(canvas)
        canvas.extentsChanged.connect(self.update_zoom_label)
        project = QgsProject.instance()
        project.layersAdded.connect(self._refresh_layer_icons)
        project.layersRemoved.connect(self._refresh_layer_icons)
        self.update_zoom_label()

    def cleanup(self):
        self._layers_load_generation += 1
        self._asset_sync_generation += 1
        self._deactivate_coverage_mode(clear_filter=True)
        self._remove_coverage_escape_filter()
        self._cancel_tree_filter()
        self.all_borders_refresh_timer.stop()
        self._clear_coverage_highlight()
        self._clear_all_coverage_geometries()
        self._cancel_layers_request()
        self._pending_pages = []
        for target in self._canvas_drop_targets:
            try:
                target.removeEventFilter(self)
            except RuntimeError:
                pass
        self._canvas_drop_targets = []
        try:
            self.iface.mapCanvas().extentsChanged.disconnect(self.update_zoom_label)
        except TypeError:
            pass
        try:
            project = QgsProject.instance()
            project.layersAdded.disconnect(self._refresh_layer_icons)
            project.layersRemoved.disconnect(self._refresh_layer_icons)
        except TypeError:
            pass

    def hideEvent(self, event):
        if hasattr(self, "coverage_action") and self.coverage_action.isChecked():
            self.coverage_action.setChecked(False)
        elif hasattr(self, "_coverage_click_tool"):
            self._deactivate_coverage_mode(clear_filter=False)
        if hasattr(self, "all_borders_action") and self.all_borders_action.isChecked():
            self.all_borders_action.setChecked(False)
        super().hideEvent(event)

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() in (
            get_enum(QEvent, "FontChange", "Type"),
            get_enum(QEvent, "StyleChange", "Type"),
        ):
            self._update_zoom_spin_width()

    def _update_zoom_spin_width(self):
        if not hasattr(self, "zoom_spin"):
            return

        metrics = self.zoom_spin.fontMetrics()
        try:
            text_width = metrics.horizontalAdvance("99,9")
        except AttributeError:
            text_width = metrics.width("99,9")

        style = self.zoom_spin.style()
        frame_metric = getattr(
            QStyle,
            "PM_SpinBoxFrameWidth",
            get_enum(QStyle, "PM_DefaultFrameWidth", "PixelMetric"),
        )
        frame_width = style.pixelMetric(frame_metric, None, self.zoom_spin)
        button_width = style.pixelMetric(
            get_enum(QStyle, "PM_ScrollBarExtent", "PixelMetric"),
            None,
            self.zoom_spin,
        )
        width = text_width + button_width + frame_width * 2 + 14
        self.zoom_spin.setFixedWidth(
            max(width, self.zoom_spin.minimumSizeHint().width())
        )

    def _install_canvas_drop_filter(self, canvas):
        targets = [canvas]
        viewport = canvas.viewport() if hasattr(canvas, "viewport") else None
        if viewport is not None and viewport is not canvas:
            targets.append(viewport)

        for target in targets:
            try:
                target.setAcceptDrops(True)
                target.installEventFilter(self)
                self._canvas_drop_targets.append(target)
            except Exception:
                pass

    def eventFilter(self, watched, event):
        if watched is getattr(self, "zoom_icon_button", None) and event.type() in (
            get_enum(QEvent, "Enter", "Type"),
            get_enum(QEvent, "HoverEnter", "Type"),
            get_enum(QEvent, "HoverMove", "Type"),
            get_enum(QEvent, "HoverLeave", "Type"),
            get_enum(QEvent, "MouseButtonPress", "Type"),
            get_enum(QEvent, "MouseButtonRelease", "Type"),
            get_enum(QEvent, "MouseButtonDblClick", "Type"),
            get_enum(QEvent, "MouseMove", "Type"),
        ):
            event.accept()
            return True

        if (
            hasattr(self, "coverage_action")
            and self.coverage_action.isChecked()
            and event.type() == get_enum(QEvent, "KeyPress", "Type")
            and event.key() == Qt_Key_Escape
        ):
            self._exit_coverage_mode()
            event.accept()
            return True

        if watched in self._canvas_drop_targets and event.type() in (
            get_enum(QEvent, "DragEnter", "Type"),
            get_enum(QEvent, "DragMove", "Type"),
            get_enum(QEvent, "Drop", "Type"),
        ):
            mime_data = event.mimeData()
            if mime_data.hasFormat(OLDMAPS_LAYER_MIME):
                if event.type() == get_enum(QEvent, "Drop", "Type"):
                    payload = self._layer_payload_from_mime(mime_data)
                    if payload is None:
                        event.ignore()
                    else:
                        event.setDropAction(Qt_CopyAction)
                        event.accept()
                        QTimer.singleShot(
                            0,
                            lambda payload=payload: self._add_layer_record_with_busy_cursor(
                                payload["page"],
                                payload["layer"],
                            ),
                        )
                else:
                    event.acceptProposedAction()
                return True

        return super().eventFilter(watched, event)
