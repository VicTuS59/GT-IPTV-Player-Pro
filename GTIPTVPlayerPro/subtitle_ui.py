# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Full-screen receiver settings for independent online subtitles."""

import threading

try:
    from Components.ActionMap import ActionMap
    from Components.Label import Label
    from Components.Pixmap import Pixmap
    from Screens.Screen import Screen
    from enigma import ePoint, eTimer, gFont, getDesktop
except ImportError:  # Responsive skin tests also run outside Enigma2.
    ActionMap = None
    Label = None
    Pixmap = None
    Screen = object
    ePoint = None
    eTimer = None
    gFont = None
    getDesktop = None

try:
    from skin import parseColor
except ImportError:
    parseColor = None

from .background import attach_background
from .i18n import N_, _, localized_upper, supported_language_names
from .online_subtitles import provider_display_name, subtitle_client
from .paths import plugin_path
from .remote_footer import decorate_remote_footer, footer_item, install_remote_footer
from .subtitle_settings import (
    BACKGROUND_STYLES,
    FONT_COLORS,
    FONT_SIZES,
    PROVIDERS,
    SEARCH_MODES,
    VERTICAL_POSITIONS,
    load_subtitle_settings,
    save_subtitle_settings,
)
from .typography import fit_dynamic_text, font_px


ROWS = 16
# The r90 settings wallpaper already contains the legacy one-panel rows and
# footer frame.  This screen builds its own two-panel glass layout, so drawing
# the legacy wallpaper underneath produces doubled borders and long horizontal
# lines through the live preview.  Reuse the clean responsive neon wallpaper;
# background.py selects its UHD/FHD/HD raster and fills SD desktops safely.
BACKGROUND_PATH = plugin_path("skin", "images", "global-neon-v0912.png")
FOOTER = (
    footer_item("red", "back"),
    footer_item("green", "save"),
    footer_item("yellow", "api_test"),
    footer_item("blue", "preview"),
)
PREVIEW_FOOTER = (footer_item("exit", "back"),)


def _connect_timer(timer, callback):
    try:
        timer.callback.append(callback)
        return
    except Exception:
        pass
    try:
        timer.timeout.connect(callback)
    except Exception:
        pass


def _scale():
    try:
        size = getDesktop(0).size()
        width, height = int(size.width()), int(size.height())
    except Exception:
        width, height = 1920, 1080
    factor = min(width / 1920.0, height / 1080.0)
    return width, height, lambda value: max(1, int(round(value * factor)))


def _desktop_size(size=None):
    if isinstance(size, (tuple, list)) and len(size) == 2:
        try:
            return max(1, int(size[0])), max(1, int(size[1]))
        except (TypeError, ValueError, OverflowError):
            pass
    try:
        desktop = getDesktop(0).size()
        return max(1, int(desktop.width())), max(1, int(desktop.height()))
    except Exception:
        return 1920, 1080


def _label(parts, px, name, x, y, w, h, size=25, color="#F8FAFC",
           background=None, align="left", z=3, nowrap=True):
    parts.append(
        '<widget name="{}" position="{},{}" size="{},{}" font="Regular;{}" '
        'foregroundColor="{}" backgroundColor="{}" transparent="{}" '
        'halign="{}" valign="center" noWrap="{}" zPosition="{}" />'.format(
            name, px(x) if x else 0, px(y) if y else 0, px(w), px(h),
            font_px(px, size), color, background or "#020617",
            "0" if background else "1", align, "1" if nowrap else "0", z,
        )
    )


def _settings_skin(size=None):
    """Build the two-panel glass settings page for UHD, FHD, HD and SD."""
    width, height = _desktop_size(size)
    sx = float(width) / 1920.0
    sy = float(height) / 1080.0
    sf = min(sx, sy)
    glass_panel = plugin_path("skin", "images", "category-glass-panel-r84.png")
    glass_row = plugin_path("skin", "images", "category-glass-row-r84.png")

    def x(value):
        return max(0, int(round(float(value) * sx)))

    def y(value):
        return max(0, int(round(float(value) * sy)))

    def font(value):
        return max(10, int(round(float(value) * sf)))

    parts = [
        '<screen name="GTSubtitleSettingsScreen" position="0,0" size="{},{}" '
        'flags="wfNoBorder" backgroundColor="#020617" transparent="0">'.format(
            width, height
        ),
        '<widget name="video_guard" position="0,0" size="{},{}" '
        'font="Regular;1" backgroundColor="#020617" transparent="0" '
        'zPosition="-30" />'.format(width, height),
        '<widget name="app_bg" position="0,0" size="{},{}" zPosition="0" />'.format(
            width, height
        ),
    ]

    def pixmap(name, left, top, item_width, item_height, path, z=1):
        parts.append(
            '<widget name="{}" position="{},{}" size="{},{}" '
            'pixmap="{}" alphatest="blend" scale="1" zPosition="{}" />'.format(
                name, x(left), y(top), x(item_width), y(item_height), path, z,
            )
        )

    def label(name, left, top, item_width, item_height, text_size=25,
              color="#F8FAFC", background=None, align="left", z=3,
              nowrap=True):
        background_xml = (
            'backgroundColor="{}" transparent="0"'.format(background)
            if background else 'transparent="1"'
        )
        parts.append(
            '<widget name="{}" position="{},{}" size="{},{}" '
            'font="Regular;{}" foregroundColor="{}" {} zPosition="{}" '
            'halign="{}" valign="center" noWrap="{}" />'.format(
                name, x(left), y(top), x(item_width), y(item_height),
                font(text_size), color, background_xml, z, align,
                "1" if nowrap else "0",
            )
        )

    pixmap("header_art", 35, 28, 1850, 82, glass_row, z=1)
    pixmap("left_panel_art", 35, 130, 990, 820, glass_panel, z=1)
    pixmap("preview_panel_art", 1040, 130, 845, 820, glass_panel, z=1)
    pixmap(
        "status_badge_art", 1500, 43, 350, 54,
        plugin_path("skin", "images", "status-neutral-r64.png"), z=2,
    )
    label("header", 75, 31, 1395, 76, 36)
    label("status_dot", 1518, 43, 34, 54, 25, "#22C55E", align="center", z=4)
    label("status_badge", 1555, 43, 275, 54, 19, "#D8E6F4",
          align="center", z=4)
    label("settings_title", 80, 143, 880, 48, 27, "#CFE5FF")
    label("appearance_title", 80, 646, 880, 44, 26, "#CFE5FF")
    label("preview_title", 1080, 143, 740, 48, 27, "#CFE5FF")

    for index in range(ROWS):
        top = (196 + index * 40) if index < 11 else (697 + (index - 11) * 45)
        row_height = 37 if index < 11 else 41
        text_height = row_height - 2
        pixmap("row_art_{}".format(index), 62, top, 936, row_height, glass_row, z=2)
        label("focus_{}_top".format(index), 62, top, 936, 2, 1,
              background="#00E5FF", z=7)
        label("focus_{}_bottom".format(index), 62, top + row_height - 2, 936, 2, 1,
              background="#C33BEE", z=7)
        label("focus_{}_left".format(index), 62, top, 2, row_height, 1,
              background="#00E5FF", z=7)
        label("focus_{}_right".format(index), 996, top, 2, row_height, 1,
              background="#C33BEE", z=7)
        label("label_{}".format(index), 92, top + 1, 500, text_height, 21, z=5)
        label("arrow_left_{}".format(index), 605, top + 1, 42, text_height, 27,
              "#8EDCFF", align="center", z=5)
        label("value_{}".format(index), 650, top + 1, 292, text_height, 20,
              "#DCEAFF", align="center", z=5)
        label("arrow_right_{}".format(index), 946, top + 1, 42, text_height, 27,
              "#8EDCFF", align="center", z=5)

    label("preview_caption", 1090, 183, 715, 30, 18, "#8298B8",
          align="center", z=3)
    label("screen_frame", 1070, 215, 785, 630, 1,
          background="#17679D", z=2)
    label("screen_body", 1075, 220, 775, 620, 1,
          background="#020A18", z=3)
    label("preview_text", 1120, 690, 685, 110, 38, "#F8FAFC",
          background="#A0000000", align="center", z=4, nowrap=False)
    label("preview_detail", 1110, 805, 705, 42, 18, "#94A3B8",
          align="center", z=4, nowrap=False)
    pixmap("message_art", 1070, 870, 785, 55, glass_row, z=2)
    label("message", 1090, 872, 745, 51, 20, "#67E8F9", align="center", z=4)
    label("footer", 0, 962, 1920, 108, 1)
    parts.append("</screen>")
    return decorate_remote_footer(
        "\n".join(parts), FOOTER, transparent_panel=False,
        show_dividers=False, stacked=True, skin_fonts_scaled=True,
        panel_height=100, content_y_offset=8,
    )


def _preview_skin(settings):
    width, height, px = _scale()
    y = {
        "bottom": 790,
        "lower": 675,
        "middle": 455,
    }.get(settings.vertical_position, 790)
    size = {"small": 32, "medium": 40, "large": 48,
            "extra_large": 58}.get(settings.font_size, 40)
    color = {"white": "#F8FAFC", "yellow": "#FDE047",
             "cyan": "#67E8F9"}.get(settings.font_color, "#F8FAFC")
    background = {"transparent_dark": "#A0000000",
                  "solid_dark": "#020617"}.get(settings.background)
    parts = [
        '<screen name="GTSubtitlePreviewScreen" position="0,0" size="{},{}" '
        'flags="wfNoBorder" backgroundColor="#020617">'.format(width, height)
    ]
    _label(parts, px, "title", 70, 45, 1780, 70, 38, "#F8FAFC", align="center")
    _label(parts, px, "hint", 100, 130, 1720, 58, 23, "#94A3B8", align="center")
    _label(parts, px, "sample", 160, y, 1600, 150, size, color,
           background=background, align="center", nowrap=False)
    _label(parts, px, "footer", 0, 982, 1920, 98, 24, background="#080E1A",
           align="center")
    parts.append("</screen>")
    return decorate_remote_footer(
        "\n".join(parts), PREVIEW_FOOTER, transparent_panel=True,
        skin_fonts_scaled=True, panel_height=92, bottom_padding=0,
    )


def _language_choices():
    output = []
    seen = set()
    for code, name in supported_language_names():
        base = code.split("_", 1)[0]
        if base not in seen:
            output.append((base, name))
            seen.add(base)
    return tuple(output)


LANGUAGE_CHOICES = _language_choices()
LANGUAGE_NAMES = dict(LANGUAGE_CHOICES)


class GTSubtitlePreviewScreen(Screen):
    def __init__(self, session, settings):
        if Label is None or ActionMap is None:
            raise RuntimeError("Enigma2 subtitle preview widgets are unavailable")
        self.skin = _preview_skin(settings)
        Screen.__init__(self, session)
        self["title"] = Label(_("Independent subtitle preview"))
        self["hint"] = Label(_("This preview uses the current unsaved appearance settings."))
        self["sample"] = Label(
            _("This is a subtitle preview.\nChanges appear here immediately.")
        )
        self["footer"] = Label("")
        install_remote_footer(self, PREVIEW_FOOTER)
        self["actions"] = ActionMap(
            ["OkCancelActions", "ColorActions"],
            {"ok": self.close, "cancel": self.close, "back": self.close,
             "red": self.close},
            -1,
        )
        self.setTitle(_("Independent subtitle preview"))


class GTSubtitleSettingsScreen(Screen):
    LABELS = (
        N_("Independent subtitles"),
        N_("Provider"),
        N_("SubDL API key"),
        N_("SubDL API key"),
        N_("SubDL API key"),
        N_("Enter the username"),
        N_("Enter the password"),
        N_("Primary language"),
        N_("Secondary language"),
        N_("Search mode"),
        N_("Hearing-impaired subtitles"),
        N_("Font size"),
        N_("Font color"),
        N_("Background"),
        N_("Vertical position"),
        N_("Timing offset"),
    )

    def __init__(self, session, settings_loader=None, settings_saver=None,
                 client_factory=None):
        if Label is None or Pixmap is None or ActionMap is None or eTimer is None:
            raise RuntimeError("Enigma2 subtitle settings widgets are unavailable")
        self.skin = _settings_skin()
        Screen.__init__(self, session)
        self.settings_loader = settings_loader or load_subtitle_settings
        self.settings_saver = settings_saver or save_subtitle_settings
        self.client_factory = client_factory
        try:
            self.settings = self.settings_loader().copy()
        except Exception:
            self.settings = load_subtitle_settings().copy()
        self.selected_index = 0
        self._closed = False
        self._api_pending = False
        self._api_result = None
        self._api_generation = 0
        self._api_test_provider = self.settings.provider
        self._api_lock = threading.Lock()
        self._api_timer = eTimer()
        _connect_timer(self._api_timer, self._poll_api_test)

        attach_background(self, "app_bg", BACKGROUND_PATH)
        for name in (
            "header_art", "left_panel_art", "preview_panel_art",
            "status_badge_art", "message_art",
        ):
            self[name] = Pixmap()
        self["video_guard"] = Label("")
        self["header"] = Label(
            "GT IPTV PLAYER PRO  |  {}  ›  {}".format(
                localized_upper(_("Settings")),
                localized_upper(_("Subtitles")),
            )
        )
        self["status_dot"] = Label("●")
        self["status_badge"] = Label("")
        self["settings_title"] = Label(
            localized_upper(_("Independent subtitles"))
        )
        self["appearance_title"] = Label(localized_upper(_("Appearance")))
        self["preview_title"] = Label(localized_upper(_("LIVE PREVIEW")))
        for index, label in enumerate(self.LABELS):
            self["row_art_{}".format(index)] = Pixmap()
            for side in ("top", "bottom", "left", "right"):
                self["focus_{}_{}".format(index, side)] = Label("")
            self["label_{}".format(index)] = Label(_(label))
            self["value_{}".format(index)] = Label("")
            self["arrow_left_{}".format(index)] = Label("‹")
            self["arrow_right_{}".format(index)] = Label("›")
        self["preview_caption"] = Label(_("Television preview"))
        self["screen_frame"] = Label("")
        self["screen_body"] = Label("")
        self["preview_text"] = Label(
            _("This is a subtitle preview.\nChanges appear here immediately.")
        )
        self["preview_detail"] = Label("")
        self["message"] = Label(_("Press GREEN to save the changes."))
        self["footer"] = Label("")
        install_remote_footer(self, FOOTER, stacked=True)
        self["actions"] = ActionMap(
            ["OkCancelActions", "DirectionActions", "ColorActions"],
            {
                "up": lambda: self.move(-1),
                "down": lambda: self.move(1),
                "left": lambda: self.change(-1),
                "right": lambda: self.change(1),
                "ok": self.activate,
                "green": self.save,
                "yellow": self.test_api,
                "blue": self.preview,
                "red": self.cancel,
                "cancel": self.cancel,
                "back": self.cancel,
            },
            -1,
        )
        if hasattr(self, "onLayoutFinish"):
            self.onLayoutFinish.append(self._layout_ready)
        if hasattr(self, "onClose"):
            self.onClose.append(self._stop)
        self._refresh()
        self.setTitle(_("Independent subtitles"))

    def _layout_ready(self):
        self["video_guard"].show()
        for name in (
            "header_art", "left_panel_art", "preview_panel_art",
            "status_badge_art", "message_art",
        ):
            self[name].show()
        for index in range(ROWS):
            self["row_art_{}".format(index)].show()
        self._refresh()

    def _stop(self):
        self._closed = True
        try:
            self._api_timer.stop()
        except Exception:
            pass

    def _api_status(self):
        if self._api_pending:
            return _("API CHECKING")
        if self._api_result is True:
            return _("API READY")
        if self._api_result is False:
            return _("API ERROR")
        if self.settings.credentials_configured():
            return _("API KEY SAVED")
        return _("API KEY REQUIRED")

    def _apply_api_status_style(self):
        instance = getattr(self["status_dot"], "instance", None)
        if instance is None or parseColor is None:
            return
        if self._api_pending:
            color = "#FACC15"
        elif self._api_result is True:
            color = "#22C55E"
        elif self._api_result is False:
            color = "#EF4444"
        elif self.settings.credentials_configured():
            color = "#22C55E"
        else:
            color = "#94A3B8"
        try:
            instance.setForegroundColor(parseColor(color))
        except Exception:
            pass

    def _provider_text(self, message, provider=None):
        provider = provider if provider in PROVIDERS else self.settings.provider
        return _(message).replace(
            "SubDL", provider_display_name(provider)
        )

    def _client(self, settings=None):
        settings = settings or self.settings
        if callable(self.client_factory):
            return self.client_factory(settings.api_key)
        return subtitle_client(settings)

    def _key_provider(self, index):
        """Keep the selected service's key immediately below Provider.

        Labels, values, keyboard callbacks and connection tests must all use
        this same row mapping when changing providers. The other services
        remain editable below it without moving or overwriting their keys.
        """
        if index not in (2, 3, 4):
            return None
        selected = self.settings.provider
        providers = (selected,) + tuple(
            provider for provider in PROVIDERS if provider != selected
        )
        return providers[index - 2]

    def _value(self, index):
        if index == 0:
            return _("On") if self.settings.enabled else _("Off")
        if index == 1:
            return provider_display_name(self.settings.provider)
        provider = self._key_provider(index)
        if provider is not None:
            key = getattr(self.settings, provider + "_api_key", "")
            return (_("Configured") + " ••••" + key[-4:]) if key else _("Not configured")
        if index == 5:
            return (
                _("Configured")
                if self.settings.opensubtitles_username
                else _("Not configured")
            )
        if index == 6:
            return (
                _("Configured")
                if self.settings.opensubtitles_password
                else _("Not configured")
            )
        if index == 7:
            return LANGUAGE_NAMES.get(self.settings.primary_language,
                                      self.settings.primary_language.upper())
        if index == 8:
            if not self.settings.secondary_language:
                return _("Off")
            return LANGUAGE_NAMES.get(self.settings.secondary_language,
                                      self.settings.secondary_language.upper())
        if index == 9:
            return _("Automatic") if self.settings.search_mode == "automatic" else _("Manual")
        if index == 10:
            return _("Show") if self.settings.hearing_impaired else _("Hide")
        if index == 11:
            return {"small": _("Small"), "medium": _("Medium"),
                    "large": _("Large"), "extra_large": _("Extra large")}.get(
                        self.settings.font_size, _("Medium"))
        if index == 12:
            return {"white": _("White"), "yellow": _("Yellow"),
                    "cyan": _("Cyan")}.get(self.settings.font_color, _("White"))
        if index == 13:
            return {"transparent_dark": _("Transparent dark"),
                    "solid_dark": _("Solid dark"), "none": _("None")}.get(
                        self.settings.background, _("Transparent dark"))
        if index == 14:
            return {"bottom": _("Bottom"), "lower": _("Lower"),
                    "middle": _("Middle")}.get(
                        self.settings.vertical_position, _("Bottom"))
        return _("{:+.1f} seconds").format(self.settings.offset_ms / 1000.0)

    def _apply_preview_style(self):
        widget = self["preview_text"]
        instance = getattr(widget, "instance", None)
        if instance is None:
            return
        width, height = _desktop_size()
        scale = min(float(width) / 1920.0, float(height) / 1080.0)
        sx = float(width) / 1920.0
        sy = float(height) / 1080.0

        def px(value):
            return max(1, int(round(float(value) * scale)))

        def screen_x(value):
            return max(0, int(round(float(value) * sx)))

        def screen_y(value):
            return max(0, int(round(float(value) * sy)))

        size = {"small": 32, "medium": 40, "large": 48,
                "extra_large": 58}.get(self.settings.font_size, 40)
        color = {"white": "#F8FAFC", "yellow": "#FDE047",
                 "cyan": "#67E8F9"}.get(self.settings.font_color, "#F8FAFC")
        try:
            instance.setFont(gFont("Regular", font_px(px, size)))
        except Exception:
            pass
        if parseColor is not None:
            try:
                instance.setForegroundColor(parseColor(color))
            except Exception:
                pass
            background = {"transparent_dark": "#A0000000",
                          "solid_dark": "#020617"}.get(self.settings.background)
            try:
                if background:
                    instance.setBackgroundColor(parseColor(background))
                    instance.setTransparent(0)
                else:
                    instance.setTransparent(1)
            except Exception:
                pass
        y = {"bottom": 690, "lower": 585, "middle": 430}.get(
            self.settings.vertical_position, 690
        )
        try:
            instance.move(ePoint(screen_x(1120), screen_y(y)))
        except Exception:
            pass

    def _refresh(self):
        for name in (
            "header", "settings_title", "appearance_title", "preview_title",
        ):
            fit_dynamic_text(
                self[name], self[name].getText(), max_lines=1,
            )
        fit_dynamic_text(
            self["status_badge"], localized_upper(self._api_status()),
            max_lines=1,
        )
        self._apply_api_status_style()
        for index in (2, 3, 4):
            self["label_{}".format(index)].setText(
                self._provider_text("SubDL API key", self._key_provider(index))
            )
        self["label_5"].setText(
            "OpenSubtitles.com • " + _("Enter the username")
        )
        self["label_6"].setText(
            "OpenSubtitles.com • " + _("Enter the password")
        )
        for index in range(ROWS):
            fit_dynamic_text(
                self["label_{}".format(index)],
                self["label_{}".format(index)].getText(),
                max_lines=1,
            )
            fit_dynamic_text(
                self["value_{}".format(index)], self._value(index),
                max_lines=1,
            )
            for side in ("top", "bottom", "left", "right"):
                focus = self["focus_{}_{}".format(index, side)]
                focus.show() if index == self.selected_index else focus.hide()
        self["preview_detail"].setText(
            "{}  •  {}  •  {}".format(
                self._value(11), self._value(13), self._value(14)
            )
        )
        self._apply_preview_style()

    def move(self, step):
        self.selected_index = (self.selected_index + int(step)) % ROWS
        self._refresh()

    @staticmethod
    def _cycled(current, choices, step):
        try:
            index = choices.index(current)
        except ValueError:
            index = 0
        return choices[(index + int(step)) % len(choices)]

    def change(self, step):
        index = self.selected_index
        if index == 0:
            self.settings.enabled = not self.settings.enabled
        elif index == 1:
            self.settings.provider = self._cycled(
                self.settings.provider, PROVIDERS, step
            )
            self._api_generation += 1
            self._api_pending = False
            self._api_result = None
        elif self._key_provider(index) is not None:
            self.edit_api_key(self._key_provider(index))
            return
        elif index == 5:
            self.edit_username()
            return
        elif index == 6:
            self.edit_password()
            return
        elif index in (7, 8):
            choices = tuple(code for code, unused_name in LANGUAGE_CHOICES)
            if index == 8:
                choices = ("",) + choices
            field = "primary_language" if index == 7 else "secondary_language"
            setattr(self.settings, field, self._cycled(
                getattr(self.settings, field), choices, step
            ))
        elif index == 9:
            self.settings.search_mode = self._cycled(
                self.settings.search_mode, SEARCH_MODES, step
            )
        elif index == 10:
            self.settings.hearing_impaired = not self.settings.hearing_impaired
        elif index == 11:
            self.settings.font_size = self._cycled(
                self.settings.font_size, FONT_SIZES, step
            )
        elif index == 12:
            self.settings.font_color = self._cycled(
                self.settings.font_color, FONT_COLORS, step
            )
        elif index == 13:
            self.settings.background = self._cycled(
                self.settings.background, BACKGROUND_STYLES, step
            )
        elif index == 14:
            self.settings.vertical_position = self._cycled(
                self.settings.vertical_position, VERTICAL_POSITIONS, step
            )
        else:
            self.settings.offset_ms = max(
                -30000, min(30000, self.settings.offset_ms + int(step) * 100)
            )
        self["message"].setText(_("Press GREEN to save the changes."))
        self._refresh()

    def activate(self):
        provider = self._key_provider(self.selected_index)
        if provider is not None:
            self.edit_api_key(provider)
        elif self.selected_index == 5:
            self.edit_username()
        elif self.selected_index == 6:
            self.edit_password()
        else:
            self.change(1)

    def edit_api_key(self, provider=None):
        provider = provider if provider in PROVIDERS else self.settings.provider
        try:
            from Screens.VirtualKeyBoard import VirtualKeyBoard
        except ImportError:
            self["message"].setText(_("The virtual keyboard is unavailable on this image."))
            return
        opener = getattr(self.session, "openWithCallback", None)
        if not callable(opener):
            self["message"].setText(_("Could not open the virtual keyboard."))
            return
        try:
            key = getattr(self.settings, provider + "_api_key", "")
            opener(
                lambda value: self._api_key_entered(provider, value),
                VirtualKeyBoard,
                title=self._provider_text("Enter the SubDL API key", provider),
                text=key,
            )
        except Exception:
            self["message"].setText(_("Could not open the virtual keyboard."))

    def _api_key_entered(self, provider, value):
        if value is None:
            self["message"].setText(
                self._provider_text("SubDL key change cancelled.", provider)
            )
            return
        key = "".join(str(value or "").split())[:300]
        if provider not in PROVIDERS:
            return
        setattr(self.settings, provider + "_api_key", key)
        self._api_generation += 1
        self._api_pending = False
        self._api_result = None
        self["message"].setText(
            self._provider_text(
                "Press YELLOW to test the SubDL connection.", provider
            )
            if key
            else self._provider_text("The SubDL API key was cleared.", provider)
        )
        self._refresh()

    def _open_credential_keyboard(self, callback, title, text=""):
        try:
            from Screens.VirtualKeyBoard import VirtualKeyBoard
        except ImportError:
            self["message"].setText(_("The virtual keyboard is unavailable on this image."))
            return
        opener = getattr(self.session, "openWithCallback", None)
        if not callable(opener):
            self["message"].setText(_("Could not open the virtual keyboard."))
            return
        try:
            opener(callback, VirtualKeyBoard, title=title, text=text)
        except Exception:
            self["message"].setText(_("Could not open the virtual keyboard."))

    def edit_username(self):
        self._open_credential_keyboard(
            self._username_entered,
            _("Enter the username"),
            self.settings.opensubtitles_username,
        )

    def edit_password(self):
        # Do not place the saved password in a text widget or keyboard buffer.
        self._open_credential_keyboard(
            self._password_entered, _("Enter the password"), ""
        )

    def _username_entered(self, value):
        if value is None:
            return
        value = str(value or "").strip()[:160]
        self.settings.opensubtitles_username = (
            "" if any(ord(character) < 32 for character in value) else value
        )
        self._credentials_changed()

    def _password_entered(self, value):
        if value is None:
            return
        value = str(value or "")[:300]
        self.settings.opensubtitles_password = (
            "" if any(ord(character) < 32 for character in value) else value
        )
        self._credentials_changed()

    def _credentials_changed(self):
        self._api_generation += 1
        self._api_pending = False
        self._api_result = None
        self["message"].setText(
            self._provider_text(
                "Press YELLOW to test the SubDL connection.", "opensubtitles"
            )
        )
        self._refresh()

    def test_api(self):
        if self._api_pending:
            self["message"].setText(
                self._provider_text("The SubDL connection test is still running...", self._api_test_provider)
            )
            return
        # YELLOW on a key row checks that service without changing the
        # user's saved provider preference.
        provider = self._key_provider(self.selected_index) or self.settings.provider
        test_settings = self.settings.copy()
        test_settings.provider = provider
        if not test_settings.credentials_configured():
            if provider == "opensubtitles":
                self["message"].setText(_("API KEY REQUIRED"))
            else:
                self["message"].setText(self._provider_text("Enter a SubDL API key first.", provider))
            return
        self._api_pending = True
        self._api_result = None
        self._api_generation += 1
        generation = self._api_generation
        self._api_test_provider = provider
        self["message"].setText(
            self._provider_text("Testing the SubDL connection...", provider)
        )
        self._refresh()

        def worker():
            result = True
            try:
                self._client(test_settings).test()
            except Exception:
                result = False
            with self._api_lock:
                if generation == self._api_generation and not self._closed:
                    self._api_result = result

        thread = threading.Thread(target=worker)
        thread.daemon = True
        thread.start()
        self._api_timer.start(100, True)

    def _poll_api_test(self):
        if self._closed or not self._api_pending:
            return
        with self._api_lock:
            result = self._api_result
        if result is None:
            self._api_timer.start(100, True)
            return
        self._api_pending = False
        self["message"].setText(
            self._provider_text("SubDL connection is ready.", self._api_test_provider)
            if result
            else self._provider_text(
                "SubDL connection failed. Check the API key and network.", self._api_test_provider
            )
        )
        self._refresh()

    def preview(self):
        self.session.open(GTSubtitlePreviewScreen, self.settings.copy())

    def save(self):
        try:
            saved = bool(self.settings_saver(self.settings))
        except Exception:
            saved = False
        if saved:
            self.close(True)
            return
        self["message"].setText(_("Subtitle settings could not be saved."))

    def cancel(self):
        self.close(False)
