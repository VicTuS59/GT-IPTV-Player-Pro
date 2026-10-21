# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Native local subtitle browser; filesystem work stays on a bounded reader."""

import os
import threading

from .i18n import _, localized_language_name
from .paths import plugin_path
from .remote_footer import decorate_remote_footer, footer_item, install_remote_footer
from .source_loader import SourceLoader
from .subtitle_browser import _desktop_size
from .typography import fit_dynamic_text

try:
    from Components.ActionMap import ActionMap
    from Components.Label import Label
    from Components.Pixmap import Pixmap
    from Screens.Screen import Screen
    from enigma import eTimer
except ImportError:  # Pure skin builders and offline tests need no receiver.
    ActionMap = Label = Pixmap = eTimer = None
    Screen = object


ROWS = 7
COMMON_LANGUAGES = ("en", "tr", "de", "ar", "fr", "es", "it", "pt", "pl", "ru", "nl")
FILE_FOOTER = (
    footer_item("green", "Subtitle language"),
    footer_item("yellow", "Search this folder"),
    footer_item("blue", "Download folder"),
    footer_item("ok", "select"),
    footer_item("exit", "back"),
    footer_item("arrows", "navigate"),
)
FOLDER_FOOTER = (
    footer_item("green", "Use this folder"),
    footer_item("ok", "open"),
    footer_item("red", "Up one folder"),
    footer_item("exit", "cancel"),
    footer_item("arrows", "navigate"),
)


def _connect(timer, callback):
    try:
        timer.callback.append(callback)
    except AttributeError:
        timer.timeout.connect(callback)


def _backend():
    from . import local_subtitles
    return local_subtitles


def _language_codes(settings):
    result = [""]
    for code in (getattr(settings, "primary_language", ""),
                 getattr(settings, "secondary_language", "")) + COMMON_LANGUAGES:
        code = str(code or "").strip().lower().replace("-", "_").split("_", 1)[0]
        if code and code != "off" and code not in result:
            result.append(code)
    return tuple(result)


def local_subtitle_browser_skin(size=None, folder_picker=False):
    """Use the existing glass artwork at SD, HD, FHD and UHD sizes."""
    width, height = _desktop_size(size)
    sx, sy = width / 1920.0, height / 1080.0
    sf = min(sx, sy)
    x = lambda value: max(0, int(round(value * sx)))
    y = lambda value: max(0, int(round(value * sy)))
    font = lambda value: max(10, int(round(value * sf)))
    parts = ['<screen name="GTLocalSubtitleBrowserScreen" position="0,0" size="{},{}" '
             'flags="wfNoBorder" backgroundColor="#020617" transparent="0">'.format(width, height)]

    def label(name, left, top, item_width, item_height, point=24, color="#F8FAFC", background=None, z=3):
        fill = ('backgroundColor="{}" transparent="0"'.format(background)
                if background else 'transparent="1"')
        parts.append('<widget name="{}" position="{},{}" size="{},{}" font="Regular;{}" '
                     'foregroundColor="{}" {} zPosition="{}" valign="center" noWrap="1" />'.format(
                         name, x(left), y(top), x(item_width), y(item_height), font(point), color, fill, z))

    def art(name, left, top, item_width, item_height, filename, z=1):
        parts.append('<widget name="{}" position="{},{}" size="{},{}" pixmap="{}" '
                     'alphatest="blend" scale="1" zPosition="{}" />'.format(
                         name, x(left), y(top), x(item_width), y(item_height),
                         plugin_path("skin", "images", filename), z))

    label("video_guard", 0, 0, 1920, 1080, 1, background="#020617", z=-30)
    parts.append('<widget name="app_bg" position="0,0" size="{},{}" zPosition="-20" />'.format(width, height))
    art("content_glass", 36, 20, 1848, 920, "category-glass-panel-r84.png", 0)
    art("header_art", 50, 35, 1820, 78, "category-glass-row-r84.png")
    art("programme_art", 50, 125, 1820, 125, "category-glass-row-r84.png")
    art("toolbar_art", 50, 263, 1820, 48, "category-glass-row-r84.png")
    label("header", 72, 35, 1776, 78, 38)
    label("programme", 80, 128, 1740, 58, 32)
    label("path", 80, 190, 1740, 48, 23, "#AFC3EC")
    label("summary", 70, 265, 1350, 44, 23, "#D8E6F4")
    label("page", 1480, 265, 340, 44, 22, "#AFC3EC")
    for index in range(ROWS):
        top = 317 + index * 79
        art("row_art_{}".format(index), 50, top, 1820, 73, "category-glass-row-r84.png")
        label("name_{}".format(index), 78, top + 7, 1450, 59, 25)
        label("detail_{}".format(index), 1550, top + 7, 285, 59, 21, "#8EDCFF")
        for side, rect in (("top", (50, top, 1820, 3)), ("bottom", (50, top + 70, 1820, 3)),
                           ("left", (50, top, 3, 73)), ("right", (1867, top, 3, 73))):
            label("focus_{}_{}".format(index, side), *rect, point=1,
                  background="#00E5FF" if side in ("top", "left") else "#C33BEE", z=7)
    label("message", 70, 874, 1780, 48, 21, "#AFC3EC")
    label("footer", 0, 962, 1920, 108, 1)
    parts.append("</screen>")
    return decorate_remote_footer("\n".join(parts), FOLDER_FOOTER if folder_picker else FILE_FOOTER,
                                  transparent_panel=True, show_dividers=False, show_web_hint=False,
                                  stacked=True, skin_fonts_scaled=True, panel_height=100)


class GTLocalSubtitleBrowserScreen(Screen):
    def __init__(self, session, controller, metadata=None, _folder_picker=False, _download_folder=""):
        if Label is None or Pixmap is None or ActionMap is None or eTimer is None:
            raise RuntimeError("Enigma2 local subtitle browser widgets are unavailable")
        self._folder_picker = bool(_folder_picker)
        self.skin = local_subtitle_browser_skin(folder_picker=self._folder_picker)
        Screen.__init__(self, session)
        self.controller = controller
        self.metadata = dict(metadata) if isinstance(metadata, dict) else {}
        self.entries, self.selected, self.top = [], 0, 0
        self._folder, self._parents = None, []
        self._query, self._search_base = "", None
        self._download_folder = str(_download_folder or "")
        self._languages, self._language = ("",) + COMMON_LANGUAGES, ""
        self._busy = self._closed = self._started = self._loading_subtitle = False
        self._operation, self._message = "", ""
        self._generation = 0
        self._cancel = threading.Event()
        self._reader = SourceLoader(eTimer(), _connect, self._io_ready)
        from .background import attach_background
        attach_background(self, "app_bg", plugin_path("skin", "images", "global-neon-v0912.png"))
        for name in ("content_glass", "header_art", "programme_art", "toolbar_art"):
            self[name] = Pixmap()
        for name in ("video_guard", "header", "programme", "path", "summary", "page", "message", "footer"):
            self[name] = Label("")
        for row in range(ROWS):
            self["row_art_{}".format(row)] = Pixmap()
            self["name_{}".format(row)] = Label("")
            self["detail_{}".format(row)] = Label("")
            for side in ("top", "bottom", "left", "right"):
                self["focus_{}_{}".format(row, side)] = Label("")
        install_remote_footer(self, FOLDER_FOOTER if self._folder_picker else FILE_FOOTER, stacked=True)
        self["actions"] = ActionMap(
            ["OkCancelActions", "DirectionActions", "ColorActions", "ChannelSelectBaseActions", "NumberActions"],
            {"ok": self.select, "cancel": self.back, "back": self.back,
             "up": lambda: self.move(-1), "down": lambda: self.move(1),
             "upRepeated": lambda: self.move(-1), "downRepeated": lambda: self.move(1),
             "left": lambda: self.move(-ROWS), "right": lambda: self.move(ROWS),
             "leftRepeated": lambda: self.move(-ROWS), "rightRepeated": lambda: self.move(ROWS),
             "channelUp": lambda: self.move(-ROWS), "channelDown": lambda: self.move(ROWS),
             "green": self.save_folder if self._folder_picker else self.choose_language,
             "yellow": self.search, "blue": self.choose_download_folder,
             "red": self.up_folder, "0": self.root}, -1)
        self.onShown.append(self._shown)
        self.onLayoutFinish.append(self._render)
        self.onClose.append(self._stop)
        self._render()

    def _shown(self):
        self._render()
        if not self._started and not self._closed:
            self._started = True
            picker, metadata = self._folder_picker, dict(self.metadata)
            def initial(cancel):
                backend = _backend()
                from .subtitle_settings import load_subtitle_settings
                folder = backend.load_download_folder()
                try:
                    settings = load_subtitle_settings()
                except Exception:
                    settings = None
                languages = _language_codes(settings)
                rows = (backend.storage_roots() if picker else
                        backend.list_subtitle_folder(None, metadata=metadata, cancel=cancel))
                return {"entries": rows, "folder": None, "download_folder": folder,
                        "languages": languages, "clear_history": True,
                        "limited": bool(getattr(rows, "limited", False))}
            self._submit("list", initial)

    def _submit(self, operation, function):
        self._cancel.set()
        self._reader.cancel()
        self._generation += 1
        generation = self._generation
        self._cancel = cancel = threading.Event()
        self._busy, self._operation = True, operation
        self._message = _("Searching subtitle files...") if operation == "search" else _("Loading...")
        self._render()
        self._reader.request(lambda: (generation, operation, function(cancel)))

    def _io_ready(self, value, error):
        if self._closed:
            return
        if error is not None or not value:
            self._busy = False
            self._message = (_("Could not save the download folder.") if self._operation == "save"
                             else _("Could not read this folder."))
            self._render()
            return
        generation, operation, result = value
        if generation != self._generation:
            return
        self._busy = False
        if operation == "save":
            if result:
                self.close(result)
            else:
                self._message = _("Could not save the download folder.")
                self._render()
            return
        result = result if isinstance(result, dict) else {}
        self.entries = [dict(row) for row in result.get("entries", ())
                        if isinstance(row, dict) and row.get("kind") in ("directory", "file")
                        and (not self._folder_picker or row.get("kind") == "directory")]
        if result.get("clear_history"):
            self._parents = []
        if result.get("push") is not None:
            self._parents.append(result["push"])
        if result.get("pop"):
            self._parents = self._parents[:-1]
        self._folder = result.get("folder", self._folder)
        self._query = str(result.get("query") or "")
        self._search_base = result.get("search_base")
        self._download_folder = str(result.get("download_folder", self._download_folder))
        self._languages = tuple(result.get("languages") or self._languages)
        self.selected = self.top = 0
        selected_path = result.get("selected_path")
        if selected_path:
            self.selected = next((index for index, row in enumerate(self.entries)
                                  if row.get("path") == selected_path), 0)
        self._message = (_("Search limit reached. Narrow your search.") if result.get("limited") else
                         ("" if self.entries else _("No subtitle files found.")))
        self._render()

    def _selected(self):
        return self.entries[self.selected] if self.entries and 0 <= self.selected < len(self.entries) else None

    def _browse(self, folder, push=False, pop=False, selected_path="", clear_history=False):
        if self._closed or self._loading_subtitle:
            return
        selected = self._selected()
        previous = (self._folder, str((selected or {}).get("path") or "")) if push else None
        metadata, picker = dict(self.metadata), self._folder_picker
        def read(cancel):
            backend = _backend()
            rows = (backend.storage_roots() if picker and folder is None else
                    backend.list_subtitle_folder(folder, metadata=metadata, cancel=cancel))
            return {"entries": rows, "folder": folder, "push": previous,
                    "pop": pop, "selected_path": selected_path, "clear_history": clear_history,
                    "limited": bool(getattr(rows, "limited", False))}
        self._submit("list", read)

    def _render(self):
        if self._closed:
            return
        title = _("Download folder") if self._folder_picker else _("Choose a subtitle file")
        self.setTitle(title)
        for name, text, limit in (
                ("header", title, 80),
                ("programme", (self.metadata.get("title") or _("Local subtitles")), 105),
                ("path", (self._search_base or self._folder or _("Device folders")), 150)):
            fit_dynamic_text(self[name], text, max_lines=1, fallback_chars=limit)
        language = localized_language_name(self._language) if self._language else _("Automatic detection")
        summary = (_("Choose a writable folder on your receiver.") if self._folder_picker else
                   (_("Subtitle language: {}").format(language)))
        if self._query:
            summary = "{}: {}".format(_("Search results"), self._query)
        fit_dynamic_text(self["summary"], summary, max_lines=1, fallback_chars=95)
        count = len(self.entries)
        self.selected = max(0, min(self.selected, count - 1)) if count else 0
        self.top = (self.selected // ROWS) * ROWS
        self["page"].setText("{}: {} / {}".format(_("Page"), self.selected // ROWS + 1,
                                                 max(1, (count + ROWS - 1) // ROWS)))
        for row in range(ROWS):
            index = self.top + row
            exists = index < count
            for stem in ("row_art", "name", "detail"):
                widget = self["{}_{}".format(stem, row)]
                widget.show() if exists else widget.hide()
            for side in ("top", "bottom", "left", "right"):
                widget = self["focus_{}_{}".format(row, side)]
                widget.show() if exists and index == self.selected else widget.hide()
            if not exists:
                continue
            entry = self.entries[index]
            name = _(entry["label"]) if entry.get("label") else str(entry.get("name") or "")
            directory = entry.get("kind") == "directory"
            prefix = "▸ " if directory else ("★ " if entry.get("score", 0) > 0 else "")
            fit_dynamic_text(self["name_{}".format(row)], name, max_lines=1,
                             fallback_chars=100, prefix=prefix)
            detail = (_("Device storage") if directory else
                      str(entry.get("language") or os.path.splitext(name)[1].lstrip(".")).upper())
            fit_dynamic_text(self["detail_{}".format(row)], detail, max_lines=1, fallback_chars=24)
        message = self._message
        if not message and self._folder and (self._folder == "/tmp" or self._folder.startswith("/tmp/")):
            message = _("The temporary folder is cleared when the receiver restarts.")
        if not message and self._download_folder and not self._folder_picker:
            message = _("Download folder: {}").format(self._download_folder)
        fit_dynamic_text(self["message"], message, max_lines=1, fallback_chars=120)

    def move(self, step):
        if self._closed or self._busy or self._loading_subtitle or not self.entries:
            return
        self.selected = max(0, min(len(self.entries) - 1, self.selected + int(step)))
        self._render()

    def select(self):
        if self._busy or self._loading_subtitle or self._closed:
            return
        entry = self._selected()
        if not entry:
            return
        if entry["kind"] == "directory":
            if entry.get("parent"):
                self.up_folder()
            else:
                self._browse(str(entry["path"]), push=True)
        elif not self._folder_picker:
            self._loading_subtitle = True
            self._message = _("Loading...")
            self._render()
            try:
                accepted = bool(self.controller.load_local_file(
                    str(entry["path"]), language=self._language, on_finished=self._subtitle_loaded))
            except Exception:
                accepted = False
            if not accepted:
                self._loading_subtitle = False
                self._message = _("Could not load this subtitle file.")
                self._render()

    def _subtitle_loaded(self, *unused):
        if self._closed:
            return
        self._loading_subtitle = False
        if getattr(self.controller, "last_download_succeeded", False):
            self.close(True)
        else:
            self._message = _("Could not load this subtitle file.")
            self._render()

    def choose_language(self):
        if self._busy or self._loading_subtitle or self._folder_picker or self._closed:
            return
        choices = [(_("Automatic detection") if not code else localized_language_name(code), code)
                   for code in self._languages]
        try:
            from Screens.ChoiceBox import ChoiceBox
            self.session.openWithCallback(self._language_chosen, ChoiceBox,
                                          title=_("Subtitle language"), list=choices,
                                          selection=self._languages.index(self._language))
        except Exception:
            self._message = _("Error")
            self._render()

    def _language_chosen(self, choice):
        if choice and not self._closed:
            self._language = str(choice[1] or "")
            self._render()

    def search(self):
        if self._busy or self._loading_subtitle or self._folder_picker or self._closed:
            return
        selected = self._selected() or {}
        base = self._folder or (selected.get("path") if selected.get("kind") == "directory" else None)
        if not base:
            self._message = _("Folder is not available.")
            self._render()
            return
        try:
            from Screens.VirtualKeyBoard import VirtualKeyBoard
            self.session.openWithCallback(lambda query: self._search_entered(base, query), VirtualKeyBoard,
                                          title=_("Search subtitle files"), text=self._query)
        except Exception:
            self._message = _("The virtual keyboard is unavailable on this image.")
            self._render()

    def _search_entered(self, folder, query):
        if query is None or self._closed:
            return
        query = str(query).strip()[:120]
        if not query:
            self._browse(self._folder)
            return
        metadata, current = dict(self.metadata), self._folder
        def read(cancel):
            result = dict(_backend().search_subtitle_files(folder, query, metadata=metadata, cancel=cancel))
            result.update(folder=current, query=query, search_base=folder)
            return result
        self._submit("search", read)

    def choose_download_folder(self):
        if self._busy or self._loading_subtitle or self._folder_picker or self._closed:
            return
        try:
            self.session.openWithCallback(self._folder_chosen, GTSubtitleDownloadFolderScreen,
                                          current_folder=self._download_folder)
        except Exception:
            self._message = _("Error")
            self._render()

    def _folder_chosen(self, folder):
        if folder and not self._closed:
            self._download_folder = str(folder)
            self._message = _("Download folder saved.")
            if self._folder is None:
                self._browse(None)
            else:
                self._render()

    def save_folder(self):
        if self._busy or self._closed or not self._folder_picker:
            return
        if not self._folder:
            self._message = _("Choose a writable folder on your receiver.")
            self._render()
            return
        folder = self._folder
        def save(cancel):
            backend = _backend()
            return backend.load_download_folder() if backend.save_download_folder(folder, cancel=cancel) else False
        self._submit("save", save)

    def up_folder(self):
        if self._closed or self._loading_subtitle or (self._busy and self._operation == "save"):
            return
        if self._query:
            self._browse(self._search_base or self._folder, clear_history=True)
        elif self._folder and self._folder != os.path.sep:
            parent = os.path.dirname(self._folder.rstrip(os.path.sep)) or os.path.sep
            if self._parents and self._parents[-1][0] == parent:
                self._browse(parent, pop=True, selected_path=self._folder)
            else:
                self._browse(parent, clear_history=True, selected_path=self._folder)
        else:
            self.root()

    def root(self):
        if not self._closed and not self._loading_subtitle and not (self._busy and self._operation == "save"):
            self._browse(os.path.sep, clear_history=True)

    def back(self):
        if self._folder_picker:
            self.close(False)
        elif self._loading_subtitle:
            self.close(False)
        elif self._query:
            self.up_folder()
        elif self._parents:
            folder, selected_path = self._parents[-1]
            self._browse(folder, pop=True, selected_path=selected_path)
        elif self._folder and self._folder != os.path.sep:
            self.up_folder()
        else:
            self.close(False)

    def _stop(self):
        if self._closed:
            return
        self._closed = True
        self._cancel.set()
        self._generation += 1
        self._reader.close()
        if self._loading_subtitle:
            self._loading_subtitle = False
            cancel = getattr(self.controller, "cancel_pending", None)
            if callable(cancel):
                try:
                    cancel()
                except Exception:
                    pass


class GTSubtitleDownloadFolderScreen(GTLocalSubtitleBrowserScreen):
    def __init__(self, session, current_folder=""):
        super().__init__(session, None, _folder_picker=True, _download_folder=current_folder)
