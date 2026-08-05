# -*- coding: utf-8 -*-

import json

from qgis.core import Qgis, QgsMessageLog
from qgis.PyQt.QtCore import QTimer

from .constants import OLDMAPS_LOG_TAG


class CatalogMixin:
    def ensure_layers_loaded(self):
        if self._layers_loaded or self._layers_loading:
            return
        self.reload_layers()

    def reload_layers(self):
        self._start_layers_load()

    def _start_layers_load(self):
        self._layers_load_generation += 1
        self._cancel_layers_request()
        self.search_timer.stop()
        self._cancel_tree_filter()
        self._search_filter_active = False
        self._saved_expansion_state = None
        self._coverage_filter_tile_urls = None
        self._coverage_filter_point = None
        self._clear_coverage_highlight()
        self._hide_coverage_status()
        self.tree.clear()
        self.data_metadata = {}
        self.data_source = ""
        self.pages = []
        self.layer_count = 0
        self._counted_layer_urls = set()
        self._pending_pages = []
        self._pending_page_index = 0
        self._local_layers_candidate = None
        self._layers_loaded = False
        self._layers_loading = True
        self._update_action_state()
        self.update_zoom_label()
        self._load_layers_from_cache()
        self._start_remote_asset_sync()

    def _cancel_layers_request(self):
        self._cancel_remote_asset_sync()

    def _load_layers_from_cache(self):
        candidate = self._load_cached_layers_candidate()
        if candidate is None:
            return False

        self._hide_catalog_retry_overlay()
        self._select_layers_candidate(candidate)
        return True

    def _reload_layers_tree_from_cache(self):
        self._layers_load_generation += 1
        self._cancel_tree_filter()
        self.tree.clear()
        self.data_metadata = {}
        self.data_source = ""
        self.pages = []
        self.layer_count = 0
        self._counted_layer_urls = set()
        self._pending_pages = []
        self._pending_page_index = 0
        self._layers_loaded = False
        self._layers_loading = True
        self._update_action_state()
        self.update_zoom_label()
        return self._load_layers_from_cache()

    def _reset_layers_after_cache_clear(self):
        self._layers_load_generation += 1
        self.search_timer.stop()
        self._cancel_tree_filter()
        self._search_filter_active = False
        self._saved_expansion_state = None
        self._coverage_filter_tile_urls = None
        self._coverage_filter_point = None
        self._clear_coverage_highlight()
        self._hide_coverage_status()
        self.tree.clear()
        self.data_metadata = {}
        self.data_source = ""
        self.pages = []
        self.layer_count = 0
        self._counted_layer_urls = set()
        self._pending_pages = []
        self._pending_page_index = 0
        self._local_layers_candidate = None
        self._layers_loaded = False
        self._layers_loading = False
        self._update_action_state()
        self._update_tree_expansion_action()
        self.update_zoom_label()
        self._show_catalog_retry_overlay(
            "Кеш OldMaps очищено",
            "Натисніть «Перезавантажити», щоб завантажити каталог.",
        )

    def _load_cached_layers_candidate(self):
        path = self._asset_file_path("data/oldmaps.gpkg") or self._asset_file_path("data/leaflet_layers.json")
        if path is None or not path.exists():
            return None

        return self._layers_candidate_from_path(path)

    def _layers_candidate_from_data(self, data, source):
        if isinstance(data, dict):
            metadata = data.get("metadata")
            pages = data.get("pages")
        else:
            metadata = {}
            pages = data

        if not isinstance(metadata, dict):
            metadata = {}
        if not isinstance(pages, list):
            return None

        return {
            "source": source,
            "metadata": metadata,
            "pages": pages,
            "file_version": self._metadata_file_version(metadata),
        }

    def _metadata_file_version(self, metadata):
        try:
            return int(str(metadata.get("file_version", "")).strip())
        except (TypeError, ValueError):
            return -1

    def _select_layers_candidate(self, candidate):
        if candidate is None:
            self._on_catalog_load_failed("Не вдалося завантажити каталог OldMaps")
            return

        self._start_tree_build(candidate)

    def _start_tree_build(self, candidate):
        self.data_metadata = candidate["metadata"]
        self.data_source = candidate["source"]
        self.pages = candidate["pages"]
        self._pending_pages = self.pages
        self._pending_page_index = 0
        self._log_layers_source(candidate)
        QTimer.singleShot(
            0,
            lambda generation=self._layers_load_generation: self._build_next_page_batch(
                generation
            ),
        )

    def _on_catalog_load_failed(self, message, detail=""):
        self._pending_pages = []
        self._pending_page_index = 0
        self._layers_loading = False
        self._layers_loaded = False
        self._update_action_state()
        self._update_tree_expansion_action()
        self._show_catalog_retry_overlay(message, detail)
        self._show_warning(message)

    def _show_catalog_retry_overlay(self, message, detail=""):
        if not hasattr(self, "catalog_retry_overlay"):
            return

        self.catalog_retry_title.setText(message)
        self.catalog_retry_detail.setText(detail)
        self.catalog_retry_detail.setVisible(bool(detail))
        self.catalog_retry_overlay.setVisible(True)
        self.catalog_retry_overlay.raise_()

    def _hide_catalog_retry_overlay(self):
        if hasattr(self, "catalog_retry_overlay"):
            self.catalog_retry_overlay.setVisible(False)

    def _log_layers_source(self, candidate):
        QgsMessageLog.logMessage(
            (
                "leaflet_layers.json loaded from "
                f"{candidate['source']} "
                f"(file_version={candidate['file_version']}, "
                f"groups={len(candidate['pages'])})"
            ),
            OLDMAPS_LOG_TAG,
            Qgis.Info,
        )
