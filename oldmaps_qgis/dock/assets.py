# -*- coding: utf-8 -*-
import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from qgis.core import Qgis, QgsMessageLog, QgsVectorLayer
from qgis.PyQt.QtCore import QStandardPaths, QUrl
from qgis.PyQt.QtGui import QPixmap
from qgis.PyQt.QtNetwork import QNetworkReply, QNetworkRequest

from ..qt_compat import get_enum

from .constants import (
    BORDERS_LAYER_NAME,
    OLDMAPS_LOG_TAG,
    PLUGIN_DIR,
    REMOTE_ASSET_TIMEOUT_MS,
    REMOTE_BORDERS_URL,
    REMOTE_CACHE_DIR_NAME,
    REMOTE_CACHE_MANIFEST_NAME,
    REMOTE_GPKG_MANIFEST_URL,
    REMOTE_GPKG_URL,
    REMOTE_HELP_BANNER_URL,
    REMOTE_LAYERS_URL,
    OLDMAPS_DATA_SERVER,
    REMOTE_PAGE_ICON_FILES_URL,
)


def remote_cache_dir():
    try:
        cache_location = QStandardPaths.writableLocation(
            QStandardPaths.CacheLocation
        )
    except AttributeError:
        cache_location = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.CacheLocation
        )

    if not cache_location:
        cache_location = tempfile.gettempdir()

    return Path(cache_location) / REMOTE_CACHE_DIR_NAME


def remote_cache_size():
    cache_dir = remote_cache_dir()
    if not cache_dir.exists():
        return 0

    total_size = 0
    try:
        for root, _, files in os.walk(cache_dir):
            for file in files:
                try:
                    total_size += os.path.getsize(os.path.join(root, file))
                except OSError:
                    pass
    except OSError:
        pass
    return total_size


def format_cache_size(size):
    value = float(max(0, size))
    units = ("Б", "КБ", "МБ", "ГБ")
    for unit in units:
        if value < 1024 or unit == units[-1]:
            if unit == "Б":
                return f"{int(value)} {unit}"
            return f"{value:.1f}".replace(".", ",") + f" {unit}"
        value /= 1024
    return "0 Б"


def clear_remote_cache_dir():
    cache_dir = remote_cache_dir()
    if cache_dir.name != REMOTE_CACHE_DIR_NAME:
        return False
    if not cache_dir.exists():
        return True

    for path in cache_dir.iterdir():
        try:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
        except OSError:
            return False
    return True


class RemoteCacheMixin:
    REMOTE_ASSETS = (
        {
            "key": "data/oldmaps.gpkg",
            "url": REMOTE_GPKG_URL,
            "manifest_url": REMOTE_GPKG_MANIFEST_URL,
            "type": "gpkg",
        },
        {
            "key": "data/page_icon_files.json",
            "url": REMOTE_PAGE_ICON_FILES_URL,
            "type": "page_icons",
        },
        {
            "key": "icons/help_banner.jpg",
            "url": REMOTE_HELP_BANNER_URL,
            "type": "image",
        },
    )

    def _start_remote_asset_sync(self):
        self._asset_sync_generation += 1
        generation = self._asset_sync_generation
        self._cancel_remote_asset_sync()
        self._hide_catalog_retry_overlay()

        self._asset_manifest = self._read_cache_manifest()
        self._asset_queue = list(self.REMOTE_ASSETS)
        self._asset_current = None
        self._asset_request_kind = None
        self._asset_sync_running = True
        self._asset_errors = []
        self._process_next_remote_asset(generation)

    def _cancel_remote_asset_sync(self):
        self.asset_timeout_timer.stop()
        self._asset_sync_running = False
        reply = self._asset_reply
        self._asset_reply = None
        self._asset_current = None
        self._asset_request_kind = None
        if reply is None:
            return

        try:
            reply.finished.disconnect()
        except (RuntimeError, TypeError):
            pass
        try:
            if reply.isRunning():
                reply.abort()
        except RuntimeError:
            pass
        try:
            reply.deleteLater()
        except RuntimeError:
            pass

    def _on_remote_asset_timeout(self):
        if self._asset_reply is None:
            return
        self._asset_reply.abort()

    def _process_next_remote_asset(self, generation):
        if generation != self._asset_sync_generation:
            return
        if not self._asset_queue:
            self._finish_remote_asset_sync(generation)
            return

        asset = self._asset_queue.pop(0)
        if asset.get("manifest_url"):
            self._start_remote_asset_manifest_request(generation, asset)
            return

        self._start_remote_asset_head_request(generation, asset)

    def _start_remote_asset_head_request(self, generation, asset):
        self._asset_current = asset
        self._asset_request_kind = "head"
        request = QNetworkRequest(QUrl(asset["url"]))
        self._asset_reply = self.network_manager.head(request)
        self._asset_reply.finished.connect(
            lambda generation=generation: self._on_remote_asset_head_finished(
                generation
            )
        )
        self.asset_timeout_timer.start(REMOTE_ASSET_TIMEOUT_MS)

    def _start_remote_asset_manifest_request(self, generation, asset):
        self._asset_current = asset
        self._asset_request_kind = "manifest"
        request = QNetworkRequest(QUrl(asset["manifest_url"]))
        self._asset_reply = self.network_manager.get(request)
        self._asset_reply.finished.connect(
            lambda generation=generation: self._on_remote_asset_manifest_finished(
                generation
            )
        )
        self.asset_timeout_timer.start(REMOTE_ASSET_TIMEOUT_MS)

    def _on_remote_asset_manifest_finished(self, generation):
        reply = self._asset_reply
        asset = self._asset_current
        if reply is None or asset is None:
            return

        self._asset_reply = None
        self._asset_current = None
        self._asset_request_kind = None
        self.asset_timeout_timer.stop()
        metadata = None
        try:
            if generation == self._asset_sync_generation:
                status_code = self._asset_reply_http_status(reply)
                if (
                    reply.error() == get_enum(QNetworkReply, "NoError", "NetworkError")
                    and (status_code is None or status_code == 200)
                ):
                    content = bytes(reply.readAll()).decode("utf-8-sig", "replace")
                    metadata = self._asset_metadata_from_manifest(asset, content)
        finally:
            reply.deleteLater()

        if generation != self._asset_sync_generation:
            return

        if not metadata or not metadata.get("version_key"):
            self._start_remote_asset_head_request(generation, asset)
            return

        resolved_asset = dict(asset)
        resolved_asset["url"] = metadata["url"]
        if self._asset_cache_matches_remote(resolved_asset, metadata):
            self._on_remote_asset_available(resolved_asset, changed=False)
            self._process_next_remote_asset(generation)
            return

        self._start_remote_asset_download(generation, resolved_asset, metadata)

    def _on_remote_asset_head_finished(self, generation):
        reply = self._asset_reply
        asset = self._asset_current
        if reply is None or asset is None:
            return

        self._asset_reply = None
        self._asset_current = None
        self._asset_request_kind = None
        self.asset_timeout_timer.stop()
        metadata = None
        try:
            if generation == self._asset_sync_generation:
                status_code = self._asset_reply_http_status(reply)
                if (
                    reply.error() == get_enum(QNetworkReply, "NoError", "NetworkError")
                    and (status_code is None or status_code == 200)
                ):
                    metadata = self._asset_metadata_from_reply(asset, reply)
        finally:
            reply.deleteLater()

        if generation != self._asset_sync_generation:
            return

        if not metadata or not metadata.get("version_key"):
            self._use_cached_asset_or_record_error(
                asset,
                "remote HEAD has no version metadata",
            )
            self._process_next_remote_asset(generation)
            return

        if self._asset_cache_matches_remote(asset, metadata):
            self._on_remote_asset_available(asset, changed=False)
            self._process_next_remote_asset(generation)
            return

        self._start_remote_asset_download(generation, asset, metadata)

    def _start_remote_asset_download(self, generation, asset, metadata):
        self._asset_current = {"asset": asset, "metadata": metadata}
        self._asset_request_kind = "get"
        request = QNetworkRequest(QUrl(asset["url"]))
        self._asset_reply = self.network_manager.get(request)
        self._asset_reply.finished.connect(
            lambda generation=generation: self._on_remote_asset_download_finished(
                generation
            )
        )
        self.asset_timeout_timer.start(REMOTE_ASSET_TIMEOUT_MS)

    def _on_remote_asset_download_finished(self, generation):
        reply = self._asset_reply
        current = self._asset_current
        if reply is None or current is None:
            return

        self._asset_reply = None
        self._asset_current = None
        self._asset_request_kind = None
        self.asset_timeout_timer.stop()
        asset = current["asset"]
        metadata = current["metadata"]
        content = None
        try:
            if generation == self._asset_sync_generation:
                status_code = self._asset_reply_http_status(reply)
                if (
                    reply.error() == get_enum(QNetworkReply, "NoError", "NetworkError")
                    and (status_code is None or status_code == 200)
                ):
                    content = bytes(reply.readAll())
        finally:
            reply.deleteLater()

        if generation != self._asset_sync_generation:
            return

        if content and self._install_remote_asset(asset, metadata, content):
            self._on_remote_asset_available(asset, changed=True)
        else:
            self._use_cached_asset_or_record_error(
                asset,
                "remote GET failed or downloaded file is invalid",
            )
        self._process_next_remote_asset(generation)

    def _install_remote_asset(self, asset, metadata, content):
        cache_path = self._remote_cache_file_path(asset["key"])
        if cache_path is None:
            return False

        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            return False

        temp_path = cache_path.with_name(f"{cache_path.name}.download")
        try:
            temp_path.write_bytes(content)
            if not self._is_valid_remote_asset(asset, temp_path):
                return False

            if asset["type"] == "borders":
                self._release_coverage_layer_for_borders_update()
            temp_path.replace(cache_path)
            manifest_entry = dict(metadata)
            manifest_entry["downloaded_at"] = self._utc_timestamp()
            self._asset_manifest.setdefault("files", {})[asset["key"]] = manifest_entry
            self._write_cache_manifest()
            return True
        except OSError:
            return False
        finally:
            try:
                if temp_path.exists():
                    temp_path.unlink()
            except OSError:
                pass

    def _use_cached_asset_or_record_error(self, asset, reason):
        asset_path = self._asset_file_path(asset["key"])
        if asset_path is not None and self._is_valid_remote_asset(asset, asset_path):
            self._on_remote_asset_available(asset, changed=False)
            QgsMessageLog.logMessage(
                f"{asset['key']} remote update skipped ({reason}), using available asset at {asset_path}",
                OLDMAPS_LOG_TAG,
                Qgis.Warning,
            )
            return True

        self._asset_errors.append(f"{asset['key']}: {reason}")
        return False

    def _finish_remote_asset_sync(self, generation):
        if generation != self._asset_sync_generation:
            return

        self._asset_sync_running = False
        self._asset_queue = []
        self._asset_current = None
        self._asset_request_kind = None

        if not self._layers_loaded and not self.data_source:
            detail = (
                "Перевірте підключення до інтернету та натисніть "
                "«Перезавантажити»."
            )
            self._on_catalog_load_failed(
                "Не вдалося завантажити каталог OldMaps",
                detail,
            )

    def _on_remote_asset_available(self, asset, changed):
        asset_type = asset["type"]
        if asset_type in ("gpkg", "layers"):
            if changed and self.data_source:
                self._reload_layers_tree_from_cache()
            elif not self.data_source:
                self._load_layers_from_cache()
            if asset_type == "gpkg":
                self._borders_path = self._asset_file_path(asset["key"])
                self._borders_loaded = True
                self._borders_loading = False
                if changed:
                    self._reset_coverage_layer_for_borders_update()
                if hasattr(self, "_restore_project_oldmaps_extents"):
                    self._restore_project_oldmaps_extents()
        elif asset_type == "borders":
            self._borders_path = self._asset_file_path(asset["key"])
            self._borders_loaded = True
            self._borders_loading = False
            if changed:
                self._reset_coverage_layer_for_borders_update()
            if hasattr(self, "_restore_project_oldmaps_extents"):
                self._restore_project_oldmaps_extents()
        elif asset_type == "page_icons":
            self._page_icon_files = None
            self._page_icon_cache = {}
            if hasattr(self, "tree"):
                self._refresh_layer_icons()
        elif asset_type == "image":
            return

    def _asset_cache_matches_remote(self, asset, metadata):
        cache_path = self._remote_cache_file_path(asset["key"])
        if cache_path is None or not self._is_valid_remote_asset(asset, cache_path):
            return False

        current_entry = self._asset_manifest.get("files", {}).get(asset["key"], {})
        return (
            current_entry.get("url") == metadata.get("url")
            and current_entry.get("version_key") == metadata.get("version_key")
        )

    def _is_valid_remote_asset(self, asset, path):
        path = Path(path) if path is not None else None
        if path is None or not path.exists() or path.stat().st_size == 0:
            return False

        asset_type = asset["type"]
        if asset_type in ("gpkg", "layers"):
            return self._layers_candidate_from_path(path) is not None
        if asset_type == "borders":
            return self._is_valid_borders_file(path)
        if asset_type == "page_icons":
            return self._page_icon_files_from_path(path) is not None
        if asset_type == "image":
            return not QPixmap(str(path)).isNull()
        return False

    def _layers_candidate_from_path(self, path):
        path = Path(path)
        if not path.exists():
            return None

        cache_dir = self._remote_cache_dir()
        source = "cache" if (cache_dir is not None and cache_dir in path.parents) else "local"

        if path.suffix.lower() in (".gpkg", ".sqlite", ".db"):
            try:
                import sqlite3
                conn = sqlite3.connect(str(path))
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()

                cursor.execute("SELECT key, value FROM metadata;")
                metadata = {row["key"]: row["value"] for row in cursor.fetchall()}

                cursor.execute("SELECT * FROM pages ORDER BY order_index ASC;")
                pages_rows = cursor.fetchall()

                pages = []
                for page_row in pages_rows:
                    page_url = page_row["page_url"]
                    page_dict = {
                        "name": page_row["name"],
                        "page_url": page_url,
                        "layers_count": page_row["layers_count"] or 0,
                    }
                    if page_row["center_lat"] is not None and page_row["center_lng"] is not None:
                        map_view = {
                            "center": {
                                "lat": page_row["center_lat"],
                                "lng": page_row["center_lng"],
                            }
                        }
                        if page_row["zoom"] is not None:
                            map_view["zoom"] = page_row["zoom"]
                        if page_row["center_variable"]:
                            map_view["center_variable"] = page_row["center_variable"]
                        page_dict["map_view"] = map_view

                    cursor.execute("SELECT * FROM layers WHERE page_url = ? ORDER BY order_index ASC;", (page_url,))
                    layers_rows = cursor.fetchall()
                    layers = []
                    for layer_row in layers_rows:
                        bounds = None
                        if layer_row["bounds_json"]:
                            try:
                                bounds = json.loads(layer_row["bounds_json"])
                            except Exception:
                                bounds = None

                        if bounds is not None and isinstance(bounds, dict):
                            keys = layer_row.keys()
                            if "overview_tile_url" in keys and layer_row["overview_tile_url"]:
                                bounds["overview_tile_url"] = layer_row["overview_tile_url"]
                            if "overview_max_zoom" in keys and layer_row["overview_max_zoom"] is not None:
                                bounds["overview_max_zoom"] = layer_row["overview_max_zoom"]

                        layer_dict = {
                            "id": layer_row["id"],
                            "name": layer_row["name"],
                            "label_html": layer_row["label_html"] or layer_row["name"],
                            "tile_url": layer_row["tile_url"],
                            "year": layer_row["year"],
                            "attribution": layer_row["attribution"] or "",
                            "custom_max_zoom": layer_row["custom_max_zoom"],
                            "max_zoom": layer_row["max_zoom"],
                            "bounds": bounds,
                        }
                        layers.append(layer_dict)

                    page_dict["layers"] = layers
                    pages.append(page_dict)

                conn.close()
                return self._layers_candidate_from_data({"metadata": metadata, "pages": pages}, source)
            except Exception as exc:
                QgsMessageLog.logMessage(f"Error reading GPKG catalog from {path}: {exc}", OLDMAPS_LOG_TAG, Qgis.Warning)
                return None

        try:
            with path.open("r", encoding="utf-8-sig") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            return None
        return self._layers_candidate_from_data(data, source)

    def _page_icon_files_from_path(self, path):
        try:
            with Path(path).open("r", encoding="utf-8-sig") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            return None

        if not isinstance(data, dict):
            return None

        return {
            str(page_name): icon_file
            for page_name, icon_file in data.items()
            if isinstance(icon_file, str)
        }

    def _is_valid_borders_file(self, path):
        layer = QgsVectorLayer(
            f"{Path(path)}|layername={BORDERS_LAYER_NAME}",
            "OldMaps map borders",
            "ogr",
        )
        try:
            return layer.isValid() and layer.fields().indexFromName("tile_url") != -1
        finally:
            del layer

    def _coverage_borders_path(self, warn=True):
        borders_path = self._asset_file_path("data/oldmaps.gpkg") or self._asset_file_path("data/borders.gpkg")
        if borders_path is not None and self._is_valid_borders_file(borders_path):
            return borders_path

        if warn:
            if self._asset_sync_running:
                self._show_warning(
                    "Межі карт ще завантажуються. "
                    "Спробуйте ще раз за кілька секунд."
                )
            else:
                self._show_warning("Не знайдено валідний файл меж карт oldmaps.gpkg у кеші або директорії плагіна.")
        return None

    def _reset_coverage_layer_for_borders_update(self):
        self._release_coverage_layer_for_borders_update()

        if (
            hasattr(self, "all_borders_action")
            and self.all_borders_action.isChecked()
        ):
            self._show_all_coverage_geometries()

    def _release_coverage_layer_for_borders_update(self):
        if hasattr(self, "_clear_coverage_highlight"):
            self._clear_coverage_highlight()
        if hasattr(self, "_clear_all_coverage_geometries"):
            self._clear_all_coverage_geometries()

        self._coverage_layer = None
        self._coverage_geometry_cache = {}
        self._coverage_geometry_cache_loaded = False
        self._coverage_missing_geometry_urls = set()

    def _asset_metadata_from_reply(self, asset, reply):
        etag = self._reply_header(reply, "ETag")
        last_modified = self._reply_header(reply, "Last-Modified")
        content_length = self._reply_header(reply, "Content-Length")
        version_key = etag or (
            f"{last_modified}:{content_length}"
            if last_modified and content_length
            else ""
        )
        return {
            "url": asset["url"],
            "etag": etag,
            "last_modified": last_modified,
            "content_length": content_length,
            "version_key": version_key,
        }

    def _asset_metadata_from_manifest(self, asset, content):
        try:
            manifest = json.loads(content)
        except (TypeError, ValueError):
            return None

        if not isinstance(manifest, dict):
            return None

        gpkg_info = manifest.get("gpkg") or {}
        if not isinstance(gpkg_info, dict):
            gpkg_info = {}

        asset_url = (
            gpkg_info.get("url")
            or manifest.get("url")
            or gpkg_info.get("key")
            or manifest.get("key")
        )
        if not asset_url:
            return None

        file_version = str(
            manifest.get("file_version")
            or gpkg_info.get("file_version")
            or ""
        ).strip()
        sha256 = str(gpkg_info.get("sha256") or manifest.get("sha256") or "").strip()
        content_length = str(gpkg_info.get("size") or manifest.get("size") or "").strip()
        version_key = file_version or sha256 or asset_url

        return {
            "url": self._absolute_remote_asset_url(asset_url),
            "manifest_url": asset.get("manifest_url", ""),
            "file_version": file_version,
            "version_date": str(manifest.get("version_date") or ""),
            "updated_at": str(manifest.get("updated_at") or ""),
            "published_at": str(manifest.get("published_at") or ""),
            "sha256": sha256,
            "content_length": content_length,
            "version_key": version_key,
        }

    def _absolute_remote_asset_url(self, url_or_key):
        value = str(url_or_key or "").strip()
        if value.startswith("http://") or value.startswith("https://"):
            return value
        return f"{OLDMAPS_DATA_SERVER}{value.lstrip('/')}"

    def _reply_header(self, reply, name):
        try:
            value = bytes(reply.rawHeader(name.encode("ascii"))).decode(
                "utf-8",
                "replace",
            )
        except Exception:
            return ""
        return value.strip()

    def _asset_reply_http_status(self, reply):
        try:
            attr = get_enum(QNetworkRequest, "HttpStatusCodeAttribute", "Attribute")
            status_code = reply.attribute(attr) if attr is not None else None
        except Exception:
            return None

        try:
            return int(status_code)
        except (TypeError, ValueError):
            return None

    def _remote_cache_file_path(self, key):
        cache_dir = self._remote_cache_dir()
        if cache_dir is None:
            return None
        return cache_dir / key

    def _asset_file_path(self, key):
        cache_path = self._remote_cache_file_path(key)
        if cache_path is not None and cache_path.exists() and cache_path.stat().st_size > 0:
            return cache_path

        local_path = PLUGIN_DIR / key
        if local_path.exists() and local_path.stat().st_size > 0:
            return local_path

        alt_local_path = PLUGIN_DIR / Path(key).name
        if alt_local_path.exists() and alt_local_path.stat().st_size > 0:
            return alt_local_path

        repo_path = PLUGIN_DIR.parent / key
        if repo_path.exists() and repo_path.stat().st_size > 0:
            return repo_path

        return None

    def _remote_cache_manifest_path(self):
        cache_dir = self._remote_cache_dir()
        if cache_dir is None:
            return None
        return cache_dir / REMOTE_CACHE_MANIFEST_NAME

    def _remote_cache_dir(self):
        return remote_cache_dir()

    def clear_remote_cache(self):
        self._asset_sync_generation += 1
        self._cancel_remote_asset_sync()
        if hasattr(self, "coverage_action") and self.coverage_action.isChecked():
            self.coverage_action.setChecked(False)
        elif hasattr(self, "_coverage_click_tool"):
            self._deactivate_coverage_mode(clear_filter=True)
        if hasattr(self, "all_borders_action") and self.all_borders_action.isChecked():
            self.all_borders_action.setChecked(False)
        self._release_coverage_layer_for_borders_update()

        cleared = clear_remote_cache_dir()
        self._asset_manifest = {"files": {}}
        self._asset_errors = []
        self._borders_loaded = False
        self._borders_loading = False
        self._borders_path = None
        self._page_icon_files = None
        self._page_icon_cache = {}
        self._reset_layers_after_cache_clear()
        return cleared

    def _read_cache_manifest(self):
        manifest_path = self._remote_cache_manifest_path()
        if manifest_path is None or not manifest_path.exists():
            return {"files": {}}

        try:
            with manifest_path.open("r", encoding="utf-8-sig") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            return {"files": {}}

        if not isinstance(data, dict):
            return {"files": {}}
        if not isinstance(data.get("files"), dict):
            data["files"] = {}
        return data

    def _write_cache_manifest(self):
        manifest_path = self._remote_cache_manifest_path()
        if manifest_path is None:
            return

        try:
            manifest_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            return

        temp_path = manifest_path.with_suffix(".json.download")
        try:
            temp_path.write_text(
                json.dumps(self._asset_manifest, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temp_path.replace(manifest_path)
        except OSError:
            try:
                if temp_path.exists():
                    temp_path.unlink()
            except OSError:
                pass

    def _help_banner_path(self):
        path = self._asset_file_path("icons/help_banner.jpg")
        if path is not None and path.exists():
            return path
        return None

    def _utc_timestamp(self):
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
