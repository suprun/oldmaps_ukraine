# -*- coding: utf-8 -*-

from qgis.core import (
    Qgis,
    QgsCoordinateTransform,
    QgsFeatureRequest,
    QgsGeometry,
    QgsPointXY,
    QgsProject,
    QgsRectangle,
    QgsVectorLayer,
    QgsWkbTypes,
)
from qgis.gui import QgsMapTool, QgsRubberBand, QgsVertexMarker
from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import QApplication

from ..qt_compat import Qt_CrossCursor, Qt_Key_Escape, Qt_LeftButton, get_enum

from .constants import (
    BORDERS_LAYER_NAME,
    DATA_ROLE,
    TILE_URL_ROLE,
    WEB_MERCATOR,
)


COVERAGE_ALL_BORDERS_Z_VALUE = 10
COVERAGE_CLICK_FILTER_Z_VALUE = 20
COVERAGE_HIGHLIGHT_Z_VALUE = 30
COVERAGE_CLICK_MARKER_Z_VALUE = 100

COVERAGE_ALL_BORDERS_COLOR = QColor(255, 212, 59, 210)
COVERAGE_ALL_BORDERS_FILL_COLOR = QColor(255, 212, 59, 0)
COVERAGE_ALL_BORDERS_WIDTH = 1
COVERAGE_CLICK_FILTER_COLOR = QColor(0, 213, 255, 240)
COVERAGE_CLICK_FILTER_FILL_COLOR = QColor(0, 213, 255, 0)
COVERAGE_CLICK_FILTER_WIDTH = 2
COVERAGE_HIGHLIGHT_COLOR = QColor(255, 59, 206, 245)
COVERAGE_HIGHLIGHT_FILL_COLOR = QColor(255, 59, 206, 45)
COVERAGE_HIGHLIGHT_WIDTH = 3
COVERAGE_CLICK_MARKER_COLOR = QColor(255, 23, 68, 255)
COVERAGE_CLICK_MARKER_ICON_SIZE = 24
COVERAGE_CLICK_MARKER_PEN_WIDTH = 4


def _set_canvas_item_z_value(item, z_value):
    set_z_value = getattr(item, "setZValue", None)
    if set_z_value is None:
        return

    try:
        set_z_value(z_value)
    except Exception:
        pass


def _refresh_canvas_overlay(canvas):
    try:
        canvas.scene().update()
    except Exception:
        pass

    try:
        canvas.viewport().update()
    except Exception:
        pass


class OldMapsCoverageClickTool(QgsMapTool):
    clicked = pyqtSignal(QgsPointXY)
    canceled = pyqtSignal()

    def __init__(self, canvas):
        super().__init__(canvas)
        self._marker = None
        self.setCursor(Qt_CrossCursor)

    def canvasReleaseEvent(self, event):
        if event.button() != Qt_LeftButton:
            return

        canvas_point = self.toMapCoordinates(event.pos())
        web_mercator_point = canvas_point
        canvas_crs = self.canvas().mapSettings().destinationCrs()

        if canvas_crs.isValid() and canvas_crs != WEB_MERCATOR:
            try:
                transform = QgsCoordinateTransform(
                    canvas_crs,
                    WEB_MERCATOR,
                    QgsProject.instance(),
                )
                web_mercator_point = transform.transform(canvas_point)
            except Exception:
                web_mercator_point = canvas_point

        self._update_marker(canvas_point)
        self.clicked.emit(web_mercator_point)

    def keyPressEvent(self, event):
        if event.key() == Qt_Key_Escape:
            self.canceled.emit()
            event.accept()
            return

        super().keyPressEvent(event)

    def deactivate(self):
        self.clear_marker()
        super().deactivate()

    def clear_marker(self):
        if self._marker is not None:
            try:
                self.canvas().scene().removeItem(self._marker)
            except RuntimeError:
                pass
            self._marker = None

    def show_marker(self, point):
        self._update_marker(point)

    def raise_marker(self):
        if self._marker is None:
            return

        _set_canvas_item_z_value(self._marker, COVERAGE_CLICK_MARKER_Z_VALUE)
        try:
            self._marker.update()
        except AttributeError:
            pass
        _refresh_canvas_overlay(self.canvas())

    def _update_marker(self, point):
        self.clear_marker()
        self._marker = QgsVertexMarker(self.canvas())
        self._marker.setCenter(point)
        self._marker.setColor(COVERAGE_CLICK_MARKER_COLOR)
        self._marker.setFillColor(COVERAGE_CLICK_MARKER_COLOR)
        icon_cross = getattr(QgsVertexMarker, "ICON_CROSS", None)
        if icon_cross is None:
            icon_cross = QgsVertexMarker.IconType.ICON_CROSS
        self._marker.setIconType(icon_cross)
        self._marker.setIconSize(COVERAGE_CLICK_MARKER_ICON_SIZE)
        self._marker.setPenWidth(COVERAGE_CLICK_MARKER_PEN_WIDTH)
        self.raise_marker()


class CoverageMixin:
    def _on_tree_item_entered(self, item, column):
        payload = item.data(0, DATA_ROLE) if item is not None else {}
        if not isinstance(payload, dict) or payload.get("type") != "layer":
            self._clear_coverage_highlight()
            return

        geometry = self._coverage_geometry_for_tile_url(item.data(0, TILE_URL_ROLE))
        if geometry is None:
            self._clear_coverage_highlight()
            return

        self._show_coverage_geometry(geometry)

    def _toggle_coverage_mode(self, checked):
        if checked:
            if self._activate_coverage_mode():
                return

            self.coverage_action.blockSignals(True)
            self.coverage_action.setChecked(False)
            self.coverage_action.blockSignals(False)
            return

        self._deactivate_coverage_mode(clear_filter=False)

    def _toggle_all_borders(self, checked):
        if checked:
            if self._show_all_coverage_geometries():
                self.all_borders_action.setToolTip("Приховати межі всі шарів")
                return

            self.all_borders_action.blockSignals(True)
            self.all_borders_action.setChecked(False)
            self.all_borders_action.blockSignals(False)
            return

        self.all_borders_action.setToolTip("Показати межі всіх шарів")
        self._clear_coverage_highlight()
        self._clear_all_coverage_geometries()

    def _exit_coverage_mode(self):
        if self.coverage_action.isChecked():
            self.coverage_action.setChecked(False)
            return

        self._deactivate_coverage_mode(clear_filter=False)

    def _reset_coverage_filter(self):
        self._coverage_filter_tile_urls = None
        self._coverage_filter_point = None
        self._hide_coverage_status()
        self._clear_coverage_highlight()
        if self._coverage_click_tool is not None:
            self._coverage_click_tool.clear_marker()
        self._apply_tree_filters(reveal_current=True)
        self._refresh_all_coverage_click_highlights()
        self._raise_coverage_click_marker()

    def _activate_coverage_mode(self):
        if self._ensure_coverage_layer() is None:
            return False

        canvas = self.iface.mapCanvas()
        if canvas.mapTool() is not self._coverage_click_tool:
            self._previous_map_tool = canvas.mapTool()
            canvas.setMapTool(self._coverage_click_tool)
        self._restore_coverage_click_marker()
        self._install_coverage_escape_filter()
        self._focus_coverage_canvas()
        return True

    def _deactivate_coverage_mode(self, clear_filter=False):
        self._remove_coverage_escape_filter()
        canvas = self.iface.mapCanvas()
        if canvas.mapTool() is self._coverage_click_tool and self._previous_map_tool is not None:
            canvas.setMapTool(self._previous_map_tool)
        self._previous_map_tool = None

        if self._coverage_click_tool is not None:
            self._coverage_click_tool.clear_marker()

        self._clear_coverage_highlight()
        if clear_filter:
            self._coverage_filter_tile_urls = None
            self._coverage_filter_point = None
            self._hide_coverage_status()
            self._apply_tree_filters(reveal_current=True)

    def _restore_coverage_click_marker(self):
        if self._coverage_click_tool is None:
            return
        if (
            self._coverage_filter_tile_urls is None
            or self._coverage_filter_point is None
        ):
            self._coverage_click_tool.clear_marker()
            return

        canvas_point = QgsPointXY(self._coverage_filter_point)
        canvas_crs = self.iface.mapCanvas().mapSettings().destinationCrs()
        if canvas_crs.isValid() and canvas_crs != WEB_MERCATOR:
            try:
                transform = QgsCoordinateTransform(
                    WEB_MERCATOR,
                    canvas_crs,
                    QgsProject.instance(),
                )
                canvas_point = transform.transform(canvas_point)
            except Exception:
                return

        self._coverage_click_tool.show_marker(canvas_point)

    def _install_coverage_escape_filter(self):
        if self._coverage_escape_filter_target is not None:
            return

        target = QApplication.instance()
        if target is None:
            return

        try:
            target.installEventFilter(self)
            self._coverage_escape_filter_target = target
        except RuntimeError:
            self._coverage_escape_filter_target = None

    def _remove_coverage_escape_filter(self):
        target = self._coverage_escape_filter_target
        self._coverage_escape_filter_target = None
        if target is None:
            return

        try:
            target.removeEventFilter(self)
        except RuntimeError:
            pass

    def _focus_coverage_canvas(self):
        canvas = self.iface.mapCanvas()
        targets = [canvas]
        viewport = canvas.viewport() if hasattr(canvas, "viewport") else None
        if viewport is not None and viewport is not canvas:
            targets.append(viewport)

        for target in targets:
            try:
                reason = get_enum(Qt, "OtherFocusReason", "FocusReason")
                if reason is not None:
                    target.setFocus(reason)
                else:
                    target.setFocus()
            except (TypeError, AttributeError):
                target.setFocus()
            except RuntimeError:
                pass

    def _on_coverage_map_click(self, point):
        tile_urls = self._coverage_tile_urls_at_point(point)
        if tile_urls is None:
            return

        self._coverage_filter_tile_urls = tile_urls
        self._coverage_filter_point = QgsPointXY(point)
        self._clear_coverage_highlight()
        self._apply_tree_filters(reveal_current=True)
        self._refresh_all_coverage_click_highlights()
        self._raise_coverage_click_marker()
        count = len(tile_urls)
        if count == 0:
            self.coverage_status_label_text = "Не знайдено зображень в цій точці"
        else:
            ending = 'я' if (count % 10 in (1, 2, 3, 4)) and (count % 100 not in (11, 12, 13, 14)) else 'ь'
            self.coverage_status_label_text = f"Знайдено {count} зображенн{ending} у точці"
        self._show_coverage_status(self.coverage_status_label_text)
        self.iface.messageBar().pushMessage(
            "OldMaps",
            self.coverage_status_label_text,
            level=Qgis.Info,
            duration=4,
        )

    def _show_coverage_status(self, text):
        self.coverage_status_label.setText(text)
        self.coverage_status_widget.setVisible(True)

    def _hide_coverage_status(self):
        if hasattr(self, "coverage_status_widget"):
            self.coverage_status_widget.setVisible(False)
            self.coverage_status_label.clear()

    def _show_all_coverage_geometries(self):
        if not self._load_coverage_geometry_cache(warn=True):
            return False

        filter_tile_urls = self._all_coverage_search_tile_urls()
        click_tile_urls = self._all_coverage_click_tile_urls()
        rubber_by_tile_url = self._coverage_all_rubber_index()
        self._coverage_all_highlight_tile_urls = set(click_tile_urls or set())

        for tile_url, geometry in self._coverage_geometry_cache.items():
            visible = filter_tile_urls is None or tile_url in filter_tile_urls
            click_filter_match = (
                click_tile_urls is not None
                and tile_url in click_tile_urls
            )
            rubber = rubber_by_tile_url.get(tile_url)
            if rubber is None and visible:
                rubber = self._create_coverage_rubber(
                    geometry,
                    highlight=False,
                    click_filter_match=click_filter_match,
                )
                if rubber is None:
                    continue
                rubber_by_tile_url[tile_url] = rubber
                self._coverage_all_rubbers.append(rubber)
            if rubber is None:
                continue

            self._set_coverage_rubber_visible(rubber, visible)
            if visible:
                self._style_coverage_rubber(
                    rubber,
                    highlight=False,
                    click_filter_match=click_filter_match,
                )

        self._refresh_coverage_overlay_canvas()
        self._raise_coverage_click_marker()
        return True

    def _refresh_all_coverage_click_highlights(self):
        if (
            not hasattr(self, "all_borders_action")
            or not self.all_borders_action.isChecked()
        ):
            return
        if not self._load_coverage_geometry_cache(warn=False):
            return

        previous_tile_urls = set(
            getattr(
                self,
                "_coverage_all_highlight_tile_urls",
                set(),
            )
            or set()
        )
        current_tile_urls = self._all_coverage_click_tile_urls() or set()
        changed_tile_urls = previous_tile_urls.symmetric_difference(current_tile_urls)
        self._coverage_all_highlight_tile_urls = set(current_tile_urls)
        if not changed_tile_urls:
            return

        filter_tile_urls = self._all_coverage_search_tile_urls()
        for tile_url in changed_tile_urls:
            visible = filter_tile_urls is None or tile_url in filter_tile_urls
            self._replace_all_coverage_rubber(
                tile_url,
                visible,
                tile_url in current_tile_urls,
            )

        self._refresh_coverage_overlay_canvas()
        self._raise_coverage_click_marker()

    def _refresh_all_coverage_geometries_for_search(self, immediate=False):
        if (
            not hasattr(self, "all_borders_action")
            or not self.all_borders_action.isChecked()
        ):
            return

        timer = getattr(self, "all_borders_refresh_timer", None)
        if immediate or timer is None:
            if timer is not None:
                timer.stop()
            self._refresh_all_coverage_geometries_now()
            return

        timer.start()

    def _refresh_all_coverage_geometries_now(self):
        if (
            not hasattr(self, "all_borders_action")
            or not self.all_borders_action.isChecked()
        ):
            return

        self._show_all_coverage_geometries()

    def _all_coverage_search_tile_urls(self):
        if not hasattr(self, "search_edit") or not hasattr(self, "tree"):
            return None

        query = (self.search_edit.text() or "").casefold().strip()
        if not query:
            return None

        return self.tree.layerTileUrlsForFilter(query=query)

    def _all_coverage_click_tile_urls(self):
        if self._coverage_filter_tile_urls is None:
            return None

        return set(self._coverage_filter_tile_urls)

    def _coverage_all_rubber_index(self):
        if not hasattr(self, "_coverage_all_rubber_by_tile_url"):
            self._coverage_all_rubber_by_tile_url = {}
        return self._coverage_all_rubber_by_tile_url

    def _replace_all_coverage_rubber(self, tile_url, visible, click_filter_match):
        rubber_by_tile_url = self._coverage_all_rubber_index()
        rubber = rubber_by_tile_url.pop(tile_url, None)
        if rubber is not None:
            try:
                self._coverage_all_rubbers.remove(rubber)
            except ValueError:
                pass
            try:
                self.iface.mapCanvas().scene().removeItem(rubber)
            except RuntimeError:
                pass

        if not visible:
            return

        geometry = self._coverage_geometry_cache.get(tile_url)
        if geometry is None:
            return

        rubber = self._create_coverage_rubber(
            geometry,
            highlight=False,
            click_filter_match=click_filter_match,
        )
        if rubber is None:
            return

        rubber_by_tile_url[tile_url] = rubber
        self._coverage_all_rubbers.append(rubber)

    def _set_coverage_rubber_visible(self, rubber, visible):
        try:
            rubber.setVisible(visible)
            self._update_coverage_rubber(rubber)
            return
        except AttributeError:
            pass

        if visible:
            try:
                rubber.show()
                self._update_coverage_rubber(rubber)
            except AttributeError:
                pass
        else:
            try:
                rubber.hide()
                self._update_coverage_rubber(rubber)
            except AttributeError:
                pass

    def _update_coverage_rubber(self, rubber):
        try:
            rubber.update()
        except AttributeError:
            pass

    def _raise_coverage_click_marker(self):
        coverage_click_tool = getattr(self, "_coverage_click_tool", None)
        if coverage_click_tool is None:
            return

        coverage_click_tool.raise_marker()

    def _refresh_coverage_overlay_canvas(self):
        try:
            canvas = self.iface.mapCanvas()
        except Exception:
            return

        _refresh_canvas_overlay(canvas)

    def _clear_all_coverage_geometries(self):
        timer = getattr(self, "all_borders_refresh_timer", None)
        if timer is not None:
            timer.stop()

        if not self._coverage_all_rubbers:
            if hasattr(self, "_coverage_all_rubber_by_tile_url"):
                self._coverage_all_rubber_by_tile_url = {}
            self._coverage_all_highlight_tile_urls = set()
            return

        canvas = self.iface.mapCanvas()
        for rubber in self._coverage_all_rubbers:
            try:
                canvas.scene().removeItem(rubber)
            except RuntimeError:
                pass
        self._coverage_all_rubbers = []
        self._coverage_all_rubber_by_tile_url = {}
        self._coverage_all_highlight_tile_urls = set()

    def _ensure_coverage_layer(self, warn=True):
        if self._coverage_layer is not None and self._coverage_layer.isValid():
            return self._coverage_layer

        borders_path = self._coverage_borders_path(warn=warn)
        if borders_path is None:
            return None

        uri = f"{borders_path}|layername={BORDERS_LAYER_NAME}"
        layer = QgsVectorLayer(uri, "OldMaps map borders", "ogr")
        if not layer.isValid():
            if warn:
                self._show_warning(f"Не вдалося відкрити borders.gpkg: {borders_path}")
            return None
        if layer.fields().indexFromName("tile_url") == -1:
            if warn:
                self._show_warning("У borders.gpkg немає поля tile_url.")
            return None

        self._coverage_layer = layer
        self._coverage_geometry_cache = {}
        self._coverage_geometry_cache_loaded = False
        self._coverage_missing_geometry_urls = set()
        return self._coverage_layer

    def _coverage_tile_urls_at_point(self, point):
        layer = self._ensure_coverage_layer()
        if layer is None:
            return None

        query_point = QgsPointXY(point)
        layer_crs = layer.crs()
        if layer_crs.isValid() and layer_crs != WEB_MERCATOR:
            try:
                transform = QgsCoordinateTransform(
                    WEB_MERCATOR,
                    layer_crs,
                    QgsProject.instance(),
                )
                query_point = transform.transform(query_point)
            except Exception as exc:
                self._show_warning(f"Не вдалося перетворити координати кліку: {exc}")
                return None

        tolerance = 0.001
        request = QgsFeatureRequest().setFilterRect(
            QgsRectangle(
                query_point.x() - tolerance,
                query_point.y() - tolerance,
                query_point.x() + tolerance,
                query_point.y() + tolerance,
            )
        )
        point_geometry = QgsGeometry.fromPointXY(query_point)
        tile_urls = set()

        for feature in layer.getFeatures(request):
            geometry = feature.geometry()
            if geometry is None or geometry.isNull() or not geometry.intersects(point_geometry):
                continue

            tile_url = str(feature.attribute("tile_url") or "").strip()
            if not tile_url:
                continue

            tile_urls.add(tile_url)
            self._coverage_geometry_cache[tile_url] = self._geometry_to_web_mercator(
                geometry,
                layer_crs,
            )

        return tile_urls

    def _coverage_geometry_for_tile_url(self, tile_url):
        tile_url = str(tile_url or "").strip()
        if not tile_url:
            return None
        if tile_url in self._coverage_geometry_cache:
            return self._coverage_geometry_cache[tile_url]
        if self._coverage_geometry_cache_loaded or tile_url in self._coverage_missing_geometry_urls:
            return None

        return self._load_coverage_geometry_for_tile_url(tile_url)

    def _load_coverage_geometry_for_tile_url(self, tile_url):
        layer = self._ensure_coverage_layer(warn=False)
        if layer is None:
            return None

        escaped_tile_url = tile_url.replace("'", "''")
        request = QgsFeatureRequest().setFilterExpression(
            f"\"tile_url\" = '{escaped_tile_url}'"
        )
        layer_crs = layer.crs()

        try:
            features = layer.getFeatures(request)
        except Exception:
            features = layer.getFeatures()

        for feature in features:
            feature_tile_url = str(feature.attribute("tile_url") or "").strip()
            if feature_tile_url != tile_url:
                continue

            geometry = feature.geometry()
            if geometry is None or geometry.isNull():
                break

            self._coverage_geometry_cache[tile_url] = self._geometry_to_web_mercator(
                geometry,
                layer_crs,
            )
            return self._coverage_geometry_cache[tile_url]

        self._coverage_missing_geometry_urls.add(tile_url)
        return None

    def _load_coverage_geometry_cache(self, warn=True):
        if self._coverage_geometry_cache_loaded:
            return True

        layer = self._ensure_coverage_layer(warn=warn)
        if layer is None:
            return False

        layer_crs = layer.crs()
        for feature in layer.getFeatures():
            tile_url = str(feature.attribute("tile_url") or "").strip()
            if not tile_url:
                continue

            geometry = feature.geometry()
            if geometry is None or geometry.isNull():
                continue

            self._coverage_geometry_cache[tile_url] = self._geometry_to_web_mercator(
                geometry,
                layer_crs,
            )

        self._coverage_geometry_cache_loaded = True
        self._coverage_missing_geometry_urls = set()
        return True

    def _geometry_to_web_mercator(self, geometry, source_crs):
        result = QgsGeometry(geometry)
        if source_crs.isValid() and source_crs != WEB_MERCATOR:
            try:
                transform = QgsCoordinateTransform(
                    source_crs,
                    WEB_MERCATOR,
                    QgsProject.instance(),
                )
                result.transform(transform)
            except Exception:
                return QgsGeometry(geometry)
        return result

    def _show_coverage_geometry(self, geometry):
        self._clear_coverage_highlight()
        self._coverage_rubber = self._create_coverage_rubber(geometry, highlight=True)
        self._raise_coverage_click_marker()

    def _create_coverage_rubber(self, geometry, highlight, click_filter_match=False):
        display_geometry = QgsGeometry(geometry)
        canvas = self.iface.mapCanvas()
        canvas_crs = canvas.mapSettings().destinationCrs()

        if canvas_crs.isValid() and canvas_crs != WEB_MERCATOR:
            try:
                transform = QgsCoordinateTransform(
                    WEB_MERCATOR,
                    canvas_crs,
                    QgsProject.instance(),
                )
                display_geometry.transform(transform)
            except Exception:
                return None

        polygon_type = getattr(QgsWkbTypes, "PolygonGeometry", None)
        if polygon_type is None:
            polygon_type = QgsWkbTypes.GeometryType.PolygonGeometry
        rubber = QgsRubberBand(canvas, polygon_type)
        self._style_coverage_rubber(rubber, highlight, click_filter_match)
        rubber.setToGeometry(display_geometry)
        return rubber

    def _style_coverage_rubber(self, rubber, highlight, click_filter_match=False):
        if highlight:
            z_value = COVERAGE_HIGHLIGHT_Z_VALUE
            rubber.setColor(COVERAGE_HIGHLIGHT_COLOR)
            rubber.setFillColor(COVERAGE_HIGHLIGHT_FILL_COLOR)
            rubber.setWidth(COVERAGE_HIGHLIGHT_WIDTH)
        elif click_filter_match:
            z_value = COVERAGE_CLICK_FILTER_Z_VALUE
            rubber.setColor(COVERAGE_CLICK_FILTER_COLOR)
            rubber.setFillColor(COVERAGE_CLICK_FILTER_FILL_COLOR)
            rubber.setWidth(COVERAGE_CLICK_FILTER_WIDTH)
        else:
            z_value = COVERAGE_ALL_BORDERS_Z_VALUE
            rubber.setColor(COVERAGE_ALL_BORDERS_COLOR)
            rubber.setFillColor(COVERAGE_ALL_BORDERS_FILL_COLOR)
            rubber.setWidth(COVERAGE_ALL_BORDERS_WIDTH)
        _set_canvas_item_z_value(rubber, z_value)
        self._update_coverage_rubber(rubber)

    def _clear_coverage_highlight(self):
        if self._coverage_rubber is None:
            return

        try:
            self.iface.mapCanvas().scene().removeItem(self._coverage_rubber)
        except RuntimeError:
            pass
        self._coverage_rubber = None
