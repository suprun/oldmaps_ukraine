# -*- coding: utf-8 -*-
import html

from qgis.PyQt.QtCore import QSize, QUrl, Qt
from qgis.PyQt.QtGui import QPixmap, QTextDocument
from qgis.PyQt.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QMessageBox,
    QTextBrowser,
    QVBoxLayout,
)

from ..qt_compat import (
    Qt_AlignCenter,
    Qt_AlignHCenter,
    Qt_KeepAspectRatio,
    Qt_RichText,
    Qt_SmoothTransformation,
    get_enum,
    safe_exec,
)

from .constants import (
    HELP_BANNER_SIZE,
    OLDMAPS_HOME,
    PLUGIN_METADATA_PATH,
)


class HelpMixin:
    def show_selected_attribution(self):
        selected = self._selected_layer_record()
        if selected is None:
            return

        page = selected["page"]
        layer_data = selected["layer"]
        page_name = html.escape(str(page.get("name") or "Без назви"))
        layer_name = html.escape(str(layer_data.get("name") or "Без назви"))
        attribution = layer_data.get("attribution") or "Атрибуцію не вказано."
        page_url = html.escape(self._oldmaps_url_for_payload(page, layer_data))

        message = QMessageBox(self)
        message.setWindowTitle("Атрибуція шару")
        message.setTextFormat(Qt_RichText)
        message.setText(
            f"<b>{layer_name}</b><br>"
            f"Локація: {page_name}<br><br>{attribution}<br><br>"
            f'<a href="{page_url}">{page_url}</a>'
        )
        message.setStandardButtons(
            get_enum(QMessageBox, "Ok", "StandardButton")
        )
        safe_exec(message)

    def show_help(self):
        layers_update_date = html.escape(
            self._format_layers_update_date(self.data_metadata.get("file_version"))
        )
        plugin_metadata = self._plugin_metadata()
        plugin_version = html.escape(plugin_metadata.get("version") or "невідомо")
        plugin_author = html.escape(plugin_metadata.get("author") or "невідомо")
        plugin_email = plugin_metadata.get("email") or ""
        plugin_page_url = (
            plugin_metadata.get("repository") or plugin_metadata.get("homepage") or ""
        )
        if plugin_page_url:
            escaped_plugin_page_url = html.escape(plugin_page_url, quote=True)
            plugin_page_link = (
                f'<a href="{escaped_plugin_page_url}">{escaped_plugin_page_url}</a>'
            )
        else:
            plugin_page_link = "не вказано"
        if plugin_email:
            escaped_email = html.escape(plugin_email)
            plugin_contact = f'<a href="mailto:{escaped_email}">{escaped_email}</a>'
        else:
            plugin_contact = "не вказано"
        groups_count = len(self.pages)
        layers_count = self.layer_count

        dialog = QDialog(self)
        dialog.setWindowTitle("Довідка OldMaps")
        dialog.resize(620, 520)

        layout = QVBoxLayout(dialog)

        banner = QLabel(dialog)
        banner.setFixedSize(HELP_BANNER_SIZE)
        banner.setAlignment(Qt_AlignCenter)
        banner_path = self._help_banner_path()
        banner_pixmap = QPixmap(str(banner_path)) if banner_path is not None else QPixmap()
        if not banner_pixmap.isNull():
            banner.setPixmap(
                banner_pixmap.scaled(
                    HELP_BANNER_SIZE,
                    Qt_KeepAspectRatio,
                    Qt_SmoothTransformation,
                )
            )
        else:
            banner.setText(
                "OldMaps.com.ua"
            )
            banner.setStyleSheet(
                "QLabel {"
                "border: 1px solid palette(mid);"
                "background: palette(base);"
                "color: palette(mid);"
                "padding: 8px;"
                "}"
            )
        layout.addWidget(banner, 0, Qt_AlignHCenter)

        browser = QTextBrowser(dialog)
        icon_add = self._help_action_icon(browser, "add", self.add_action)
        icon_info = self._help_action_icon(browser, "info", self.info_action)
        icon_zoom = self._help_action_icon(browser, "zoom", self.zoom_to_selection_action)
        icon_no_duplicates = self._help_action_icon(browser, "no_duplicates", self.no_duplicates_action)
        icon_coverage = self._help_action_icon(browser, "coverage", self.coverage_action)
        icon_borders = self._help_action_icon(browser, "borders", self.all_borders_action)
        icon_tree_expansion = self._help_action_icon(browser, "tree_expansion", self.tree_expansion_action)
        icon_search = self._help_action_icon(browser, "search", self.search_action)
        icon_link = self._help_action_icon(browser, "link", self.link_action)
        browser.setOpenExternalLinks(True)
        browser.setHtml(
            f"""
            <html>
            <body style="font-family: sans-serif; font-size: 10pt; line-height: 1.35;">
              <h2 style="margin-top: 0;">OldMaps для QGIS</h2>
              <p>
                O**OldMaps for QGIS** додає до QGIS док-панель із каталогом геоприв'язаних матеріалів <a href="https://oldmaps.com.ua">OldMaps.com.ua</a>. Каталог об'єднує історичні карти, аерофотознімки та супутникові зображення міст і сіл України за локаціями та часовими періодами. Завдяки синхронізації з іншими картами
                користувачі можуть порівнювати місцевість у різні періоди часу та
                відстежувати динаміку змін ландшафту. Наявні карти доступні в окремих
                Локаціях на території України, кожна Локація має власний набір
                історичних шарів.
              </p>

              <h3>Як додати шар</h3>
              <ul>
                <li>Виберіть шар у списку і натисніть кнопку {icon_add}.</li>
                <li>або двічі клацніть по шару.</li>
                <li>або перетягніть шар зі списку на карту QGIS.</li>
              </ul>

              <h3>Кнопки панелі</h3>
              <ul>
                <li>{icon_add} <b>Додати шар</b> додає вибраний шар у проєкт.</li>
                <li>{icon_info} <b>Атрибуція</b> показує атрибуцію та посилання на джерело шару.</li>
                <li>{icon_zoom} <b>Збільшити до вибраного</b> переносить карту до вибраного міста або шару.</li>
                <li>{icon_link} <b>Переглянути на сайті</b> відкриває сторінку вибраного елемента на oldmaps.com.ua.</li>
                <li>{icon_coverage} <b>Знайти карти в точці</b> вмикає режим кліку по карті та фільтрує список за точкою. Esc вимикає режим кліку, а кнопка «Скинути» очищає фільтр.</li>
                <li>{icon_borders} <b>Показати всі межі</b> вмикає або вимикає відображення меж усіх карт.</li>
                <li>{icon_no_duplicates} <b>Не додавати шари повторно</b> не дозволяє додати той самий tile_url двічі.</li>
                <li>{icon_search} <b>Пошук</b> фільтрує список за назвою шару.</li>
                <li>{icon_tree_expansion} <b>Розгорнути/згорнути все</b> перемикає стан дерева Локацій.</li>
              </ul>

              <h3>Дані</h3>
              <p>
                Джерелом тайлів є OldMaps.com.ua; 
                у QGIS вони додаються як XYZ-шари.
              </p>
              <table cellspacing="0" cellpadding="4" style="border-collapse: collapse;">
                <tr><td><b>Дата останнього оновлення шарів:</b></td><td>{layers_update_date}</td></tr>
                <tr><td><b>Локації:</b></td><td>{groups_count}</td></tr>
                <tr><td><b>Шари:</b></td><td>{layers_count}</td></tr>
              </table>

              <h3>Плагін</h3>
              <table cellspacing="0" cellpadding="4" style="border-collapse: collapse;">
                <tr><td><b>Версія плагіна:</b></td><td>{plugin_version}</td></tr>
                <tr><td><b>Автор плагіна:</b></td><td>{plugin_author}</td></tr>
                <tr><td><b>Контакти автора плагіна:</b></td><td>{plugin_contact}</td></tr>
                <tr><td><b>Сторінка плагіна:</b></td><td>{plugin_page_link}</td></tr>
              </table>

              <h3>Сайт проєкту</h3>
              <p>
                <a href="https://oldmaps.com.ua/">https://oldmaps.com.ua/</a>
              </p>
            </body>
            </html>
            """
        )
        layout.addWidget(browser)

        buttons = QDialogButtonBox(
            get_enum(QDialogButtonBox, "Ok", "StandardButton"),
            dialog,
        )
        buttons.accepted.connect(dialog.accept)
        layout.addWidget(buttons)
        safe_exec(dialog)

    def _format_layers_update_date(self, file_version):
        version = str(file_version or "").strip()
        if len(version) < 8 or not version[:8].isdigit():
            return "невідомо"

        year = version[:4]
        month = version[4:6]
        day = version[6:8]
        revision = version[8:] if len(version) > 8 and version[8:].isdigit() else ""
        formatted = f"{day}.{month}.{year}"
        if revision:
            formatted = f"{formatted}, ревізія {revision}"
        return formatted

    def _plugin_metadata(self):
        metadata = {}
        try:
            with PLUGIN_METADATA_PATH.open("r", encoding="utf-8-sig") as handle:
                for line in handle:
                    line = line.strip()
                    if not line or line.startswith("[") or "=" not in line:
                        continue
                    key, value = line.split("=", 1)
                    metadata[key.strip()] = value.strip()
        except Exception:
            return {}

        return metadata

    def _help_action_icon(self, browser, name, action):
        icon = action.icon()
        if icon.isNull():
            return ""
        pixmap = icon.pixmap(QSize(16, 16))
        if pixmap.isNull():
            return ""
        url = QUrl(f"oldmaps-help-icon:{name}")
        browser.document().addResource(
            get_enum(QTextDocument, "ImageResource", "ResourceType"),
            url,
            pixmap,
        )
        return (
            f'<img src="{html.escape(url.toString())}" '
            'width="16" height="16" style="vertical-align: middle;">'
        )
