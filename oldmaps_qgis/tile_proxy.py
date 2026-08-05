# -*- coding: utf-8 -*-
import base64
import binascii
import collections
import concurrent.futures
import os
import pathlib
import socketserver
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from http.server import BaseHTTPRequestHandler, HTTPServer

from .dock.assets import remote_cache_dir
from .dock.constants import (
    BLOCK_PROXY_TILES_BELOW_MIN_ZOOM,
    LOAD_SUPABASE_OVERVIEW_TILES,
    PROXY_MIN_TILE_ZOOM,
)

REFERER = "https://oldmaps.com.ua"
USER_AGENT = "Mozilla/5.0 (compatible; OldMapsQgisPlugin/0.1; +https://oldmaps.com.ua)"
DEFAULT_PORT = 48731
SUCCESS_CACHE_SECONDS = 86400
FALLBACK_CACHE_SECONDS = 5
MISSING_CACHE_TTL_SECONDS = 120
MAX_REMOTE_WORKERS = 16
MAX_POOL_WORKERS = 32
REMOTE_CONNECT_TIMEOUT = 2
REMOTE_READ_TIMEOUT = 4
REMOTE_WORKER_WAIT_SECONDS = 1.5
RETRY_COUNT = 0
LRU_MAX_ITEMS = 2048
DEBUG_LOG_ENABLED = os.environ.get("OLDMAPS_TILE_PROXY_DEBUG", "").lower() in (
    "1",
    "true",
    "yes",
    "on",
)
LOG_TAG = "OldMaps tile proxy"


def _debug_log(message):
    if not DEBUG_LOG_ENABLED:
        return

    try:
        from qgis.core import Qgis, QgsMessageLog

        QgsMessageLog.logMessage(message, LOG_TAG, Qgis.Info)
    except Exception:
        print(f"{LOG_TAG}: {message}")


def _png_chunk(kind, payload):
    return (
        len(payload).to_bytes(4, "big")
        + kind
        + payload
        + binascii.crc32(kind + payload).to_bytes(4, "big")
    )


def _transparent_png(width=256, height=256):
    signature = b"\x89PNG\r\n\x1a\n"
    ihdr = (
        width.to_bytes(4, "big")
        + height.to_bytes(4, "big")
        + b"\x08\x06\x00\x00\x00"
    )
    raw = b"".join(b"\x00" + (b"\x00" * width * 4) for _ in range(height))
    return signature + _png_chunk(b"IHDR", ihdr) + _png_chunk(b"IDAT", zlib.compress(raw)) + _png_chunk(b"IEND", b"")


TRANSPARENT_TILE = _transparent_png()


def _tile_cache_path(token, z, x, y):
    cache_dir = remote_cache_dir()
    if cache_dir is None:
        return None
    return cache_dir / "tiles" / token / str(z) / str(x) / f"{y}.png"


def _tile_missing_cache_path(token, z, x, y):
    cache_dir = remote_cache_dir()
    if cache_dir is None:
        return None
    return cache_dir / "tiles_missing" / token / str(z) / str(x) / f"{y}.missing"


class _TileLRUCache:
    """Thread-safe in-memory LRU cache for tile content."""

    def __init__(self, max_items=LRU_MAX_ITEMS):
        self._data = collections.OrderedDict()
        self._lock = threading.Lock()
        self._max_items = max_items

    def get(self, key):
        with self._lock:
            value = self._data.get(key)
            if value is not None:
                self._data.move_to_end(key)
            return value

    def put(self, key, value):
        with self._lock:
            if key in self._data:
                self._data.move_to_end(key)
                self._data[key] = value
            else:
                self._data[key] = value
                while len(self._data) > self._max_items:
                    self._data.popitem(last=False)


class OldMapsTileProxy:
    def __init__(self, port=DEFAULT_PORT):
        self.port = port
        self.server = None
        self.thread = None

    @property
    def is_running(self):
        return self.server is not None

    @property
    def base_url(self):
        if self.server is None:
            return None
        host, port = self.server.server_address
        return f"http://{host}:{port}/oldmaps"

    def start(self):
        if self.server is not None:
            return

        server = None
        for port in (self.port, 0):
            try:
                server = _OldMapsProxyServer(("127.0.0.1", port), _OldMapsProxyHandler)
                break
            except OSError:
                if port == 0:
                    raise

        self.server = server
        self.thread = threading.Thread(target=server.serve_forever, name="OldMapsTileProxy", daemon=True)
        self.thread.start()

    def stop(self):
        if self.server is None:
            return

        self.server.shutdown()
        self.server.server_close()
        if self.thread is not None:
            self.thread.join(timeout=2)

        self.server = None
        self.thread = None

    def url_for(self, tile_template, bounds=None):
        if self.server is None:
            self.start()

        token = base64.urlsafe_b64encode(tile_template.encode("utf-8")).decode("ascii").rstrip("=")
        if bounds and self.server is not None:
            self.server.register_bounds(token, bounds)

        return f"{self.base_url}/{token}/{{z}}/{{x}}/{{y}}.png"


class _OldMapsProxyServer(HTTPServer):
    """HTTP server with a fixed thread pool instead of unbounded thread creation."""

    daemon_threads = True
    request_queue_size = 128
    transparent_tile = TRANSPARENT_TILE
    remote_semaphore = threading.BoundedSemaphore(MAX_REMOTE_WORKERS)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._pool = concurrent.futures.ThreadPoolExecutor(max_workers=MAX_POOL_WORKERS)
        self.layer_bounds = {}
        self._bounds_lock = threading.Lock()
        self.tile_lru = _TileLRUCache(LRU_MAX_ITEMS)

    def process_request(self, request, client_address):
        self._pool.submit(self._process_request_thread, request, client_address)

    def _process_request_thread(self, request, client_address):
        try:
            self.finish_request(request, client_address)
        except Exception:
            self.handle_error(request, client_address)
        finally:
            self.shutdown_request(request)

    def server_close(self):
        super().server_close()
        self._pool.shutdown(wait=False)

    def register_bounds(self, token, bounds):
        if not bounds or not isinstance(bounds, dict):
            return

        area_km2 = 0.0
        xmin = bounds.get("xmin")
        ymin = bounds.get("ymin")
        xmax = bounds.get("xmax")
        ymax = bounds.get("ymax")

        if xmin is not None and ymin is not None and xmax is not None and ymax is not None:
            try:
                width_km = abs(float(xmax) - float(xmin)) / 1000.0
                height_km = abs(float(ymax) - float(ymin)) / 1000.0
                area_km2 = width_km * height_km * 0.656
            except (ValueError, TypeError):
                area_km2 = 0.0
        elif bounds.get("tile_count") and bounds.get("zoom"):
            try:
                tc = int(bounds.get("tile_count"))
                z = int(bounds.get("zoom"))
                tile_size_km = (40075.016 / (2 ** z)) * 0.656
                area_km2 = tc * (tile_size_km ** 2)
            except (ValueError, TypeError):
                area_km2 = 0.0

        min_allowed_z = 0
        if area_km2 >= 50000:
            min_allowed_z = 11
        elif area_km2 >= 10000:
            min_allowed_z = 10

        bounds_copy = dict(bounds)
        bounds_copy["min_allowed_z"] = min_allowed_z
        bounds_copy["area_km2"] = area_km2

        with self._bounds_lock:
            self.layer_bounds[token] = bounds_copy

    def get_tile_url(self, token, z, x, y, default_remote_url):
        with self._bounds_lock:
            bounds = self.layer_bounds.get(token)

        if LOAD_SUPABASE_OVERVIEW_TILES and bounds and isinstance(bounds, dict):
            overview_url = bounds.get("overview_tile_url")
            overview_max_z = bounds.get("overview_max_zoom")
            try:
                z_val = int(z)
                if overview_url and overview_max_z is not None and z_val <= int(overview_max_z):
                    return overview_url.replace("{z}", str(z)).replace("{x}", str(x)).replace("{y}", str(y))
            except Exception:
                pass

        return default_remote_url

    def is_zoom_blocked(self, token, z):
        try:
            z_val = int(z)
        except (TypeError, ValueError):
            return True

        min_zoom = None
        if BLOCK_PROXY_TILES_BELOW_MIN_ZOOM:
            min_zoom = PROXY_MIN_TILE_ZOOM

        with self._bounds_lock:
            bounds = self.layer_bounds.get(token)

        if bounds and isinstance(bounds, dict):
            try:
                bounds_min_zoom = int(bounds.get("min_allowed_z") or 0)
                if bounds_min_zoom > 0:
                    min_zoom = max(min_zoom or 0, bounds_min_zoom)
            except (TypeError, ValueError):
                pass

        return min_zoom is not None and z_val < min_zoom

    def is_tile_in_bounds(self, token, z, x, y):
        with self._bounds_lock:
            bounds = self.layer_bounds.get(token)

        if not bounds or not isinstance(bounds, dict):
            return True

        try:
            ref_z = int(bounds.get("zoom"))
            tr = bounds.get("tile_range")
            if not tr or not isinstance(tr, dict):
                return True

            ref_x_min, ref_x_max = int(tr["x_min"]), int(tr["x_max"])
            ref_y_min, ref_y_max = int(tr["y_min"]), int(tr["y_max"])
            z_val = int(z)
            x_val = int(x)
            y_val = int(y)

            if z_val <= ref_z:
                shift = ref_z - z_val
                x_min_z = ref_x_min >> shift
                x_max_z = ref_x_max >> shift
                y_min_z = ref_y_min >> shift
                y_max_z = ref_y_max >> shift
            else:
                shift = z_val - ref_z
                x_min_z = ref_x_min << shift
                x_max_z = (ref_x_max << shift) | ((1 << shift) - 1)
                y_min_z = ref_y_min << shift
                y_max_z = (ref_y_max << shift) | ((1 << shift) - 1)

            return (x_min_z <= x_val <= x_max_z) and (y_min_z <= y_val <= y_max_z)
        except Exception:
            return True


class _OldMapsProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):
        remote_info = self._parse_tile_path()
        if not remote_info:
            self._send_empty_tile("invalid proxy request")
            return

        token, z, x, y, remote_url = remote_info
        lru_key = (token, z, x, y)

        # 1. Zoom guard: avoid slow lower-zoom remote/proxy requests.
        if self.server.is_zoom_blocked(token, z):
            self._send_empty_tile("below proxy min zoom", remote_url)
            return

        # 2. Bounding-box filtering
        if not self.server.is_tile_in_bounds(token, z, x, y):
            self._send_empty_tile("out of bounds", remote_url)
            return

        # 3. In-memory LRU cache (fastest path, ~0.01 ms)
        lru_content = self.server.tile_lru.get(lru_key)
        if lru_content is not None:
            _debug_log(f"LRU HIT {remote_url} ({len(lru_content)} bytes)")
            self._send_tile(lru_content, SUCCESS_CACHE_SECONDS)
            return

        # 4. Local disk tile cache (fast path, ~1-5 ms)
        cache_file = _tile_cache_path(token, z, x, y)
        if cache_file and cache_file.exists() and cache_file.stat().st_size > 0:
            try:
                content = cache_file.read_bytes()
                self.server.tile_lru.put(lru_key, content)
                _debug_log(f"CACHE HIT {remote_url} ({len(content)} bytes)")
                self._send_tile(content, SUCCESS_CACHE_SECONDS)
                return
            except OSError:
                pass

        # 5. Negative caching check (with 5-minute TTL expiration)
        missing_file = _tile_missing_cache_path(token, z, x, y)
        if missing_file and missing_file.exists():
            try:
                st = missing_file.stat()
                if (time.time() - st.st_mtime) < MISSING_CACHE_TTL_SECONDS:
                    self._send_empty_tile("cached missing", remote_url)
                    return
                else:
                    missing_file.unlink(missing_ok=True)
            except OSError:
                pass

        # 6. Remote fetch (optionally uses Supabase overview_tile_url for lower zooms)
        fetch_url = self.server.get_tile_url(token, z, x, y, remote_url)

        request = urllib.request.Request(
            fetch_url,
            headers={
                "Referer": REFERER,
                "User-Agent": USER_AGENT,
                "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
            },
        )

        if not self.server.remote_semaphore.acquire(timeout=REMOTE_WORKER_WAIT_SECONDS):
            self._send_temporary_error("proxy busy", remote_url)
            return

        try:
            content, content_type, status = self._fetch_remote_tile(request, fetch_url)
        except urllib.error.HTTPError as exc:
            if exc.code == 404 and missing_file:
                try:
                    missing_file.parent.mkdir(parents=True, exist_ok=True)
                    missing_file.touch()
                except OSError:
                    pass
                self._send_empty_tile(f"HTTP 404", remote_url)
            else:
                self._send_temporary_error(f"HTTP {exc.code}", remote_url)
            return
        except Exception as exc:
            self._send_temporary_error(f"{type(exc).__name__}: {exc}", remote_url)
            return
        finally:
            self.server.remote_semaphore.release()

        # Save to disk cache
        if cache_file and content:
            try:
                cache_file.parent.mkdir(parents=True, exist_ok=True)
                temp_file = cache_file.with_name(f"{cache_file.name}.tmp")
                temp_file.write_bytes(content)
                temp_file.replace(cache_file)
            except OSError:
                pass

        # Save to LRU cache
        if content:
            self.server.tile_lru.put(lru_key, content)

        _debug_log(f"OK HTTP {status} {remote_url} ({len(content)} bytes, {content_type})")
        self._send_tile(content, SUCCESS_CACHE_SECONDS, content_type)

    def _fetch_remote_tile(self, request, remote_url):
        timeout = REMOTE_CONNECT_TIMEOUT + REMOTE_READ_TIMEOUT
        last_error = None
        for attempt in range(RETRY_COUNT + 1):
            try:
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    content = response.read()
                    content_type = response.headers.get("Content-Type") or "image/png"
                    status = getattr(response, "status", 200)
                    return content, content_type, status
            except urllib.error.HTTPError:
                raise
            except Exception as exc:
                last_error = exc
                _debug_log(
                    "REMOTE retry "
                    f"{attempt + 1}/{RETRY_COUNT + 1} failed: "
                    f"{type(exc).__name__}: {exc}: {remote_url}"
                )

        raise last_error or RuntimeError("remote tile request failed")

    def _parse_tile_path(self):
        parsed = urllib.parse.urlsplit(self.path)
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) != 5 or parts[0] != "oldmaps":
            return None

        token, z, x, y_file = parts[1], parts[2], parts[3], parts[4]
        y = y_file.split(".", 1)[0]
        try:
            padding = "=" * (-len(token) % 4)
            template = base64.urlsafe_b64decode((token + padding).encode("ascii")).decode("utf-8")
        except Exception:
            return None

        remote_url = template.replace("{z}", z).replace("{x}", x).replace("{y}", y)
        return token, z, x, y, remote_url

    def _send_tile(self, content, max_age, content_type="image/png"):
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", f"public, max-age={max_age}")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def _send_empty_tile(self, reason=None, remote_url=None):
        if reason:
            _debug_log(f"EMPTY {reason}: {remote_url or self.path}")

        self._send_tile(self.server.transparent_tile, FALLBACK_CACHE_SECONDS)

    def _send_temporary_error(self, reason=None, remote_url=None):
        if reason:
            _debug_log(f"TEMP ERROR {reason}: {remote_url or self.path}")

        content = self.server.transparent_tile
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, format, *args):
        return
