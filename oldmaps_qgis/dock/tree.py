# -*- coding: utf-8 -*-
import json

from qgis.PyQt.QtCore import (
    QAbstractItemModel,
    QByteArray,
    QMimeData,
    QModelIndex,
    QSortFilterProxyModel,
    QTimer,
    Qt,
    pyqtSignal,
)
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAbstractItemView, QMenu, QStyle, QTreeView

from ..qt_compat import (
    Qt_AscendingOrder,
    Qt_CopyAction,
    Qt_DecorationRole,
    Qt_DisplayRole,
    Qt_Horizontal,
    Qt_ItemIsDragEnabled,
    Qt_ItemIsEnabled,
    Qt_ItemIsSelectable,
    Qt_NoItemFlags,
    Qt_ToolTipRole,
    get_enum,
    safe_exec,
)

from .constants import (
    BASE_TOOLTIP_ROLE,
    DATA_ROLE,
    EXPANSION_KEY_ROLE,
    ICON_PATH,
    LOCK_VIEW_SETTING_KEY,
    NO_DUPLICATES_SETTING_KEY,
    OLDMAPS_LAYER_MIME,
    PAGE_BUILD_BATCH_SIZE,
    SEARCH_ROLE,
    TILE_URL_ROLE,
)


SEARCH_AUTO_EXPAND_MATCH_LIMIT = 200


class OldMapsTreeNode:
    def __init__(
        self,
        kind,
        texts=None,
        parent=None,
        payload=None,
        search_text="",
        expansion_key="",
        base_tooltip="",
        tile_url="",
    ):
        self.kind = kind
        self.texts = texts or ["", ""]
        self._parent = parent
        self.children = []
        self.payload = payload or {}
        self.search_text = search_text
        self.expansion_key = expansion_key
        self.base_tooltip = base_tooltip
        self.tile_url = tile_url

    def append_child(self, child):
        child._parent = self
        self.children.append(child)

    def parent(self):
        return self._parent if self._parent is not None and self._parent.kind != "root" else None

    def child(self, row):
        if 0 <= row < len(self.children):
            return self.children[row]
        return None

    def childCount(self):
        return len(self.children)

    def row(self):
        if self._parent is None:
            return 0
        return self._parent.children.index(self)

    def text(self, column):
        if 0 <= column < len(self.texts):
            return self.texts[column]
        return ""

    def data(self, column, role):
        if role == DATA_ROLE:
            return self.payload
        if role == SEARCH_ROLE:
            return self.search_text
        if role == EXPANSION_KEY_ROLE:
            return self.expansion_key
        if role == BASE_TOOLTIP_ROLE:
            return self.base_tooltip
        if role == TILE_URL_ROLE:
            return self.tile_url
        if role == Qt_DisplayRole:
            return self.text(column)
        return None


class OldMapsLayerTreeModel(QAbstractItemModel):
    def __init__(self, dock_widget, parent=None):
        super().__init__(parent)
        self.dock_widget = dock_widget
        self.root = OldMapsTreeNode("root")
        self.headers = ["Шар", "max zoom"]
        self.existing_tile_urls = set()
        self.raster_icon = QIcon()
        self.page_match_counts = None

    def clear(self):
        self.beginResetModel()
        self.root.children = []
        self.page_match_counts = None
        self.endResetModel()

    def set_header_labels(self, labels):
        self.headers = list(labels)
        self.headerDataChanged.emit(Qt_Horizontal, 0, max(0, len(self.headers) - 1))

    def append_page(self, page_node):
        row = len(self.root.children)
        self.beginInsertRows(QModelIndex(), row, row)
        self.root.append_child(page_node)
        self.endInsertRows()

    def set_project_tile_urls(self, tile_urls, raster_icon):
        self.existing_tile_urls = set(tile_urls or [])
        self.raster_icon = raster_icon or QIcon()
        self._emit_all_data_changed()

    def set_page_match_counts(self, counts):
        self.page_match_counts = counts
        if not self.root.children:
            return

        top_left = self.index(0, 0, QModelIndex())
        bottom_right = self.index(len(self.root.children) - 1, 0, QModelIndex())
        self.dataChanged.emit(top_left, bottom_right, [Qt_DisplayRole])

    def _emit_all_data_changed(self):
        if not self.root.children:
            return

        roles = [Qt_DecorationRole, Qt_ToolTipRole]
        top_left = self.index(0, 0, QModelIndex())
        bottom_right = self.index(len(self.root.children) - 1, 1, QModelIndex())
        self.dataChanged.emit(top_left, bottom_right, roles)
        for page_node in self.root.children:
            if not page_node.children:
                continue
            parent_index = self.index_for_node(page_node)
            child_top_left = self.index(0, 0, parent_index)
            child_bottom_right = self.index(len(page_node.children) - 1, 1, parent_index)
            self.dataChanged.emit(child_top_left, child_bottom_right, roles)

    def index(self, row, column, parent=QModelIndex()):
        if not self.hasIndex(row, column, parent):
            return QModelIndex()

        parent_node = self.node_from_index(parent)
        child_node = parent_node.child(row)
        if child_node is None:
            return QModelIndex()
        return self.createIndex(row, column, child_node)

    def parent(self, index):
        if not index.isValid():
            return QModelIndex()

        node = index.internalPointer()
        parent_node = node._parent if node is not None else None
        if parent_node is None or parent_node is self.root:
            return QModelIndex()
        return self.createIndex(parent_node.row(), 0, parent_node)

    def rowCount(self, parent=QModelIndex()):
        if parent.isValid() and parent.column() > 0:
            return 0

        parent_node = self.node_from_index(parent)
        return len(parent_node.children)

    def columnCount(self, parent=QModelIndex()):
        return 2

    def data(self, index, role=Qt_DisplayRole):
        if not index.isValid():
            return None

        node = index.internalPointer()
        column = index.column()
        if role == Qt_DisplayRole:
            if (
                node.kind == "page"
                and column == 0
                and self.page_match_counts is not None
            ):
                match_count = self.page_match_counts.get(node)
                if match_count is not None:
                    return f"{node.text(column)} ({match_count})"
            return node.text(column)
        if role == Qt_DecorationRole and column == 0:
            if node.kind == "page":
                return self.dock_widget._page_icon(node.text(0))
            if node.kind == "layer" and node.tile_url in self.existing_tile_urls:
                return self.raster_icon
            return QIcon()
        if role == Qt_ToolTipRole:
            if node.kind == "layer" and node.tile_url in self.existing_tile_urls:
                return f"Шар уже додано в проект\n{node.text(0)}"
            return node.base_tooltip or node.text(0)
        return node.data(column, role)

    def headerData(self, section, orientation, role=Qt_DisplayRole):
        if orientation == Qt_Horizontal and role == Qt_DisplayRole:
            if 0 <= section < len(self.headers):
                return self.headers[section]
        return None

    def flags(self, index):
        if not index.isValid():
            return Qt_NoItemFlags

        flags = Qt_ItemIsEnabled | Qt_ItemIsSelectable
        node = index.internalPointer()
        if node is not None and node.kind == "layer":
            flags |= Qt_ItemIsDragEnabled
        return flags

    def mimeTypes(self):
        return [OLDMAPS_LAYER_MIME, "text/plain"]

    def mimeData(self, indexes):
        node = None
        for index in indexes:
            if not index.isValid():
                continue
            candidate = index.internalPointer()
            if candidate is not None and candidate.kind == "layer":
                node = candidate
                break

        if node is None:
            return QMimeData()

        payload = node.payload if isinstance(node.payload, dict) else {}
        page = payload.get("page") if isinstance(payload.get("page"), dict) else {}
        layer = payload.get("layer") if isinstance(payload.get("layer"), dict) else {}
        drag_payload = {
            "type": "layer",
            "page": {key: value for key, value in page.items() if key != "layers"},
            "layer": layer,
        }

        mime_data = QMimeData()
        mime_data.setData(
            OLDMAPS_LAYER_MIME,
            QByteArray(json.dumps(drag_payload, ensure_ascii=False).encode("utf-8")),
        )
        mime_data.setText(node.text(0))
        return mime_data

    def supportedDragActions(self):
        return Qt_CopyAction

    def node_from_index(self, index):
        if index.isValid():
            node = index.internalPointer()
            if node is not None:
                return node
        return self.root

    def index_for_node(self, node, column=0):
        if node is None or node is self.root:
            return QModelIndex()
        return self.createIndex(node.row(), column, node)


class OldMapsTreeFilterProxyModel(QSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.query = ""
        self.coverage_urls = None
        self.setDynamicSortFilter(True)

    def set_filter_state(self, query="", coverage_urls=None):
        self.query = (query or "").casefold().strip()
        self.coverage_urls = set(coverage_urls) if coverage_urls is not None else None
        self.refresh_sorting()

    def refresh_sorting(self):
        self.invalidate()
        self.sort(0, Qt_AscendingOrder)

    def has_filter(self):
        return bool(self.query) or self.coverage_urls is not None

    def mimeData(self, indexes):
        source_indexes = [
            self.mapToSource(index)
            for index in indexes
            if index.isValid()
        ]
        return self.sourceModel().mimeData(source_indexes)

    def filterAcceptsRow(self, source_row, source_parent):
        if not self.has_filter():
            return True

        model = self.sourceModel()
        source_index = model.index(source_row, 0, source_parent)
        node = model.node_from_index(source_index)
        if node.kind == "page":
            return self._page_matches(node)
        if node.kind == "layer":
            parent_match = self._parent_matches(node)
            return self._layer_matches(node, parent_match)
        return True

    def _page_matches(self, page_node):
        parent_match = bool(self.query) and self.query in page_node.search_text
        child_match_found = any(
            self._layer_matches(child, parent_match)
            for child in page_node.children
        )
        return child_match_found or (parent_match and self.coverage_urls is None)

    def _layer_matches(self, node, parent_match=False):
        search_match = (
            not self.query
            or parent_match
            or self.query in node.search_text
        )
        coverage_match = (
            self.coverage_urls is None
            or bool(node.tile_url and node.tile_url in self.coverage_urls)
        )
        return search_match and coverage_match

    def _parent_matches(self, node):
        parent = node.parent()
        return bool(
            parent is not None
            and self.query
            and self.query in parent.search_text
        )

    def lessThan(self, left, right):
        model = self.sourceModel()
        left_node = model.node_from_index(left)
        right_node = model.node_from_index(right)

        if (
            self.has_filter()
            and left_node.kind == "page"
            and right_node.kind == "page"
        ):
            counts = getattr(model, "page_match_counts", None) or {}
            left_count = counts.get(left_node, 0)
            right_count = counts.get(right_node, 0)
            if left_count != right_count:
                return left_count > right_count

        return left.row() < right.row()


class OldMapsLayerTreeView(QTreeView):
    currentItemChanged = pyqtSignal(object, object)
    itemCollapsed = pyqtSignal(object)
    itemExpanded = pyqtSignal(object)
    itemDoubleClicked = pyqtSignal(object, int)
    itemEntered = pyqtSignal(object, int)

    def __init__(self, dock_widget, parent=None):
        super().__init__(parent)
        self.dock_widget = dock_widget
        self._source_model = OldMapsLayerTreeModel(dock_widget, self)
        self._filter_model = OldMapsTreeFilterProxyModel(self)
        self._filter_model.setSourceModel(self._source_model)
        self.setModel(self._filter_model)
        self.expanded.connect(self._emit_item_expanded)
        self.collapsed.connect(self._emit_item_collapsed)
        self.doubleClicked.connect(self._emit_item_double_clicked)
        self.entered.connect(self._emit_item_entered)

    def leaveEvent(self, event):
        self.dock_widget._clear_coverage_highlight()
        super().leaveEvent(event)

    def currentChanged(self, current, previous):
        super().currentChanged(current, previous)
        self.currentItemChanged.emit(
            self._node_from_proxy_index(current),
            self._node_from_proxy_index(previous),
        )

    def setColumnCount(self, count):
        return None

    def setHeaderLabels(self, labels):
        self._source_model.set_header_labels(labels)

    def clear(self):
        self._source_model.clear()
        self._filter_model.set_filter_state("", None)

    def addTopLevelItem(self, item):
        self._source_model.append_page(item)

    def topLevelItemCount(self):
        return len(self._source_model.root.children)

    def topLevelItem(self, row):
        return self._source_model.root.child(row)

    def currentItem(self):
        return self._node_from_proxy_index(self.currentIndex())

    def setCurrentItem(self, item):
        proxy_index = self._proxy_index_for_node(item)
        if proxy_index.isValid():
            self.setCurrentIndex(proxy_index)

    def itemAt(self, position):
        return self._node_from_proxy_index(self.indexAt(position))

    def scrollToItem(self, item, hint=None):
        if hint is None:
            hint = get_enum(QAbstractItemView, "EnsureVisible", "ScrollHint")
        proxy_index = self._proxy_index_for_node(item)
        if proxy_index.isValid():
            self.scrollTo(proxy_index, hint)

    def set_filter_state(self, query="", coverage_urls=None):
        self._filter_model.set_filter_state(query, coverage_urls)

    def has_filter(self):
        return self._filter_model.has_filter()

    def visibleTopLevelItems(self):
        return [
            item
            for item in self._source_model.root.children
            if self._proxy_index_for_node(item).isValid()
        ]

    def layerTileUrlsForFilter(self, query="", coverage_urls=None):
        query = (query or "").casefold().strip()
        coverage_urls = set(coverage_urls) if coverage_urls is not None else None
        tile_urls = set()

        for page_node in self._source_model.root.children:
            parent_match = bool(query) and query in page_node.search_text
            for child in page_node.children:
                search_match = (
                    not query
                    or parent_match
                    or query in child.search_text
                )
                coverage_match = (
                    coverage_urls is None
                    or bool(child.tile_url and child.tile_url in coverage_urls)
                )
                if search_match and coverage_match and child.tile_url:
                    tile_urls.add(child.tile_url)

        return tile_urls

    def layerMatchCountForFilter(self, query="", coverage_urls=None):
        counts, match_count = self.pageMatchCountsForFilter(query, coverage_urls)
        return match_count

    def pageMatchCountsForFilter(self, query="", coverage_urls=None):
        query = (query or "").casefold().strip()
        coverage_urls = set(coverage_urls) if coverage_urls is not None else None
        counts = {}
        match_count = 0

        for page_node in self._source_model.root.children:
            parent_match = bool(query) and query in page_node.search_text
            page_match_count = 0
            for child in page_node.children:
                search_match = (
                    not query
                    or parent_match
                    or query in child.search_text
                )
                coverage_match = (
                    coverage_urls is None
                    or bool(child.tile_url and child.tile_url in coverage_urls)
                )
                if search_match and coverage_match:
                    page_match_count += 1
                    match_count += 1

            if page_match_count:
                counts[page_node] = page_match_count

        return counts, match_count

    def setPageMatchCounts(self, counts):
        self._source_model.set_page_match_counts(counts)
        self._filter_model.refresh_sorting()

    def isItemExpanded(self, item):
        proxy_index = self._proxy_index_for_node(item)
        return proxy_index.isValid() and self.isExpanded(proxy_index)

    def setItemExpanded(self, item, expanded):
        proxy_index = self._proxy_index_for_node(item)
        if proxy_index.isValid():
            self.setExpanded(proxy_index, expanded)

    def expandVisibleTopLevelItems(self):
        for item in self.visibleTopLevelItems():
            self.setItemExpanded(item, True)

    def collapseVisibleTopLevelItems(self):
        for item in self.visibleTopLevelItems():
            self.setItemExpanded(item, False)

    def isItemEffectivelyVisible(self, item):
        proxy_index = self._proxy_index_for_node(item)
        if not proxy_index.isValid():
            return False

        parent = proxy_index.parent()
        while parent.isValid():
            if not self.isExpanded(parent):
                return False
            parent = parent.parent()
        return True

    def refreshAppearance(self, existing_tile_urls, raster_icon):
        self._source_model.set_project_tile_urls(existing_tile_urls, raster_icon)

    def _emit_item_expanded(self, index):
        self.itemExpanded.emit(self._node_from_proxy_index(index))

    def _emit_item_collapsed(self, index):
        self.itemCollapsed.emit(self._node_from_proxy_index(index))

    def _emit_item_double_clicked(self, index):
        self.itemDoubleClicked.emit(self._node_from_proxy_index(index), index.column())

    def _emit_item_entered(self, index):
        self.itemEntered.emit(self._node_from_proxy_index(index), index.column())

    def _node_from_proxy_index(self, proxy_index):
        if not proxy_index.isValid():
            return None

        source_index = self._filter_model.mapToSource(proxy_index)
        node = self._source_model.node_from_index(source_index)
        return None if node is self._source_model.root else node

    def _proxy_index_for_node(self, node, column=0):
        source_index = self._source_model.index_for_node(node, column)
        if not source_index.isValid():
            return QModelIndex()
        return self._filter_model.mapFromSource(source_index)


class TreeMixin:
    def _build_next_page_batch(self, generation):
        if generation != self._layers_load_generation:
            return

        built_count = 0
        while (
            built_count < PAGE_BUILD_BATCH_SIZE
            and self._pending_page_index < len(self._pending_pages)
        ):
            page = self._pending_pages[self._pending_page_index]
            self._pending_page_index += 1
            built_count += 1
            if isinstance(page, dict):
                self._add_page_tree_item(page)

        if self._pending_page_index < len(self._pending_pages):
            QTimer.singleShot(
                0,
                lambda generation=generation: self._build_next_page_batch(generation),
            )
            return

        self._finish_tree_build(generation)

    def _add_page_tree_item(self, page):
        page_name = str(page.get("name") or "Без назви")
        page_item = OldMapsTreeNode(
            "page",
            [page_name, ""],
            payload={"type": "page", "page": page},
            search_text=page_name.casefold(),
            expansion_key=page.get("page_url") or page_name,
            base_tooltip=page_name,
        )

        layers = page.get("layers") or []
        for layer in layers:
            if not isinstance(layer, dict):
                continue
            tile_url = str(layer.get("tile_url") or "").strip()
            if not tile_url:
                continue

            layer_name = self._display_layer_name(layer)
            layer_tooltip = (
                "Подвійний клік або перетягніть на карту, "
                "щоб додати шар.\n"
                f"{layer_name}"
            )
            layer_item = OldMapsTreeNode(
                "layer",
                [layer_name, str(self._layer_max_zoom(layer))],
                parent=page_item,
                payload={"type": "layer", "page": page, "layer": layer},
                search_text=self._layer_search_text(layer, layer_name),
                base_tooltip=layer_tooltip,
                tile_url=tile_url,
            )
            page_item.append_child(layer_item)
            self._count_unique_layer(layer)

        self.tree.addTopLevelItem(page_item)

    def _count_unique_layer(self, layer):
        tile_url = str(layer.get("tile_url") or "").strip()
        if not tile_url or tile_url in self._counted_layer_urls:
            return

        self._counted_layer_urls.add(tile_url)
        self.layer_count += 1

    def _finish_tree_build(self, generation):
        if generation != self._layers_load_generation:
            return

        self._pending_pages = []
        self._pending_page_index = 0

        if self._project_oldmaps_tile_urls():
            self._refresh_layer_icons()
        self._apply_search_filter_now(self.search_edit.text())
        self._layers_loading = False
        self._layers_loaded = True
        self._update_action_state()
        self._update_tree_expansion_action()
        self.update_zoom_label()

    def _on_current_item_changed(self, current, previous):
        self._update_action_state()
        self.update_zoom_label()

    def _on_tree_item_expansion_changed(self, item):
        self._update_action_state()
        self._update_tree_expansion_action()
        self.update_zoom_label()

    def _on_item_double_clicked(self, item, column):
        payload = item.data(0, DATA_ROLE) if item is not None else {}
        if payload.get("type") == "layer":
            self.add_selected_layer()

    def _show_tree_context_menu(self, position):
        item = self.tree.itemAt(position)
        payload = item.data(0, DATA_ROLE) if item is not None else {}
        if not isinstance(payload, dict) or payload.get("type") != "layer":
            return

        if self.tree.currentItem() is not item:
            self.tree.setCurrentItem(item)
        if self._selected_layer_record() is None:
            return

        menu = QMenu(self.tree)
        add_action = menu.addAction(self.add_action.icon(), "Додати шар на карту")
        add_action.setEnabled(self.add_action.isEnabled())
        add_action.triggered.connect(self.add_selected_layer)

        info_action = menu.addAction(self.info_action.icon(), "Атрибуція")
        info_action.setEnabled(self.info_action.isEnabled())
        info_action.triggered.connect(self.show_selected_attribution)

        zoom_action = menu.addAction(
            self.zoom_to_selection_action.icon(),
            "Збільшити до шару",
        )
        zoom_action.setEnabled(self.zoom_to_selection_action.isEnabled())
        zoom_action.triggered.connect(self.zoom_to_selected_item)

        safe_exec(menu, self.tree.viewport().mapToGlobal(position))

    def _toggle_search(self, checked):
        self.search_edit.setVisible(checked)
        if checked:
            self.search_edit.setFocus()
            self.search_edit.selectAll()
            if self.search_edit.text().strip():
                self._schedule_search_filter(self.search_edit.text())
            return

        self.search_timer.stop()
        self._pending_search_text = ""
        self.search_edit.blockSignals(True)
        self.search_edit.clear()
        self.search_edit.blockSignals(False)
        self._apply_tree_filters(reveal_current=True)

    def _schedule_search_filter(self, text):
        self._pending_search_text = text or ""
        self.search_timer.start()

    def _apply_pending_search_filter(self):
        self._apply_search_filter_now(self._pending_search_text)

    def _apply_search_filter_now(self, text):
        self._pending_search_text = text or ""
        self._apply_tree_filters(reveal_current=True)

    def _apply_tree_filters(self, reveal_current=False):
        query = (self.search_edit.text() or "").casefold().strip()
        coverage_urls = self._coverage_filter_tile_urls
        if coverage_urls is not None:
            coverage_urls = set(coverage_urls)
        has_filter = bool(query) or coverage_urls is not None

        if not has_filter:
            self.tree.set_filter_state("", None)
            self.tree.setPageMatchCounts(None)
            self._refresh_all_coverage_geometries_for_search()
            if self._search_filter_active:
                self._restore_expansion_state(self._saved_expansion_state)
                self._search_filter_active = False
                self._saved_expansion_state = None
                if reveal_current:
                    self._reveal_current_item()
            self._update_action_state()
            self._update_tree_expansion_action()
            return

        if not self._search_filter_active:
            self._saved_expansion_state = self._current_expansion_state()
            self._search_filter_active = True

        self._clear_coverage_highlight()
        page_match_counts, match_count = self.tree.pageMatchCountsForFilter(
            query,
            coverage_urls,
        )
        self.tree.setPageMatchCounts(page_match_counts)
        self.tree.set_filter_state(query, coverage_urls)
        self._refresh_all_coverage_geometries_for_search()
        if match_count <= SEARCH_AUTO_EXPAND_MATCH_LIMIT:
            self.tree.expandVisibleTopLevelItems()
        else:
            self.tree.collapseVisibleTopLevelItems()
        if reveal_current:
            self._reveal_current_item()
        self._update_action_state()
        self._update_tree_expansion_action()

    def _cancel_tree_filter(self):
        self._filter_generation += 1
        self._filter_pending_state = None
        self._filter_page_index = 0
        if hasattr(self, "tree"):
            self.tree.set_filter_state("", None)
            self.tree.setPageMatchCounts(None)

    def _coverage_item_matches(self, item, coverage_urls):
        if coverage_urls is None:
            return True

        tile_url = item.data(0, TILE_URL_ROLE)
        return bool(tile_url and tile_url in coverage_urls)

    def _reset_tree_filter(self, reveal_current=False):
        self._coverage_filter_tile_urls = None
        self._apply_tree_filters(reveal_current=reveal_current)

    def toggle_tree_expansion(self):
        visible_pages = self._visible_page_items()
        if not visible_pages:
            return

        should_expand = any(
            not self.tree.isItemExpanded(item)
            for item in visible_pages
        )
        for item in visible_pages:
            self.tree.setItemExpanded(item, should_expand)

        self._update_action_state()
        self._update_tree_expansion_action()
        self.update_zoom_label()

    def _visible_page_items(self):
        return self.tree.visibleTopLevelItems()

    def _update_tree_expansion_action(self):
        if not hasattr(self, "tree_expansion_action"):
            return

        visible_pages = self._visible_page_items()
        if not visible_pages:
            self.tree_expansion_action.setEnabled(False)
            self.tree_expansion_action.setIcon(self.expand_tree_icon)
            self.tree_expansion_action.setToolTip("Розгорнути все")
            return

        self.tree_expansion_action.setEnabled(True)
        if any(not self.tree.isItemExpanded(item) for item in visible_pages):
            self.tree_expansion_action.setIcon(self.expand_tree_icon)
            self.tree_expansion_action.setToolTip("Розгорнути все")
        else:
            self.tree_expansion_action.setIcon(self.collapse_tree_icon)
            self.tree_expansion_action.setToolTip("Згорнути все")

    def _current_expansion_state(self):
        state = {}
        for index in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(index)
            key = item.data(0, EXPANSION_KEY_ROLE)
            if key:
                state[key] = self.tree.isItemExpanded(item)
        return state

    def _restore_expansion_state(self, state):
        if not state:
            return

        for index in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(index)
            key = item.data(0, EXPANSION_KEY_ROLE)
            if key in state:
                self.tree.setItemExpanded(item, state[key])

    def _reveal_current_item(self):
        item = self.tree.currentItem()
        if item is None:
            return

        parent = item.parent()
        if parent is not None:
            self.tree.setItemExpanded(parent, True)
        self.tree.scrollToItem(
            item,
            get_enum(QAbstractItemView, "PositionAtCenter", "ScrollHint"),
        )

    def _selected_layer_record(self):
        payload = self._selected_record()
        if payload is None or payload.get("type") != "layer":
            return None
        return payload

    def _selected_record(self):
        item = self.tree.currentItem()
        if item is None:
            return None
        if not self._is_tree_item_effectively_visible(item):
            return None
        payload = item.data(0, DATA_ROLE) or {}
        if payload.get("type") not in ("page", "layer"):
            return None
        return payload

    def _is_tree_item_effectively_visible(self, item):
        return self.tree.isItemEffectivelyVisible(item)

    def _update_action_state(self):
        has_record = self._selected_record() is not None
        has_layer = self._selected_layer_record() is not None
        self.add_action.setEnabled(has_layer)
        self.info_action.setEnabled(has_layer)
        self.zoom_to_selection_action.setEnabled(has_record)
        self._update_no_duplicates_tooltip(
            self.no_duplicates_action.isChecked()
        )
        if hasattr(self, "lock_view_action"):
            self._update_lock_view_tooltip(
                self.lock_view_action.isChecked()
            )

    def _settings_bool(self, key, default):
        value = self.settings.value(key, default, type=bool)
        if isinstance(value, str):
            return value.lower() in ("1", "true", "yes", "on")
        return bool(value)

    def _save_no_duplicates_setting(self, checked):
        self.settings.setValue(NO_DUPLICATES_SETTING_KEY, bool(checked))

    def _update_no_duplicates_tooltip(self, checked):
        state = "Увімк." if checked else "Вимк."
        self.no_duplicates_action.setToolTip(
            f"Не додавати шари повторно: {state}"
        )

    def _save_lock_view_setting(self, checked):
        self.settings.setValue(LOCK_VIEW_SETTING_KEY, bool(checked))

    def _update_lock_view_tooltip(self, checked):
        state = "Увімк." if checked else "Вимк."
        if hasattr(self, "lock_view_action"):
            self.lock_view_action.setToolTip(
                f"Заблокувати вид при додаванні шару на карту: {state}"
            )
