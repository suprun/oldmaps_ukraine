# -*- coding: utf-8 -*-
"""
Qt5 / Qt6 compatibility helpers for QGIS 3.x and QGIS 4.x
"""
from qgis.PyQt.QtCore import Qt

try:
    from qgis.PyQt.QtWidgets import QAction
except ImportError:
    from qgis.PyQt.QtGui import QAction


def safe_exec(obj, *args, **kwargs):
    """
    Safely execute a dialog or menu in both Qt5 (exec_ / exec) and Qt6 (exec).
    """
    func = getattr(obj, "exec", None) or getattr(obj, "exec_")
    return func(*args, **kwargs)


def get_enum(container, name, *sub_enum_names):
    """
    Safely retrieve an enum value across Qt5 (where it was at top-level container)
    and Qt6 (where it is scoped inside a sub-enum class).
    """
    if hasattr(container, name):
        return getattr(container, name)
    for sub_name in sub_enum_names:
        sub = getattr(container, sub_name, None)
        if sub is not None and hasattr(sub, name):
            return getattr(sub, name)
    return None


# Pre-resolved Qt Core/Gui/Widgets enums
Qt_LeftButton = get_enum(Qt, "LeftButton", "MouseButton")
Qt_RightButton = get_enum(Qt, "RightButton", "MouseButton")
Qt_Horizontal = get_enum(Qt, "Horizontal", "Orientation")
Qt_Vertical = get_enum(Qt, "Vertical", "Orientation")
Qt_DisplayRole = get_enum(Qt, "DisplayRole", "ItemDataRole")
Qt_DecorationRole = get_enum(Qt, "DecorationRole", "ItemDataRole")
Qt_ToolTipRole = get_enum(Qt, "ToolTipRole", "ItemDataRole")
Qt_UserRole = get_enum(Qt, "UserRole", "ItemDataRole")
Qt_AlignCenter = get_enum(Qt, "AlignCenter", "AlignmentFlag")
Qt_AlignHCenter = get_enum(Qt, "AlignHCenter", "AlignmentFlag")
Qt_CrossCursor = get_enum(Qt, "CrossCursor", "CursorShape")
Qt_ArrowCursor = get_enum(Qt, "ArrowCursor", "CursorShape")
Qt_WaitCursor = get_enum(Qt, "WaitCursor", "CursorShape")
Qt_NoFocus = get_enum(Qt, "NoFocus", "FocusPolicy")
Qt_ItemIsEnabled = get_enum(Qt, "ItemIsEnabled", "ItemFlag")
Qt_ItemIsSelectable = get_enum(Qt, "ItemIsSelectable", "ItemFlag")
Qt_ItemIsDragEnabled = get_enum(Qt, "ItemIsDragEnabled", "ItemFlag")
Qt_NoItemFlags = get_enum(Qt, "NoItemFlags", "ItemFlag")
Qt_CopyAction = get_enum(Qt, "CopyAction", "DropAction")
Qt_CustomContextMenu = get_enum(Qt, "CustomContextMenu", "ContextMenuPolicy")
Qt_LeftDockWidgetArea = get_enum(Qt, "LeftDockWidgetArea", "DockWidgetArea")
Qt_RightDockWidgetArea = get_enum(Qt, "RightDockWidgetArea", "DockWidgetArea")
Qt_Key_Escape = get_enum(Qt, "Key_Escape", "Key")
Qt_RichText = get_enum(Qt, "RichText", "TextFormat")
Qt_KeepAspectRatio = get_enum(Qt, "KeepAspectRatio", "AspectRatioMode")
Qt_SmoothTransformation = get_enum(Qt, "SmoothTransformation", "TransformationMode")
Qt_AscendingOrder = get_enum(Qt, "AscendingOrder", "SortOrder")
