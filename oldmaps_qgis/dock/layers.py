# -*- coding: utf-8 -*-
import html
import json
import math
import re

from qgis.core import (
    Qgis,
    QgsApplication,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsLayerTreeModel,
    QgsPointXY,
    QgsProject,
    QgsRasterLayer,
    QgsRectangle,
)
from qgis.PyQt.QtCore import QSize, QTimer, QUrl, Qt
from qgis.PyQt.QtGui import QDesktopServices, QIcon
from qgis.PyQt.QtWidgets import QApplication, QStyle

from ..qt_compat import Qt_WaitCursor, get_enum

from .constants import (
    ADD_LAYER_MIN_VIEW_ZOOM,
    ADDED_LAYER_MIN_VISIBLE_ZOOM,
    DEFAULT_PAGE_ICON_PATH,
    FLAGS_AND_COATS_DIR,
    HIDE_ADDED_LAYERS_BELOW_MIN_ZOOM,
    LAYER_EXTENT_MIN_HEIGHT,
    LAYER_EXTENT_MIN_WIDTH,
    LAYER_EXTENT_ZOOM_PADDING,
    LOAD_SUPABASE_OVERVIEW_TILES,
    MAX_ZOOM_RE,
    MIN_TILE_ZOOM,
    OLDMAPS_HOME,
    OLDMAPS_LAYER_MIME,
    OLDMAPS_REFERER,
    STATIC_VIEW_RE,
    WEB_MERCATOR,
    WEB_MERCATOR_INITIAL_RESOLUTION,
    WGS84,
)


class LayersMixin:
    def add_selected_layer(self):
        selected = self._selected_layer_record()
        if selected is None:
            return

        self.add_layer_record(selected["page"], selected["layer"])

    def add_layer_record(self, page, layer_data):
        tile_url = layer_data.get("tile_url")
        if not tile_url:
            self._show_warning("У вибраному елементі немає tile_url.")
            return

        if self.no_duplicates_action.isChecked():
            existing_layer = self._find_project_layer_by_tile_url(tile_url)
            if existing_layer is not None:
                self._activate_project_layer(existing_layer)
                self.iface.messageBar().pushMessage(
                    "OldMaps",
                    f"Шар уже додано в проект: {existing_layer.name()}",
                    level=Qgis.Warning,
                    duration=5,
                )
                self._refresh_layer_icons()
                return

        lock_view = (
            hasattr(self, "lock_view_action")
            and self.lock_view_action.isChecked()
        )
        locked_canvas_view = self._capture_canvas_view() if lock_view else None

        layer_name = self._qgis_layer_name(page, layer_data)
        uri = self._xyz_uri(
            tile_url,
            self._layer_min_zoom(layer_data),
            self._layer_max_zoom(layer_data),
            layer_data=layer_data,
        )
        raster = QgsRasterLayer(uri, layer_name, "wms")
        if not raster.isValid():
            self._show_warning(f"QGIS не зміг створити XYZ-шар: {layer_name}")
            return

        attribution = layer_data.get("attribution") or ""
        page_url = page.get("page_url") or self._script_page_url(layer_data) or OLDMAPS_HOME
        raster.setCustomProperty("oldmaps:attribution", attribution)
        raster.setCustomProperty("oldmaps:page_url", page_url)
        raster.setCustomProperty("oldmaps:tile_url", tile_url)
        layer_extent = self._set_oldmaps_layer_extent(raster, page, layer_data)

        if hasattr(raster, "setAttribution"):
            raster.setAttribution(self._plain_text(attribution))
        if hasattr(raster, "setAttributionUrl"):
            raster.setAttributionUrl(page_url)

        self._add_opacity_slider_to_legend(raster)
        QgsProject.instance().addMapLayer(raster)
        self._restore_oldmaps_layer_extent(raster)
        self._expand_layer_tree_node(raster)
        self._refresh_layer_icons()
        if not lock_view:
            if layer_extent is not None:
                self._center_canvas_on_added_layer_extent(layer_extent, WEB_MERCATOR)
                QTimer.singleShot(0, lambda layer=raster: self._restore_oldmaps_layer_extent(layer))
                QTimer.singleShot(
                    0,
                    lambda extent=layer_extent: self._center_canvas_on_added_layer_extent(
                        extent,
                        WEB_MERCATOR,
                    ),
                )
            else:
                self._center_canvas_on_page(page)
                QTimer.singleShot(0, lambda page=page: self._center_canvas_on_page(page))
        else:
            self._restore_canvas_view(locked_canvas_view)
            QTimer.singleShot(
                0,
                lambda view=locked_canvas_view, layer=raster: (
                    self._restore_layer_and_canvas_view(layer, view)
                ),
            )
        self.iface.messageBar().pushSuccess("OldMaps", f"Додано шар: {layer_name}")

    def _add_layer_record_with_busy_cursor(self, page, layer_data):
        QApplication.setOverrideCursor(Qt_WaitCursor)
        try:
            self.add_layer_record(page, layer_data)
        finally:
            QApplication.restoreOverrideCursor()

    def _layer_payload_from_mime(self, mime_data):
        try:
            payload = json.loads(bytes(mime_data.data(OLDMAPS_LAYER_MIME)).decode("utf-8"))
        except Exception:
            return None

        if not isinstance(payload, dict) or payload.get("type") != "layer":
            return None

        page = payload.get("page")
        layer = payload.get("layer")
        if not isinstance(page, dict) or not isinstance(layer, dict):
            return None
        if not layer.get("tile_url"):
            return None

        return {"page": page, "layer": layer}

    def open_selected_page(self):
        page_url = OLDMAPS_HOME
        payload = self._selected_record()
        if payload is not None:
            page_url = self._oldmaps_url_for_payload(payload.get("page"), payload.get("layer"))

        QDesktopServices.openUrl(QUrl.fromUserInput(page_url))

    def zoom_to_selected_item(self):
        payload = self._selected_record()
        if payload is None:
            return

        if payload.get("type") == "layer":
            page = payload.get("page") or {}
            layer_data = payload.get("layer") or {}
            extent_record = self._oldmaps_layer_extent(page, layer_data)
            if extent_record is not None:
                extent, _extent_source = extent_record
                self._center_canvas_on_added_layer_extent(extent, WEB_MERCATOR)
                return

        page = payload.get("page") or {}
        self._center_canvas_on_page(page)

    def update_zoom_label(self):
        zoom = self._estimate_xyz_zoom()
        self._updating_zoom_spin = True
        try:
            if zoom is not None:
                self.zoom_spin.setValue(max(self.zoom_spin.minimum(), min(self.zoom_spin.maximum(), zoom)))
        finally:
            self._updating_zoom_spin = False

        tooltip = "Поточний рівень зуму XYZ-шарів"
        #selected = self._selected_layer_record()
        #if selected is not None:
        #    tooltip = f"{tooltip}; max {self._layer_max_zoom(selected['layer'])}"

        self.zoom_spin.setToolTip(tooltip)
        self.zoom_icon_button.setToolTip(tooltip)

    def _on_zoom_spin_changed(self, value):
        if self._updating_zoom_spin:
            return
        self._set_canvas_xyz_zoom(float(value))

    def _set_oldmaps_layer_extent(self, layer, page, layer_data=None):
        extent_record = self._oldmaps_layer_extent(page, layer_data or {})
        if extent_record is None:
            return None
        extent, extent_source = extent_record

        layer.setCustomProperty("oldmaps:extent_source", extent_source)
        layer.setCustomProperty("oldmaps:extent_crs", "EPSG:3857")
        layer.setCustomProperty("oldmaps:extent_xmin", extent.xMinimum())
        layer.setCustomProperty("oldmaps:extent_ymin", extent.yMinimum())
        layer.setCustomProperty("oldmaps:extent_xmax", extent.xMaximum())
        layer.setCustomProperty("oldmaps:extent_ymax", extent.yMaximum())
        self._restore_oldmaps_layer_extent(layer)
        return extent

    def _restore_oldmaps_layer_extent(self, layer):
        if layer.customProperty("oldmaps:extent_crs", "") != "EPSG:3857":
            return

        try:
            extent = QgsRectangle(
                float(layer.customProperty("oldmaps:extent_xmin")),
                float(layer.customProperty("oldmaps:extent_ymin")),
                float(layer.customProperty("oldmaps:extent_xmax")),
                float(layer.customProperty("oldmaps:extent_ymax")),
            )
        except (TypeError, ValueError):
            return

        if not self._is_valid_extent(extent):
            return

        try:
            layer.setExtent(extent)
        except Exception:
            pass

    def _layer_extent_size(self):
        try:
            size = self.iface.mapCanvas().size()
            width = max(size.width(), LAYER_EXTENT_MIN_WIDTH)
            height = max(size.height(), LAYER_EXTENT_MIN_HEIGHT)
        except Exception:
            width = LAYER_EXTENT_MIN_WIDTH
            height = LAYER_EXTENT_MIN_HEIGHT
        return QSize(width, height)

    def _oldmaps_layer_extent(self, page, layer_data):
        extent = self._layer_borders_extent(layer_data)
        if extent is not None:
            return extent, "borders"

        extent = self._layer_bounds_extent(layer_data)
        if extent is not None:
            return extent, "bounds"

        extent = self._page_extent(
            page,
            WEB_MERCATOR,
            self._layer_extent_size(),
            zoom_padding=LAYER_EXTENT_ZOOM_PADDING,
        )
        if extent is not None:
            return extent, "page"

        return None

    def _layer_borders_extent(self, layer_data):
        tile_url = str((layer_data or {}).get("tile_url") or "").strip()
        if not tile_url:
            return None

        geometry_for_tile_url = getattr(self, "_coverage_geometry_for_tile_url", None)
        if geometry_for_tile_url is None:
            return None

        try:
            geometry = geometry_for_tile_url(tile_url)
        except Exception:
            return None

        if geometry is None or geometry.isNull():
            return None

        try:
            extent = geometry.boundingBox()
        except Exception:
            return None

        if not self._is_valid_extent(extent):
            return None
        return extent

    def _layer_bounds_extent(self, layer_data):
        bounds = layer_data.get("bounds")
        if not isinstance(bounds, dict):
            return None

        extent_data = bounds.get("extent") if isinstance(bounds.get("extent"), dict) else bounds
        try:
            extent = QgsRectangle(
                float(extent_data["xmin"]),
                float(extent_data["ymin"]),
                float(extent_data["xmax"]),
                float(extent_data["ymax"]),
            )
        except (KeyError, TypeError, ValueError):
            return None

        crs_value = str(bounds.get("crs") or "EPSG:3857")
        source_crs = QgsCoordinateReferenceSystem(crs_value)
        if source_crs.isValid() and source_crs != WEB_MERCATOR:
            try:
                transform = QgsCoordinateTransform(source_crs, WEB_MERCATOR, QgsProject.instance())
                extent = transform.transformBoundingBox(extent)
            except Exception:
                return None

        if not self._is_valid_extent(extent):
            return None
        return extent

    def _layer_bounds_zoom(self, layer_data):
        bounds = layer_data.get("bounds")
        if not isinstance(bounds, dict):
            return None

        try:
            zoom = float(bounds["zoom"])
        except (KeyError, TypeError, ValueError):
            return None

        if not math.isfinite(zoom) or zoom < 0:
            return None
        return zoom

    def _add_opacity_slider_to_legend(self, layer):
        layer.setCustomProperty("embeddedWidgets/count", 1)
        layer.setCustomProperty("embeddedWidgets/0/id", "transparency")

        try:
            layer_tree_view = self.iface.layerTreeView()
            model = layer_tree_view.layerTreeModel() if hasattr(layer_tree_view, "layerTreeModel") else layer_tree_view.model()
            if model is not None:
                model.setFlag(QgsLayerTreeModel.UseEmbeddedWidgets, True)
        except Exception:
            pass

    def _expand_layer_tree_node(self, layer):
        node = QgsProject.instance().layerTreeRoot().findLayer(layer.id())
        if node is not None:
            node.setExpanded(True)

        try:
            layer_tree_view = self.iface.layerTreeView()
            model = layer_tree_view.layerTreeModel() if hasattr(layer_tree_view, "layerTreeModel") else layer_tree_view.model()
            if node is not None and hasattr(model, "refreshLayerLegend"):
                model.refreshLayerLegend(node)
            layer_tree_view.refreshLayerSymbology(layer.id())
        except Exception:
            pass

    def _refresh_layer_icons(self, *args):
        self._restore_project_oldmaps_extents()
        existing_tile_urls = self._project_oldmaps_tile_urls()
        raster_icon = self._qgis_icon_from_path(
            ":/images/themes/default/mIconRaster.svg",
            get_enum(QStyle, "SP_FileIcon", "StandardPixmap"),
            ":mIconRaster.svg",
        )
        self.tree.refreshAppearance(existing_tile_urls, raster_icon)

    def _restore_project_oldmaps_extents(self):
        for layer in QgsProject.instance().mapLayers().values():
            tile_url = layer.customProperty("oldmaps:tile_url", "")
            if not tile_url:
                continue

            page_url = layer.customProperty("oldmaps:page_url", "")
            record = self._layer_record_for_project_layer(tile_url, page_url)
            if record is not None:
                page, layer_data = record
                self._set_oldmaps_layer_extent(layer, page, layer_data)
            else:
                self._restore_oldmaps_layer_extent(layer)

    def _layer_record_for_project_layer(self, tile_url, page_url):
        first_match = None
        for page in self.pages:
            if not isinstance(page, dict):
                continue

            layers = page.get("layers") or []
            if not isinstance(layers, list):
                continue

            current_page_url = page.get("page_url") or ""
            for layer_data in layers:
                if not isinstance(layer_data, dict):
                    continue
                if layer_data.get("tile_url") != tile_url:
                    continue

                if first_match is None:
                    first_match = (page, layer_data)

                layer_page_url = current_page_url or self._script_page_url(layer_data) or ""
                if page_url and page_url == layer_page_url:
                    return page, layer_data

        return first_match

    def _project_oldmaps_tile_urls(self):
        urls = set()
        for layer in QgsProject.instance().mapLayers().values():
            tile_url = layer.customProperty("oldmaps:tile_url", "")
            if tile_url:
                urls.add(tile_url)
        return urls

    def _find_project_layer_by_tile_url(self, tile_url):
        for layer in QgsProject.instance().mapLayers().values():
            if layer.customProperty("oldmaps:tile_url", "") == tile_url:
                return layer
        return None

    def _activate_project_layer(self, layer):
        root = QgsProject.instance().layerTreeRoot()
        node = root.findLayer(layer.id())
        if node is not None:
            node.setItemVisibilityChecked(True)
            parent = node.parent()
            while parent is not None:
                parent.setItemVisibilityChecked(True)
                parent.setExpanded(True)
                parent = parent.parent()
            node.setExpanded(True)

        try:
            layer_tree_view = self.iface.layerTreeView()
            layer_tree_view.setCurrentLayer(layer)
            if node is not None:
                layer_tree_view.scrollToLayer(layer)
        except Exception:
            pass

    def _qgis_icon(self, names, fallback):
        for name in names:
            icon = QgsApplication.getThemeIcon(name)
            if not icon.isNull():
                return icon
        return self.style().standardIcon(fallback)

    def _parse_zoom_value(self, value):
        try:
            zoom = float(value)
        except (TypeError, ValueError):
            return None

        if not math.isfinite(zoom) or zoom < 0:
            return None
        return int(zoom)

    def _qgis_icon_from_path(self, name, fallback=None, theme_name=None):
        icon = QIcon(name)
        if not icon.isNull():
            return icon

        path = QgsApplication.iconPath(theme_name or name)
        icon = QIcon(path) if path else QIcon()
        if not icon.isNull():
            return icon
        if fallback is None:
            return QIcon()
        return self.style().standardIcon(fallback)

    def _page_icon(self, page_name):
        cached_icon = self._page_icon_cache.get(page_name)
        if cached_icon is not None:
            return cached_icon

        icon_file = self._page_icon_mapping().get(page_name)
        if not icon_file:
            icon = QIcon(str(DEFAULT_PAGE_ICON_PATH))
            self._page_icon_cache[page_name] = icon
            return icon

        icon_path = FLAGS_AND_COATS_DIR / icon_file
        if not icon_path.exists() or icon_path.stat().st_size == 0:
            icon = QIcon(str(DEFAULT_PAGE_ICON_PATH))
            self._page_icon_cache[page_name] = icon
            return icon

        icon = QIcon(str(icon_path))
        self._page_icon_cache[page_name] = icon
        return icon

    def _page_icon_mapping(self):
        if self._page_icon_files is not None:
            return self._page_icon_files

        mapping = {}
        path = self._asset_file_path("data/page_icon_files.json")
        if path is not None and path.exists():
            cached_mapping = self._page_icon_files_from_path(path)
            if cached_mapping is not None:
                mapping = cached_mapping

        self._page_icon_files = mapping
        return mapping

    def _find_data_path(self):
        path = self._asset_file_path("data/oldmaps.gpkg") or self._asset_file_path("data/leaflet_layers.json")
        if path is None or not path.exists():
            return None
        return path

    def _estimate_xyz_zoom(self):
        canvas = self.iface.mapCanvas()
        width = canvas.size().width()
        if width <= 0:
            return None

        try:
            extent = canvas.extent()
            map_crs = canvas.mapSettings().destinationCrs()
            if map_crs.isValid() and map_crs != WEB_MERCATOR:
                transform = QgsCoordinateTransform(map_crs, WEB_MERCATOR, QgsProject.instance())
                extent = transform.transformBoundingBox(extent)

            resolution = abs(extent.width()) / width
            if not math.isfinite(resolution) or resolution <= 0:
                return None

            zoom = math.log(WEB_MERCATOR_INITIAL_RESOLUTION / resolution, 2)
            if not math.isfinite(zoom):
                return None
            return zoom
        except Exception:
            return None

    def _set_canvas_xyz_zoom(self, zoom):
        canvas = self.iface.mapCanvas()
        size = canvas.size()
        if size.width() <= 0 or size.height() <= 0:
            return

        try:
            extent = canvas.extent()
            map_crs = canvas.mapSettings().destinationCrs()
            center = extent.center()
            if map_crs.isValid() and map_crs != WEB_MERCATOR:
                to_web_mercator = QgsCoordinateTransform(map_crs, WEB_MERCATOR, QgsProject.instance())
                center = to_web_mercator.transform(center)

            resolution = WEB_MERCATOR_INITIAL_RESOLUTION / (2 ** zoom)
            width = resolution * size.width()
            height = resolution * size.height()
            new_extent = QgsRectangle(
                center.x() - width / 2,
                center.y() - height / 2,
                center.x() + width / 2,
                center.y() + height / 2,
            )

            if map_crs.isValid() and map_crs != WEB_MERCATOR:
                from_web_mercator = QgsCoordinateTransform(WEB_MERCATOR, map_crs, QgsProject.instance())
                new_extent = from_web_mercator.transformBoundingBox(new_extent)

            canvas.setExtent(new_extent)
            canvas.refresh()
        except Exception as exc:
            self._show_warning(f"Не вдалося змінити zoom: {exc}")

    def _xyz_uri(self, tile_url, min_zoom, max_zoom, layer_data=None):
        qgis_tile_url = tile_url
        uri_parts = [
            "type=xyz",
            f"url={qgis_tile_url}",
            f"zmin={min_zoom}",
            f"zmax={max_zoom}",
            "crs=EPSG:3857",
        ]

        if self.tile_proxy is not None:
            bounds = layer_data.get("bounds") if layer_data else None
            qgis_tile_url = self.tile_proxy.url_for(tile_url, bounds=bounds)
            uri_parts[1] = f"url={qgis_tile_url}"
        else:
            uri_parts.extend(
                [
                    f"referer={OLDMAPS_REFERER}",
                    f"http-header:referer={OLDMAPS_REFERER}",
                ]
            )

        return "&".join(uri_parts)

    def _layer_min_zoom(self, layer_data):
        min_zoom = None
        for key in ("custom_min_zoom", "min_zoom"):
            zoom = self._parse_zoom_value(layer_data.get(key))
            if zoom is not None:
                min_zoom = zoom
                break

        if min_zoom is None:
            bounds = layer_data.get("bounds")
            if isinstance(bounds, dict):
                if (
                    LOAD_SUPABASE_OVERVIEW_TILES
                    and (bounds.get("overview_tile_url") or bounds.get("overview_max_zoom"))
                ):
                    min_zoom = MIN_TILE_ZOOM
                else:
                    zoom = self._parse_zoom_value(bounds.get("zoom"))
                    if zoom is not None:
                        min_zoom = zoom

        if min_zoom is None:
            min_zoom = MIN_TILE_ZOOM

        if HIDE_ADDED_LAYERS_BELOW_MIN_ZOOM:
            min_zoom = max(min_zoom, ADDED_LAYER_MIN_VISIBLE_ZOOM)

        return min_zoom

    def _layer_max_zoom(self, layer_data):
        for key in ("custom_max_zoom", "max_zoom"):
            zoom = self._parse_zoom_value(layer_data.get(key))
            if zoom is not None:
                return zoom
        return 18

    def _qgis_layer_name(self, page, layer_data):
        page_name = str(page.get("name") or "OldMaps")
        layer_name = str(layer_data.get("name") or layer_data.get("variable") or "layer")
        return f"{page_name} - {layer_name}"

    def _display_layer_name(self, layer_data):
        layer_name = str(layer_data.get("name") or layer_data.get("label_html") or "Без назви")
        layer_name = MAX_ZOOM_RE.sub(" ", layer_name)
        layer_name = STATIC_VIEW_RE.sub(" ", layer_name)
        layer_name = re.sub(r"\s+:", ":", layer_name)
        layer_name = re.sub(r"\s+\)", ")", layer_name)
        layer_name = re.sub(r"\(\s+", "(", layer_name)
        layer_name = re.sub(r"\(\s*\)", "", layer_name)
        layer_name = re.sub(r"\s{2,}", " ", layer_name)
        layer_name = re.sub(r"\s+[-–]\s*$", "", layer_name)
        return layer_name.strip()

    def _layer_search_text(self, layer_data, layer_name):
        return str(layer_data.get("name") or "").casefold()

    def _center_canvas_on_extent(self, extent, source_crs):
        if not self._is_valid_extent(extent):
            return False

        canvas = self.iface.mapCanvas()
        target_extent = QgsRectangle(
            extent.xMinimum(),
            extent.yMinimum(),
            extent.xMaximum(),
            extent.yMaximum(),
        )

        try:
            target_crs = canvas.mapSettings().destinationCrs()
            if source_crs.isValid() and target_crs.isValid() and source_crs != target_crs:
                transform = QgsCoordinateTransform(source_crs, target_crs, QgsProject.instance())
                target_extent = transform.transformBoundingBox(target_extent)

            if not self._is_valid_extent(target_extent):
                return False

            canvas.setExtent(target_extent)
            canvas.refresh()
            return True
        except Exception as exc:
            self._show_warning(f"РќРµ РІРґР°Р»РѕСЃСЏ С†РµРЅС‚СЂСѓРІР°С‚Рё РєР°СЂС‚Сѓ: {exc}")
            return False

    def _capture_canvas_view(self):
        canvas = self.iface.mapCanvas()
        try:
            extent = canvas.extent()
            if not self._is_valid_extent(extent):
                return None

            return (
                QgsRectangle(
                    extent.xMinimum(),
                    extent.yMinimum(),
                    extent.xMaximum(),
                    extent.yMaximum(),
                ),
                QgsCoordinateReferenceSystem(
                    canvas.mapSettings().destinationCrs()
                ),
            )
        except Exception:
            return None

    def _restore_canvas_view(self, view):
        if view is None:
            return

        extent, source_crs = view
        target_extent = QgsRectangle(
            extent.xMinimum(),
            extent.yMinimum(),
            extent.xMaximum(),
            extent.yMaximum(),
        )
        canvas = self.iface.mapCanvas()
        try:
            target_crs = canvas.mapSettings().destinationCrs()
            if source_crs.isValid() and target_crs.isValid() and source_crs != target_crs:
                transform = QgsCoordinateTransform(
                    source_crs,
                    target_crs,
                    QgsProject.instance(),
                )
                target_extent = transform.transformBoundingBox(target_extent)

            if not self._is_valid_extent(target_extent):
                return

            canvas.setExtent(target_extent)
            canvas.refresh()
        except Exception:
            pass

    def _restore_layer_and_canvas_view(self, layer, view):
        self._restore_oldmaps_layer_extent(layer)
        self._restore_canvas_view(view)

    def _center_canvas_on_added_layer_extent(self, extent, source_crs):
        if not self._center_canvas_on_extent(extent, source_crs):
            return

        zoom = self._estimate_xyz_zoom()
        if zoom is not None and zoom < ADD_LAYER_MIN_VIEW_ZOOM:
            self._set_canvas_xyz_zoom(ADD_LAYER_MIN_VIEW_ZOOM)

    def _center_canvas_on_extent_center(self, extent, source_crs, zoom=None):
        if not self._is_valid_extent(extent):
            return

        if zoom is None:
            self._center_canvas_on_extent(extent, source_crs)
            return

        canvas = self.iface.mapCanvas()
        size = canvas.size()
        if size.width() <= 0 or size.height() <= 0:
            return

        try:
            center = extent.center()
            if source_crs.isValid() and source_crs != WEB_MERCATOR:
                to_web_mercator = QgsCoordinateTransform(source_crs, WEB_MERCATOR, QgsProject.instance())
                center = to_web_mercator.transform(center)

            resolution = WEB_MERCATOR_INITIAL_RESOLUTION / (2 ** zoom)
            width = resolution * size.width()
            height = resolution * size.height()
            target_extent = QgsRectangle(
                center.x() - width / 2,
                center.y() - height / 2,
                center.x() + width / 2,
                center.y() + height / 2,
            )

            target_crs = canvas.mapSettings().destinationCrs()
            if target_crs.isValid() and target_crs != WEB_MERCATOR:
                from_web_mercator = QgsCoordinateTransform(WEB_MERCATOR, target_crs, QgsProject.instance())
                target_extent = from_web_mercator.transformBoundingBox(target_extent)

            if not self._is_valid_extent(target_extent):
                return

            canvas.setExtent(target_extent)
            canvas.refresh()
        except Exception as exc:
            self._show_warning(f"РќРµ РІРґР°Р»РѕСЃСЏ Р·РјС–РЅРёС‚Рё zoom: {exc}")

    def _center_canvas_on_page(self, page):
        canvas = self.iface.mapCanvas()
        size = canvas.size()
        if size.width() <= 0 or size.height() <= 0:
            return

        try:
            target_crs = canvas.mapSettings().destinationCrs()
            extent = self._page_extent(page, target_crs, size)
            if extent is None:
                return

            canvas.setExtent(extent)
            canvas.refresh()
        except Exception as exc:
            self._show_warning(f"Не вдалося центрувати карту: {exc}")

    def _page_extent(self, page, target_crs, size, zoom_padding=0):
        view = self._page_map_view(page)
        if view is None:
            return None

        lat, lng, zoom = view
        width_px = size.width()
        height_px = size.height()
        if width_px <= 0 or height_px <= 0:
            return None

        zoom = max(0.0, zoom - float(zoom_padding))
        to_web_mercator = QgsCoordinateTransform(WGS84, WEB_MERCATOR, QgsProject.instance())
        center = to_web_mercator.transform(QgsPointXY(lng, lat))
        resolution = WEB_MERCATOR_INITIAL_RESOLUTION / (2 ** zoom)
        width = resolution * width_px
        height = resolution * height_px
        extent = QgsRectangle(
            center.x() - width / 2,
            center.y() - height / 2,
            center.x() + width / 2,
            center.y() + height / 2,
        )

        if target_crs.isValid() and target_crs != WEB_MERCATOR:
            transform = QgsCoordinateTransform(WEB_MERCATOR, target_crs, QgsProject.instance())
            extent = transform.transformBoundingBox(extent)

        if not self._is_valid_extent(extent):
            return None
        return extent

    def _is_valid_extent(self, extent):
        return (
            extent is not None
            and not extent.isEmpty()
            and math.isfinite(extent.xMinimum())
            and math.isfinite(extent.yMinimum())
            and math.isfinite(extent.xMaximum())
            and math.isfinite(extent.yMaximum())
        )

    def _page_map_view(self, page):
        map_view = page.get("map_view")
        if not isinstance(map_view, dict):
            map_view = page

        center = map_view.get("center") or page.get("center")
        if not isinstance(center, dict):
            return None

        try:
            lat = float(center["lat"])
            lng = float(center["lng"])
            zoom = float(map_view.get("zoom", page.get("zoom")))
        except (KeyError, TypeError, ValueError):
            return None

        return lat, lng, zoom

    def _oldmaps_url_for_payload(self, page, layer):
        page = page or {}
        layer = layer or {}
        page_url = page.get("page_url") or self._script_page_url(layer) or OLDMAPS_HOME
        if layer:
            variable = str(layer.get("variable") or "")
            leftmap = variable[5:] if variable.startswith("layer") and variable[5:] else layer.get("year")
            map_view = page.get("map_view") if isinstance(page.get("map_view"), dict) else page
            zoom = map_view.get("zoom") if isinstance(map_view, dict) else None
            if leftmap is not None and zoom is not None:
                return f"{str(page_url).rstrip('/')}/?leftmap={leftmap}#{zoom}"
        return page_url

    def _script_page_url(self, layer_data):
        script_source = layer_data.get("script_source") or ""
        if "#" in script_source:
            return script_source.split("#", 1)[0]
        return script_source or None

    def _plain_text(self, value):
        text = str(value or "")
        text = html.unescape(text)
        return " ".join(text.replace("<br>", " ").replace("<br/>", " ").split())

    def _show_warning(self, message):
        self.iface.messageBar().pushWarning("OldMaps", message)
