# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Glass, resolution-independent browser for all subtitle providers."""

import os

from .i18n import _
from .online_subtitles import canonical_subtitle_fps, provider_display_name
from .paths import plugin_path
from .remote_footer import (
    decorate_remote_footer,
    footer_item,
    install_remote_footer,
)
from .typography import fit_dynamic_text

try:
    from Components.ActionMap import ActionMap
    from Components.Label import Label
    from Components.Pixmap import Pixmap
    from Screens.Screen import Screen
    from enigma import getDesktop
except ImportError:  # Source maintenance and unit tests run without Enigma2.
    ActionMap = None
    Label = None
    Pixmap = None
    Screen = object
    getDesktop = None


ROWS = 7
FILTERS = ("all", "opensubtitles", "subdl", "subsource")
SEARCH_PROVIDER_NAMES = "SubDL + OpenSubtitles.com + SubSource"
COMPATIBILITY_STATUSES = (
    "high", "possible", "fps_convert", "unknown", "different",
)
COMPATIBILITY_LABELS = {
    "high": "High compatibility",
    "possible": "Possible compatibility",
    "fps_convert": "FPS will be converted",
    "unknown": "Compatibility unknown",
    "different": "Different version",
}
COMPATIBILITY_COLORS = {
    "high": "#22C55E",
    "possible": "#EAB308",
    "fps_convert": "#3B82F6",
    "unknown": "#94A3B8",
    "different": "#EF4444",
}
COMPATIBILITY_ART = {
    "high": "status-green-r64.png",
    "possible": "status-yellow-r64.png",
    "fps_convert": "status-neutral-r64.png",
    "unknown": "status-neutral-r64.png",
    "different": "status-red-r64.png",
}
SUBTITLE_BROWSER_FOOTER = (
    footer_item("green", "search"),
    footer_item("yellow", "filter"),
    footer_item("blue", "edit"),
    footer_item("ok", "download"),
    footer_item("exit", "back"),
    footer_item("arrows", "navigate"),
)
FLAG_COUNTRIES = {
    "sq": "al", "ar": "sa", "bg": "bg", "ca": "es",
    "zh": "cn", "hr": "hr", "cs": "cz", "da": "dk",
    "nl": "nl", "en": "gb", "et": "ee", "fa": "ir",
    "fi": "fi", "fr": "fr", "fy": "nl", "gl": "es",
    "de": "de", "el": "gr", "he": "il", "hu": "hu",
    "is": "is", "id": "id", "it": "it", "ku": "iq",
    "lv": "lv", "lt": "lt", "mk": "mk", "nb": "no",
    "nn": "no", "pl": "pl", "pt": "pt", "ro": "ro",
    "ru": "ru", "sr": "rs", "sk": "sk", "sl": "si",
    "es": "es", "sv": "se", "ta": "in", "th": "th",
    "tr": "tr", "uk": "ua", "vi": "vn",
}


def _desktop_size(size=None):
    if isinstance(size, (tuple, list)) and len(size) == 2:
        try:
            return max(1, int(size[0])), max(1, int(size[1]))
        except (TypeError, ValueError, OverflowError):
            pass
    if getDesktop is not None:
        try:
            desktop = getDesktop(0).size()
            return max(1, int(desktop.width())), max(1, int(desktop.height()))
        except Exception:
            pass
    return 1920, 1080


def subtitle_flag_country(language):
    code = str(language or "").strip().lower().replace("-", "_")
    return FLAG_COUNTRIES.get(code.split("_", 1)[0], "xx")


def subtitle_flag_path(language):
    return plugin_path(
        "skin", "images", "subtitle-flags",
        subtitle_flag_country(language) + ".png",
    )


def filtered_subtitle_results(results, provider_filter):
    entries = [item for item in (results or ()) if isinstance(item, dict)]
    if provider_filter not in FILTERS[1:]:
        return entries
    return [
        item for item in entries
        if str(item.get("provider") or "").lower() == provider_filter
    ]


def manual_subtitle_metadata(metadata, title):
    """Return a title-only lookup override without stale catalogue identity.

    A manually corrected name must not be combined with an IMDb/TMDb/SD id or
    release year that belonged to the portal's original (possibly incorrect)
    title.  Season, episode and playback compatibility hints remain useful and
    are deliberately preserved.  Users can include a year in the edited title
    when they need to distinguish a remake; the provider normalizer will infer
    that year again.
    """
    value = " ".join(str(title or "").split())[:120]
    if len(value) < 2:
        return None
    override = dict(metadata) if isinstance(metadata, dict) else {}
    override["title"] = value
    override["year"] = 0
    for key in ("imdb_id", "tmdb_id", "sd_id"):
        override.pop(key, None)
    return override


def subtitle_browser_skin(size=None):
    """Return the glass subtitle browser at UHD, FHD, HD and SD sizes."""
    width, height = _desktop_size(size)
    sx = float(width) / 1920.0
    sy = float(height) / 1080.0
    sf = min(sx, sy)
    image_dir = plugin_path("skin", "images")
    glass_panel = os.path.join(image_dir, "category-glass-panel-r84.png")
    glass_row = os.path.join(image_dir, "category-glass-row-r84.png")

    def x(value):
        return max(0, int(round(float(value) * sx)))

    def y(value):
        return max(0, int(round(float(value) * sy)))

    def font(value):
        return max(10, int(round(float(value) * sf)))

    parts = [
        '<screen name="GTOnlineSubtitleBrowserScreen" position="0,0" '
        'size="{},{}" flags="wfNoBorder" backgroundColor="#020617" '
        'transparent="0">'.format(width, height),
        '<widget name="video_guard" position="0,0" size="{},{}" '
        'font="Regular;1" backgroundColor="#020617" transparent="0" '
        'zPosition="-30" />'.format(width, height),
        '<widget name="app_bg" position="0,0" size="{},{}" '
        'zPosition="-20" />'.format(width, height),
    ]

    def pixmap(name, left, top, item_width, item_height, path, z=1):
        parts.append(
            '<widget name="{}" position="{},{}" size="{},{}" '
            'pixmap="{}" alphatest="blend" scale="1" zPosition="{}" />'.format(
                name, x(left), y(top), x(item_width), y(item_height), path, z,
            )
        )

    def label(
        name, left, top, item_width, item_height, text_size=24,
        color="#F8FAFC", background=None, align="left", z=3,
    ):
        background_xml = (
            'backgroundColor="{}" transparent="0"'.format(background)
            if background else 'transparent="1"'
        )
        parts.append(
            '<widget name="{}" position="{},{}" size="{},{}" '
            'font="Regular;{}" foregroundColor="{}" {} zPosition="{}" '
            'halign="{}" valign="center" noWrap="1" />'.format(
                name, x(left), y(top), x(item_width), y(item_height),
                font(text_size), color, background_xml, z, align,
            )
        )

    # Every surface is an RGBA asset over the opaque application wallpaper.
    # This preserves the approved glass look without allowing DVB video to
    # leak through the full-desktop guard on image-specific framebuffer paths.
    pixmap("content_glass", 36, 20, 1848, 920, glass_panel, z=0)
    pixmap("header_art", 50, 35, 1820, 78, glass_row, z=1)
    pixmap("programme_art", 50, 125, 1820, 125, glass_row, z=1)
    pixmap("filter_art", 1450, 151, 370, 70,
           os.path.join(image_dir, "status-neutral-r64.png"), z=2)
    pixmap("toolbar_art", 50, 263, 1820, 48, glass_row, z=1)

    label("header", 72, 35, 1776, 78, 40)
    label("programme", 80, 132, 1260, 58, 35, z=3)
    label("provider_status", 80, 191, 1260, 42, 22, "#AFC3EC", z=3)
    label("filter", 1470, 151, 330, 70, 24, "#D8E6F4", align="center", z=3)
    label("summary", 70, 265, 1230, 44, 24, "#D8E6F4")
    label("page", 1430, 265, 390, 44, 22, "#AFC3EC", align="right")

    for index in range(ROWS):
        top = 317 + index * 79
        pixmap(
            "row_art_{}".format(index), 50, top, 1820, 73,
            glass_row, z=1,
        )
        # A four-sided outline follows the user's saved fill/frame palette.
        # Border-only focus keeps the underlying row translucent.
        label("focus_{}_top".format(index), 50, top, 1820, 3, 1,
              background="#00E5FF", z=7)
        label("focus_{}_bottom".format(index), 50, top + 70, 1820, 3, 1,
              background="#C33BEE", z=7)
        label("focus_{}_left".format(index), 50, top, 3, 73, 1,
              background="#00E5FF", z=7)
        label("focus_{}_right".format(index), 1867, top, 3, 73, 1,
              background="#C33BEE", z=7)
        parts.append(
            '<widget name="flag_{}" position="{},{}" size="{},{}" '
            'alphatest="blend" scale="1" zPosition="4" />'.format(
                index, x(70), y(top + 12), x(68), y(49),
            )
        )
        label("language_{}".format(index), 154, top + 8, 70, 57, 24, z=5)
        label(
            "provider_{}".format(index), 240, top + 8, 250, 57,
            22, "#67E8F9", z=5,
        )
        label("release_{}".format(index), 510, top + 7, 750, 59, 23, z=5)
        for status in COMPATIBILITY_STATUSES:
            pixmap(
                "status_art_{}_{}".format(status, index),
                1280, top + 10, 315, 53,
                os.path.join(image_dir, COMPATIBILITY_ART[status]), z=4,
            )
        label(
            "compatibility_{}".format(index), 1295, top + 8, 285, 57,
            18, "#E8F3FF", align="center", z=6,
        )
        label(
            "meta_{}".format(index), 1610, top + 8, 220, 57,
            18, "#8EDCFF", align="right", z=5,
        )

    label("message", 70, 874, 1780, 48, 21, "#AFC3EC", align="center")
    label("footer", 0, 962, 1920, 108, 1)
    parts.append("</screen>")
    return decorate_remote_footer(
        "\n".join(parts),
        SUBTITLE_BROWSER_FOOTER,
        transparent_panel=False,
        show_dividers=False,
        stacked=True,
        skin_fonts_scaled=True,
        panel_height=100,
    )


class GTOnlineSubtitleBrowserScreen(Screen):
    """Search all providers once, then filter the local combined result set."""

    def __init__(self, session, controller, metadata=None):
        if Label is None or Pixmap is None or ActionMap is None:
            raise RuntimeError("Enigma2 subtitle browser widgets are unavailable")
        self.skin = subtitle_browser_skin()
        Screen.__init__(self, session)
        self.controller = controller
        self.metadata = dict(metadata) if isinstance(metadata, dict) else {}
        self.results = []
        self.provider_filter = "all"
        self.selected = 0
        self.top = 0
        self._busy = False
        self._closed = False
        self._flag_keys = [None] * ROWS

        from .background import attach_background
        attach_background(
            self,
            "app_bg",
            plugin_path("skin", "images", "global-neon-v0912.png"),
        )
        for name in (
            "content_glass", "header_art", "programme_art", "filter_art",
            "toolbar_art",
        ):
            self[name] = Pixmap()
        for name in (
            "video_guard", "header", "programme", "provider_status",
            "filter", "summary", "page", "message", "footer",
        ):
            self[name] = Label("")
        for index in range(ROWS):
            self["row_art_{}".format(index)] = Pixmap()
            for stem in (
                "language", "provider", "release", "compatibility", "meta",
            ):
                self["{}_{}".format(stem, index)] = Label("")
            for side in ("top", "bottom", "left", "right"):
                self["focus_{}_{}".format(index, side)] = Label("")
            for status in COMPATIBILITY_STATUSES:
                self["status_art_{}_{}".format(status, index)] = Pixmap()
            self["flag_{}".format(index)] = Pixmap()

        self["header"].setText("GT IPTV PLAYER PRO • " + _("Subtitles"))
        self["programme"].setText(self._programme_text())
        self["provider_status"].setText(self._provider_status_text())
        install_remote_footer(
            self,
            SUBTITLE_BROWSER_FOOTER,
            stacked=True,
        )
        self["actions"] = ActionMap(
            [
                "OkCancelActions", "DirectionActions", "ColorActions",
                "ChannelSelectBaseActions",
            ],
            {
                "ok": self.download_selected,
                "cancel": self.close,
                "back": self.close,
                "up": lambda: self.move(-1),
                "down": lambda: self.move(1),
                "upRepeated": lambda: self.move(-1),
                "downRepeated": lambda: self.move(1),
                "left": lambda: self.move(-ROWS),
                "right": lambda: self.move(ROWS),
                "leftRepeated": lambda: self.move(-ROWS),
                "rightRepeated": lambda: self.move(ROWS),
                "channelUp": lambda: self.move(-ROWS),
                "channelDown": lambda: self.move(ROWS),
                "green": self.search,
                "yellow": self.cycle_filter,
                "blue": self.edit_title,
            },
            -1,
        )
        self.onLayoutFinish.append(self._layout_ready)
        self.onClose.append(self._stop)
        self.setTitle("GT IPTV PLAYER PRO • " + _("Subtitles"))
        self._render()

    def _layout_ready(self):
        """Reassert layers after image-specific Enigma2 skin construction."""
        self["video_guard"].show()
        for name in (
            "content_glass", "header_art", "programme_art", "filter_art",
            "toolbar_art",
        ):
            self[name].show()
        self._render()

    def _programme_text(self):
        title = " ".join(str(self.metadata.get("title") or "").split())
        year = str(self.metadata.get("year") or "").strip()
        if year and year not in title:
            title = "{} ({})".format(title, year)
        season = self.metadata.get("season")
        episode = self.metadata.get("episode")
        try:
            if int(season or 0) > 0 and int(episode or 0) > 0:
                title += " • S{:02d}E{:02d}".format(int(season), int(episode))
        except (TypeError, ValueError, OverflowError):
            pass
        return title[:120] or _("Subtitles")

    def _provider_status_text(self):
        values = ["{}: {}".format(_("Search"), SEARCH_PROVIDER_NAMES)]
        fps = canonical_subtitle_fps(self.metadata.get("fps"))
        if fps:
            values.append(
                ("{:.3f}".format(fps)).rstrip("0").rstrip(".") + " FPS"
            )
        try:
            resolution = int(self.metadata.get("resolution") or 0)
        except (TypeError, ValueError, OverflowError):
            resolution = 0
        if resolution in (2160, 1080, 720, 576, 480):
            values.append("{}p".format(resolution))
        return " • ".join(values)[:160]

    def _filter_name(self):
        if self.provider_filter == "all":
            return _("All")
        return provider_display_name(self.provider_filter)

    def _filtered(self):
        return filtered_subtitle_results(self.results, self.provider_filter)

    def _render(self):
        visible_results = self._filtered()
        if visible_results:
            self.selected = max(0, min(self.selected, len(visible_results) - 1))
            if self.selected < self.top:
                self.top = (self.selected // ROWS) * ROWS
            if self.selected >= self.top + ROWS:
                self.top = (self.selected // ROWS) * ROWS
            last_page = ((len(visible_results) - 1) // ROWS) * ROWS
            self.top = max(0, min(self.top, last_page))
        else:
            self.selected = self.top = 0
        self["filter"].setText(
            "{}: {}".format(_("Filter"), self._filter_name())
        )
        self["summary"].setText(
            "{}: {}".format(_("Subtitles"), len(visible_results))
        )
        page_count = max(1, (len(visible_results) + ROWS - 1) // ROWS)
        current_page = min(page_count, self.selected // ROWS + 1)
        self["page"].setText(
            "{}: {} / {}".format(_("Page"), current_page, page_count)
        )
        rows = visible_results[self.top:self.top + ROWS]
        for row in range(ROWS):
            labels = [
                self["{}_{}".format(stem, row)]
                for stem in (
                    "language", "provider", "release", "compatibility",
                    "meta",
                )
            ]
            focus_widgets = [
                self["focus_{}_{}".format(row, side)]
                for side in ("top", "bottom", "left", "right")
            ]
            status_art = [
                self["status_art_{}_{}".format(status, row)]
                for status in COMPATIBILITY_STATUSES
            ]
            flag = self["flag_{}".format(row)]
            row_art = self["row_art_{}".format(row)]
            if row >= len(rows):
                for widget in labels + focus_widgets + status_art:
                    widget.hide()
                row_art.hide()
                flag.hide()
                self._flag_keys[row] = None
                continue
            result = rows[row]
            row_art.show()
            for widget in labels:
                widget.show()
            for widget in status_art:
                widget.hide()
            language = str(result.get("language") or "--").upper()[:3]
            provider = str(result.get("provider") or "subdl").lower()
            release = " ".join(
                str(result.get("release") or _("Subtitle")).split()
            )[:150]
            meta = []
            try:
                fps = float(
                    result.get("subtitle_fps") or result.get("fps") or 0
                )
            except (TypeError, ValueError, OverflowError):
                fps = 0
            if fps > 0:
                fps_text = ("{:.3f}".format(fps)).rstrip("0").rstrip(".")
                if result.get("compatibility_status") == "fps_convert":
                    video_fps = canonical_subtitle_fps(result.get("video_fps"))
                    if video_fps:
                        target_text = (
                            "{:.3f}".format(video_fps)
                        ).rstrip("0").rstrip(".")
                        fps_text = "{}>{}".format(fps_text, target_text)
                meta.append(fps_text + " FPS")
            if result.get("hearing_impaired"):
                meta.append("HI")
            status = str(
                result.get("compatibility_status") or "unknown"
            ).lower()
            if status not in COMPATIBILITY_STATUSES:
                status = "unknown"
            self["language_{}".format(row)].setText(language)
            self["provider_{}".format(row)].setText(provider_display_name(provider))
            fit_dynamic_text(
                self["release_{}".format(row)], release, max_lines=1,
            )
            fit_dynamic_text(
                self["compatibility_{}".format(row)],
                _(COMPATIBILITY_LABELS[status]),
                max_lines=1,
            )
            self["meta_{}".format(row)].setText(" • ".join(meta))
            self["status_art_{}_{}".format(status, row)].show()
            absolute = self.top + row
            for widget in focus_widgets:
                widget.show() if absolute == self.selected else widget.hide()
            self._set_flag(row, language)

    def _set_flag(self, row, language):
        path = subtitle_flag_path(language)
        key = (subtitle_flag_country(language), path)
        widget = self["flag_{}".format(row)]
        if self._flag_keys[row] != key:
            self._flag_keys[row] = key
            try:
                from .background import attach_pixmap
                attach_pixmap(
                    self, "flag_{}".format(row), path,
                    key=("subtitle-flag", row),
                )
            except Exception:
                widget.hide()
                return
        widget.show()

    def move(self, step):
        entries = self._filtered()
        if not entries or self._busy:
            return
        self.selected = max(0, min(len(entries) - 1, self.selected + int(step)))
        self._render()

    def cycle_filter(self):
        if self._busy:
            return
        try:
            index = FILTERS.index(self.provider_filter)
        except ValueError:
            index = 0
        self.provider_filter = FILTERS[(index + 1) % len(FILTERS)]
        self.selected = self.top = 0
        self._render()

    def edit_title(self):
        """Open Enigma2's keyboard for a manual provider search title."""
        if self._closed or self._busy:
            return
        try:
            from Screens.VirtualKeyBoard import VirtualKeyBoard
        except ImportError:
            self["message"].setText(
                _("The virtual keyboard is unavailable on this image.")
            )
            return
        opener = getattr(self.session, "openWithCallback", None)
        if not callable(opener):
            self["message"].setText(_("Could not open the virtual keyboard."))
            return
        current = " ".join(str(self.metadata.get("title") or "").split())[:120]
        try:
            opener(
                self._title_entered,
                VirtualKeyBoard,
                title=_("Edit"),
                text=current,
            )
        except Exception:
            self["message"].setText(_("Could not open the virtual keyboard."))

    def _title_entered(self, value):
        if value is None or self._closed:
            return
        metadata = manual_subtitle_metadata(self.metadata, value)
        if metadata is None:
            self["message"].setText(_("Error"))
            return
        self.metadata = metadata
        self.results = []
        self.provider_filter = "all"
        self.selected = self.top = 0
        self["programme"].setText(self._programme_text())
        self["message"].setText("")
        self._render()

    def search(self):
        if self._closed or self._busy:
            return
        searcher = getattr(self.controller, "search_combined", None)
        if not callable(searcher):
            self["message"].setText(_("Error"))
            return
        self._busy = True
        self["message"].setText(
            "{}  {}".format(_("Loading..."), SEARCH_PROVIDER_NAMES)
        )
        opened = False
        try:
            opened = bool(searcher(
                self._search_ready,
                self._search_finished,
                dict(self.metadata),
            ))
        except Exception:
            opened = False
        if not opened:
            self._busy = False
            self["message"].setText(_("Error"))

    def _search_ready(self, results, notices):
        if self._closed:
            return
        self.results = list(results or ())[:100]
        self.selected = self.top = 0
        if notices:
            self["message"].setText("  |  ".join(notices)[:240])
        elif not self.results:
            self["message"].setText("{}: 0".format(_("Subtitles")))
        else:
            self["message"].setText("")
        self._render()

    def _search_finished(self):
        if self._closed:
            return
        self._busy = False
        self._render()

    def download_selected(self):
        if self._closed or self._busy:
            return
        entries = self._filtered()
        if not entries:
            return
        downloader = getattr(self.controller, "download_result", None)
        if not callable(downloader):
            self["message"].setText(_("Error"))
            return
        self._busy = True
        self["message"].setText(_("Loading..."))
        opened = False
        try:
            opened = bool(downloader(entries[self.selected], self._download_finished))
        except Exception:
            opened = False
        if not opened:
            self._busy = False
            self["message"].setText(_("Error"))

    def _download_finished(self):
        if self._closed:
            return
        self._busy = False
        if getattr(self.controller, "last_download_succeeded", False):
            self.close(True)
        else:
            status = getattr(self.controller, "last_download_status", "")
            self["message"].setText(
                _("Different version") if status == "different" else _("Error")
            )
            self._render()

    def _stop(self):
        if self._closed:
            return
        self._closed = True
        if self._busy:
            cancel = getattr(self.controller, "cancel_pending", None)
            if callable(cancel):
                try:
                    cancel()
                except Exception:
                    pass
