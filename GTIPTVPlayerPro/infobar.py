# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Compact native infobar layout and decoder-local stream information."""

import math
import re

from .i18n import _, device_language, localized_language_name, localized_upper
from .paths import plugin_path

INFOBAR_HEIGHT = 300
INFOBAR_BACKGROUND = "#00061321"
_EPG_CLOCK = re.compile(r"^\d{1,2}:\d{2}(?:\s*[-–]\s*\d{1,2}:\d{2})?$")
_COMMA_DECIMAL = frozenset(("bg", "ca", "cs", "da", "de", "el", "es", "et", "fi",
                           "fr", "fy", "gl", "hr", "hu", "is", "it", "lt", "lv",
                           "mk", "nb", "nl", "nn", "pl", "pt", "ro", "ru", "sk",
                           "sl", "sq", "sr", "sv", "tr", "uk", "vi"))


def stream_rate_mbps(service):
    """Read the active decoder's input rate; unavailable never means zero.

    Enigma2 exposes iStreamBufferInfo on newer images and a five-element
    tuple on older ones. GStreamer's avg-in-rate is bytes/s (gstqueue2.c).
    Only its input rate is used; output rate, nominal codec tags and device
    network counters cannot stand in for this stream's measured throughput.
    """
    try:
        streamed = service.streamed() if service is not None else None
        charge = streamed.getBufferCharge() if streamed is not None else None
        getter = getattr(charge, "getAverageInputRate", None)
        if callable(getter):
            rate = getter()
        elif isinstance(charge, (tuple, list)) and len(charge) >= 5:
            # OpenPLi's python_service.i can return PyTuple_New(5) with NULL
            # slots when the decoder has no buffer info yet. Indexing or
            # iterating that tuple segfaults before Python can catch anything.
            # CPython repr safely spells these slots <NULL> (3.9 through 3.14).
            if "<NULL>" in repr(charge):
                return None
            rate = charge[1]
        else:
            return None
        if isinstance(rate, (bool, str, bytes)):
            return None
        rate = float(rate)
        if not math.isfinite(rate) or not 0 < rate <= 12500000000:
            return None
        return rate * 8.0 / 1000000.0
    except Exception:
        # A stopped/unsupported service is ordinary during zap/engine change.
        return None


def stream_rate_text(value):
    if value is None:
        number = "--"
    else:
        number = "{:.1f}".format(value)
        if device_language().split("_", 1)[0] in _COMMA_DECIMAL:
            number = number.replace(".", ",")
    return "{}  {} Mbps".format(localized_upper(_("Stream")), number)


def network_rate_text(value, compact=False):
    if value is None:
        number = "--"
    else:
        number = "{:.1f}".format(value)
        if device_language().split("_", 1)[0] in _COMMA_DECIMAL:
            number = number.replace(".", ",")
    label = _("Net") if compact else _("Network speed")
    return "{}  {} Mbps".format(localized_upper(label), number)


def remaining_time_text(seconds):
    minutes = max(0, int(math.ceil(float(seconds) / 60.0)))
    hours, minutes = divmod(minutes, 60)
    value = (_("{} h {} min").format(hours, minutes) if hours
             else _("{} min").format(minutes))
    return _("Remaining: {}").format(value)


def split_epg_text(value):
    value = str(value or "")
    clock, separator, title = value.partition("  ")
    if separator and _EPG_CLOCK.fullmatch(clock.strip()):
        return clock.strip().replace("-", " – "), title.strip()
    return "", value


def audio_language_text(service):
    language = "--"
    try:
        tracks = service.audioTracks() if service is not None else None
        if tracks is not None and tracks.getNumberOfTracks() > 0:
            index = tracks.getCurrentTrack()
            if index >= 0:
                track = tracks.getTrackInfo(index)
                raw = str(track.getLanguage() or "").split("/", 1)[0].strip()
                if raw and raw.lower() not in ("und", "unknown", "n/a", "--"):
                    language = localized_language_name(raw)
    except Exception:
        pass
    return _("Audio: {}").format(language)


def subtitle_status_text(controller):
    state = "--"
    if controller is not None:
        try:
            enabled = bool(controller.selected_track is not None or controller.external_loaded)
            state = _("On") if enabled else _("Off")
        except Exception:
            pass
    return _("Subtitles: {}").format(state)


def source_account_text(client):
    account = getattr(client, "account", None) or getattr(client, "source", None)
    # A Stalker account's display_name includes a masked MAC. The simple
    # host/name is enough in this small banner and never exposes login URLs.
    value = getattr(account, "name", "") or getattr(account, "host", "")
    value = " ".join(str(value or "--").replace("\n", " ").split())[:96]
    return _("Source: {}").format(value)


def infobar_panel_path(width, live=True):
    variant = ("sd640" if width <= 640 else "sd720" if width <= 720
               else "720" if width <= 1280 else "1080" if width <= 1920 else "2160")
    return plugin_path("skin", "images", "infobar-r52-panel-{}-{}.png".format("live" if live else "vod", variant))


def infobar_progress_geometry(width, px):
    return max(1, width - px(1030)), max(1, px(6))


def infobar_skin(width, height, px, live=True):
    """300 design pixels at 1080p, with resolution-scaled native controls."""
    bar_h = px(INFOBAR_HEIGHT)
    widgets = []

    def label(name, x, y, w, h, font=20, color="#E2E8F0", align="left", z=3,
              background=None):
        # Blend glyphs into the painted panel. Without RT_BLEND, Enigma2's
        # font renderer uses the transparent screen color for antialiasing
        # and overwrites panel pixels with translucent glyph pixels.
        extra = ('backgroundColor="{}" transparent="0"'.format(background)
                 if background else 'transparent="1" alphatest="blend"')
        widgets.append(
            '<widget name="{}" position="{},{}" size="{},{}" font="Regular;{}" '
            'foregroundColor="{}" {} valign="center" halign="{}" noWrap="1" zPosition="{}" />'.format(
                name, px(x) if x else 0, px(y) if y else 0, px(w), px(h), px(font), color, extra, align, z))

    def pixmap(name, x, y, w, h, path="", z=4):
        asset = 'pixmap="{}"'.format(path.replace('&', '&amp;').replace('"', '&quot;')) if path else ''
        widgets.append('<widget name="{}" position="{},{}" size="{},{}" {} alphatest="blend" scale="1" zPosition="{}" />'.format(
            name, px(x) if x else 0, px(y) if y else 0, px(w), px(h), asset, z))

    # Only this single PNG layer supplies the normal panel's ~90% opacity.
    # Its background reaches every edge without an outline or clear gaps.
    # Text uses separate opaque native labels, never global window alpha.
    widgets.append('<widget name="info_background" position="0,0" size="{},{}" pixmap="{}" alphatest="blend" scale="1" zPosition="1" />'.format(
        width, bar_h, infobar_panel_path(width, live=live)))
    for name in ("top_line", "bottom_line", "picon_frame", "picon_inner", "picon_placeholder",
                 "number_badge_frame", "number_badge_inner", "number_divider", "epg_separator",
                 "epg_bottom_separator", "weather_card", "shortcut_audio_dot", "shortcut_aspect_dot",
                 "shortcut_web_key", "web_shortcut_hint"):
        label(name, 0, 0, 1, 1, font=1, z=1)
    pixmap("picon", 30, 53, 170, 170)
    # The common overlay also hosts YouTube videos. These widgets remain
    # hidden for provider content; the local logo covers missing thumbnails.
    pixmap("youtube_logo", 43, 88, 144, 100,
           plugin_path("skin", "images", "yt-logo-r44-2160.png"))
    label("youtube_number_mask", 240, 12, 90, 54, font=1, z=2,
          background=INFOBAR_BACKGROUND)
    label("channel_number", 247, 16, 76, 46, font=34, color="#FFFFFF", align="center", z=4)
    label("channel_name", 345, 13, 535 if live else 840, 49, font=32, color="#FFFFFF")
    # Keep source details with the technical group so live names can use the
    # full header space before the resolution badge.
    label("account_source", 900 if live else 250, 55 if live else 72,
          500 if live else 420, 24 if live else 36, font=19)
    chip_y = 21 if live else 74
    # Cover the old painted badges/frame before drawing the wider audio
    # caption and the relocated network field as native labels.
    if live:
        label("info_chip_background", 900, 18, 714, 40, font=1, z=2,
              background=INFOBAR_BACKGROUND)
    else:
        label("info_chip_background", 1280, 18, 334, 40, font=1, z=2,
              background=INFOBAR_BACKGROUND)
    chip_x = (900, 1036, 1114, 1196) if live else (690, 791, 909, 1027)
    chip_w = (128, 70, 74, 204) if live else (92, 110, 110, 204)
    for name, x, w in zip(("resolution", "fps", "video_codec", "audio_codec"), chip_x, chip_w):
        label(name, x, chip_y, w, 34, font=19, align="center",
              background="#132B3D" if live or name == "audio_codec" else None)
    frame_border = px(1)
    widgets.append('<widget name="stream_bitrate_frame" position="{},{}" size="{},{}" '
                   'font="Regular;1" backgroundColor="#22D3EE" transparent="0" zPosition="3" />'.format(
                       px(1412) - frame_border, px(19) - frame_border,
                       px(200) + frame_border * 2, px(38) + frame_border * 2))
    label("stream_bitrate", 1412, 19, 200, 38, font=20, color="#22D3EE", align="center",
          z=4, background=INFOBAR_BACKGROUND)
    # Preserve old skin component names without showing status captions.
    label("audio_language", 0, 0, 1, 1, font=1, z=1)
    label("subtitle_status", 0, 0, 1, 1, font=1, z=1)
    for prefix, x, w in (("cpu", 1624, 126), ("ram", 1762, 132)):
        widgets.append('<widget name="{}_gauge" position="{},{}" size="{},{}" zPosition="4" />'.format(
            prefix, px(x), px(19), px(w), px(38)))
        # A smaller SD icon leaves room for the full CPU/RAM 100% caption.
        inset = 24 if width <= 720 else 36
        label(prefix + "_usage", x + inset, 19, w - inset - 6, 38, font=19, z=5)
    label("epg_now_label", 250, 83 if live else 120, 188, 29, font=22, color="#22D3EE")
    label("epg_now_time", 250, 112, 188, 27, font=19)
    label("epg_now", 465, 80 if live else 110, 850, 53, font=36, color="#FFFFFF")
    label("epg_next_label", 250, 155 if live else 165, 188, 25, font=19)
    label("epg_next_time", 250, 181, 188, 25, font=19)
    label("epg_next", 465, 151 if live else 156, 850, 43, font=26)
    progress_w, progress_h = infobar_progress_geometry(width, px)
    for name, color, z in (("player_progress_bg", "#26344E", 2), ("player_progress_fill", "#22D3EE", 3)):
        widgets.append('<widget name="{}" position="{},{}" size="{},{}" font="Regular;1" backgroundColor="{}" transparent="0" zPosition="{}" />'.format(
            name, px(338), px(239), progress_w, progress_h, color, z))
    label("player_progress_start", 250, 226, 78, 27, font=18)
    label("player_progress_end", 1240, 226, 82, 27, font=18, align="right")
    label("player_progress_text", 970, 203, 352, 28, font=20, align="right")
    label("weather_divider", 1340, 80, 1, 168, font=1, z=2, background="#22D3EE")
    label("favorite_badge", 30, 225, 170, 27, font=17, color="#22D3EE", align="center", z=4)
    label("weather_city", 1362, 83, 272, 27, font=18, align="center")
    pixmap("weather_icon", 1365, 120, 84, 84)
    label("weather_temp", 1455, 116, 170, 67, font=46, color="#FFFFFF")
    label("weather_desc", 1370, 195, 260, 33, font=20, align="center")
    label("clock_divider", 1650, 80, 1, 168, font=1, background="#22D3EE")
    label("clock", 1669, 88, 161, 73, font=52, color="#FFFFFF", align="right")
    label("clock_seconds", 1832, 108, 62, 50, font=30, color="#FFFFFF")
    label("date", 1665, 163, 233, 35, font=18, align="center")
    label("engine", 1665, 207, 233, 29, font=17, align="center")
    label("shortcut_separator", 15, 260, 1890, 1, font=1, z=2, background="#0E7490")
    label("shortcut_epg_key", 35, 267, 80, 27, font=19, color="#FFFFFF", align="center")
    label("shortcut_epg", 132, 266, 365, 29, font=20)
    label("shortcut_audio", 607, 266, 377, 29, font=20)
    label("shortcut_aspect", 1042, 266, 380, 29, font=20)
    # Cover the unused MENU badge painted into the existing panel artwork.
    # Keep the old text components as empty compatibility widgets above.
    label("web_shortcut_background", 1478, 264, 164, 34, font=1, z=2,
          background=INFOBAR_BACKGROUND)
    # Seeking remains an independent high-z overlay over every normal field.
    full_width = max(1, width - px(160))
    widgets.append('<widget name="seek_panel" position="0,0" size="{},{}" font="Regular;1" backgroundColor="{}" transparent="0" zPosition="20" />'.format(width, bar_h, INFOBAR_BACKGROUND))
    for name, y, h, font, color, align in (
        ("seek_state", 28, 56, 34, "#FFFFFF", "left"),
        ("seek_times", 130, 52, 25, "#22D3EE", "center"),
        ("seek_hint", 208, 48, 20, "#E2E8F0", "center")):
        widgets.append('<widget name="{}" position="{},{}" size="{},{}" font="Regular;{}" foregroundColor="{}" transparent="1" alphatest="blend" zPosition="21" valign="center" halign="{}" />'.format(name, px(80), px(y), full_width, px(h), px(font), color, align))
    for name, color, z in (("seek_progress_bg", "#334155", 21), ("seek_progress_fill", "#22D3EE", 22)):
        widgets.append('<widget name="{}" position="{},{}" size="{},{}" font="Regular;1" backgroundColor="{}" transparent="0" zPosition="{}" />'.format(name, px(80), px(108), full_width, px(14), color, z))
    return '<screen name="GTPlayerInfoOverlay" position="0,{}" size="{},{}" flags="wfNoBorder" backgroundColor="#FF000000" zPosition="100">\n{}\n</screen>'.format(height - bar_h, width, bar_h, '\n'.join(widgets))
