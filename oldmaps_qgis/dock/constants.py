# -*- coding: utf-8 -*-
import re
from pathlib import Path

from qgis.core import QgsCoordinateReferenceSystem
from qgis.PyQt.QtCore import QSize, Qt

from ..qt_compat import Qt_UserRole


DATA_ROLE = Qt_UserRole + 1
SEARCH_ROLE = Qt_UserRole + 2
EXPANSION_KEY_ROLE = Qt_UserRole + 3
BASE_TOOLTIP_ROLE = Qt_UserRole + 4
TILE_URL_ROLE = Qt_UserRole + 5
OLDMAPS_LAYER_MIME = "application/x-oldmaps-qgis-layer"
OLDMAPS_HOME = "https://oldmaps.com.ua/"
OLDMAPS_REFERER = "https://oldmaps.com.ua"
OLDMAPS_DATA_SERVER = "https://aksdhgaczyemtajagiwb.supabase.co/storage/v1/object/public/oldmaps/"
OLDMAPS_LOG_TAG = "OldMaps"
NO_DUPLICATES_SETTING_KEY = "oldmaps/no_duplicate_layers"
LOCK_VIEW_SETTING_KEY = "oldmaps/lock_view_on_add"
REMOTE_GPKG_MANIFEST_URL = f"{OLDMAPS_DATA_SERVER}versions/oldmaps.json"
REMOTE_GPKG_URL = f"{OLDMAPS_DATA_SERVER}oldmaps.gpkg"
REMOTE_LAYERS_URL = REMOTE_GPKG_URL
REMOTE_ASSET_TIMEOUT_MS = 15000
TREE_FILTER_BATCH_SIZE = 8
PLUGIN_DIR = Path(__file__).resolve().parent.parent
PLUGIN_METADATA_PATH = PLUGIN_DIR / "metadata.txt"
ICON_PATH = PLUGIN_DIR / "icons" / "oldmaps.svg"
LINK_ICON_PATH = PLUGIN_DIR / "icons" / "Link.svg"

REMOTE_HELP_BANNER_URL = f"{OLDMAPS_DATA_SERVER}help_banner.png"
HELP_BANNER_SIZE = QSize(600, 140)
FLAGS_AND_COATS_DIR = PLUGIN_DIR / "icons" / "flags_and_coats"

REMOTE_PAGE_ICON_FILES_URL = f"{OLDMAPS_DATA_SERVER}page_icon_files.json"
DEFAULT_PAGE_ICON_PATH = FLAGS_AND_COATS_DIR / "map_tools.png"

REMOTE_BORDERS_URL = REMOTE_GPKG_URL
BORDERS_LAYER_NAME = "tile_footprints"
REMOTE_CACHE_DIR_NAME = "oldmaps_qgis"
REMOTE_CACHE_MANIFEST_NAME = "manifest.json"
MAX_ZOOM_RE = re.compile(r"\s*\(max\s+zoom\s*=?\s*[\d-]+\)\s*", re.IGNORECASE)
STATIC_VIEW_RE = re.compile(
    r"\s*(?:[-–]\s*)?\(?\s*статичний\s+(?:вид|перегляд)\s*\)?",
    re.IGNORECASE,
)
WGS84 = QgsCoordinateReferenceSystem("EPSG:4326")
WEB_MERCATOR = QgsCoordinateReferenceSystem("EPSG:3857")
WEB_MERCATOR_INITIAL_RESOLUTION = 156543.03392804097
MIN_TILE_ZOOM = 8
ADD_LAYER_MIN_VIEW_ZOOM = 11
HIDE_ADDED_LAYERS_BELOW_MIN_ZOOM = True
ADDED_LAYER_MIN_VISIBLE_ZOOM = 11
BLOCK_PROXY_TILES_BELOW_MIN_ZOOM = True
PROXY_MIN_TILE_ZOOM = ADDED_LAYER_MIN_VISIBLE_ZOOM
LOAD_SUPABASE_OVERVIEW_TILES = True
PAGE_BUILD_BATCH_SIZE = 1
# Fallback only: borders.gpkg extents are preferred when available.
LAYER_EXTENT_ZOOM_PADDING = 2
LAYER_EXTENT_MIN_WIDTH = 1024
LAYER_EXTENT_MIN_HEIGHT = 768
