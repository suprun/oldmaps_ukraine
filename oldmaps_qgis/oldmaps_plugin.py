# -*- coding: utf-8 -*-
from pathlib import Path

from qgis.core import QgsApplication
from qgis.PyQt.QtCore import QTimer
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QStyle

from .qt_compat import QAction, Qt_RightDockWidgetArea, get_enum

from .dock.assets import (
    clear_remote_cache_dir,
    format_cache_size,
    remote_cache_dir,
    remote_cache_size,
)
from .oldmaps_dock import OldMapsDockWidget
from .tile_proxy import OldMapsTileProxy

PLUGIN_DIR = Path(__file__).resolve().parent
ICON_PATH = PLUGIN_DIR / "icons" / "oldmaps.svg"


class OldMapsPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.action = None
        self.update_locations_action = None
        self.clear_cache_action = None
        self.help_action = None
        self.plugin_menu = None
        self.dock = None
        self.tile_proxy = OldMapsTileProxy()
        self._cached_cache_size_text = None
        self._cache_size_calc_thread = None

    def initGui(self):
        self.tile_proxy.start()
        icon = QIcon(str(ICON_PATH))

        self.action = QAction(icon, "OldMaps", self.iface.mainWindow())
        self.action.setObjectName("oldmapsToggleAction")
        self.action.setCheckable(True)
        self.action.setToolTip("OldMaps")
        self.action.toggled.connect(self._toggle_dock)

        self.iface.addToolBarIcon(self.action)
        self.iface.addPluginToMenu("&OldMaps", self.action)

        self.update_locations_action = QAction(
            self._theme_icon(
                ("/mActionRefresh.svg", "/mActionReload.svg"),
                get_enum(QStyle, "SP_BrowserReload", "StandardPixmap")
                or get_enum(QStyle, "SP_DialogResetButton", "StandardPixmap"),
            ),
            "Оновити локації",
            self.iface.mainWindow(),
        )
        self.update_locations_action.triggered.connect(self._update_locations)
        self.iface.addPluginToMenu("&OldMaps", self.update_locations_action)

        self.clear_cache_action = QAction(
            self._theme_icon(
                ("/mActionDeleteSelected.svg", "/mActionRemove.svg"),
                get_enum(QStyle, "SP_TrashIcon", "StandardPixmap")
                or get_enum(QStyle, "SP_DialogDiscardButton", "StandardPixmap"),
            ),
            "",
            self.iface.mainWindow(),
        )
        self.clear_cache_action.triggered.connect(self._clear_cache)
        self._update_cache_action_text()
        self.iface.addPluginToMenu("&OldMaps", self.clear_cache_action)

        self.help_action = QAction(
            self._theme_icon(
                ("/mActionHelpContents.svg", "/mActionHelpAbout.svg"),
                get_enum(QStyle, "SP_DialogHelpButton", "StandardPixmap"),
            ),
            "Довідка",
            self.iface.mainWindow(),
        )
        self.help_action.triggered.connect(self._show_help)
        self.iface.addPluginToMenu("&OldMaps", self.help_action)

        self._set_plugin_menu_icon(icon)

        try:
            self.plugin_menu = self.iface.pluginMenu()
            self.plugin_menu.aboutToShow.connect(self._update_cache_action_text)
        except Exception:
            self.plugin_menu = None

    def unload(self):
        if self.plugin_menu is not None:
            try:
                self.plugin_menu.aboutToShow.disconnect(
                    self._update_cache_action_text
                )
            except (RuntimeError, TypeError):
                pass
            self.plugin_menu = None

        if self.clear_cache_action is not None:
            self.iface.removePluginMenu("&OldMaps", self.clear_cache_action)
            try:
                self.clear_cache_action.triggered.disconnect(self._clear_cache)
            except (RuntimeError, TypeError):
                pass
            self.clear_cache_action.deleteLater()
            self.clear_cache_action = None

        if self.help_action is not None:
            self.iface.removePluginMenu("&OldMaps", self.help_action)
            try:
                self.help_action.triggered.disconnect(self._show_help)
            except (RuntimeError, TypeError):
                pass
            self.help_action.deleteLater()
            self.help_action = None

        if self.update_locations_action is not None:
            self.iface.removePluginMenu("&OldMaps", self.update_locations_action)
            try:
                self.update_locations_action.triggered.disconnect(
                    self._update_locations
                )
            except (RuntimeError, TypeError):
                pass
            self.update_locations_action.deleteLater()
            self.update_locations_action = None

        if self.action is not None:
            self.iface.removeToolBarIcon(self.action)
            self.iface.removePluginMenu("&OldMaps", self.action)
            self.action.toggled.disconnect(self._toggle_dock)
            self.action.deleteLater()
            self.action = None

        if self.dock is not None:
            self.dock.cleanup()
            self.iface.removeDockWidget(self.dock)
            self.dock.deleteLater()
            self.dock = None

        self.tile_proxy.stop()

    def _toggle_dock(self, checked):
        if checked:
            self._show_dock()
        elif self.dock is not None:
            self.dock.hide()

    def _show_dock(self):
        self._ensure_dock()
        if self.dock is None:
            return

        self.dock.show()
        self.dock.raise_()
        QTimer.singleShot(0, self.dock.ensure_layers_loaded)

    def _ensure_dock(self):
        if self.dock is None:
            self.dock = OldMapsDockWidget(self.iface, self.tile_proxy)
            self.dock.visibilityChanged.connect(self._sync_action_state)
            self.iface.addDockWidget(Qt_RightDockWidgetArea, self.dock)

        return self.dock

    def _update_locations(self):
        dock = self._ensure_dock()
        if dock is None:
            return

        dock.show()
        dock.raise_()
        QTimer.singleShot(0, dock.reload_layers)

    def _show_help(self):
        dock = self._ensure_dock()
        if dock is None:
            return

        dock.show_help()

    def _clear_cache(self):
        if self.dock is not None:
            cleared = self.dock.clear_remote_cache()
        else:
            cleared = clear_remote_cache_dir()

        self._cached_cache_size_text = format_cache_size(0)
        self._update_cache_action_text(force_sync=True)
        if cleared:
            self.iface.messageBar().pushSuccess("OldMaps", "Кеш очищено.")
        else:
            self.iface.messageBar().pushWarning(
                "OldMaps",
                "Не вдалося повністю очистити кеш.",
            )

    def _update_cache_action_text(self, force_sync=False):
        if self.clear_cache_action is None:
            return

        cache_dir = remote_cache_dir()
        self.clear_cache_action.setToolTip(str(cache_dir))

        if force_sync:
            size_bytes = remote_cache_size()
            self._cached_cache_size_text = format_cache_size(size_bytes)
            self.clear_cache_action.setText(f"Очистити кеш ({self._cached_cache_size_text})")
            return

        if self._cached_cache_size_text is not None:
            self.clear_cache_action.setText(f"Очистити кеш ({self._cached_cache_size_text})")
        else:
            self.clear_cache_action.setText("Очистити кеш")

        if self._cache_size_calc_thread is None or not self._cache_size_calc_thread.is_alive():
            import threading
            self._cache_size_calc_thread = threading.Thread(
                target=self._async_calc_cache_size,
                daemon=True,
            )
            self._cache_size_calc_thread.start()

    def _async_calc_cache_size(self):
        try:
            size_bytes = remote_cache_size()
            size_text = format_cache_size(size_bytes)
            self._cached_cache_size_text = size_text
            QTimer.singleShot(0, self._on_cache_size_calculated)
        except Exception:
            pass

    def _on_cache_size_calculated(self):
        if self.clear_cache_action is not None and self._cached_cache_size_text is not None:
            self.clear_cache_action.setText(f"Очистити кеш ({self._cached_cache_size_text})")

    def _sync_action_state(self, visible):
        if self.action is None or self.action.isChecked() == visible:
            return

        self.action.blockSignals(True)
        self.action.setChecked(visible)
        self.action.blockSignals(False)

    def _set_plugin_menu_icon(self, icon):
        if icon.isNull():
            return

        try:
            plugin_menu = self.iface.pluginMenu()
        except Exception:
            return

        for action in plugin_menu.actions():
            menu = action.menu()
            if menu is None:
                continue
            if menu.title().replace("&", "") == "OldMaps":
                action.setIcon(icon)
                menu.setIcon(icon)
                return

    def _theme_icon(self, names, fallback):
        for name in names:
            icon = QgsApplication.getThemeIcon(name)
            if not icon.isNull():
                return icon

        return self.iface.mainWindow().style().standardIcon(fallback)
