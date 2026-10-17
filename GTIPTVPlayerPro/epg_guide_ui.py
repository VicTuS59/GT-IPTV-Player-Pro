# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Receiver-native day/programme guide sharing the fullscreen decoder."""

import datetime
import threading
import time
import xml.etree.ElementTree as ET

from Components.ActionMap import ActionMap
from Components.Label import Label
from Components.Pixmap import Pixmap
from Components.ProgressBar import ProgressBar
from Screens.Screen import Screen
from enigma import ePoint, eSize, eTimer, getDesktop
try:
    from Components.VideoWindow import VideoWindow
except ImportError:
    VideoWindow = None
try:
    from skin import parseColor
except ImportError:
    parseColor = None

from .epg_guide import clean_events, event_days, load_guide
from .i18n import _, localized_date_text, localized_short_weekday, localized_upper
from .paths import plugin_path
from .remote_footer import decorate_remote_footer, footer_item, install_remote_footer
from .scrollbar import hide_scrollbar, update_scrollbar
from .smooth_scroll import GTSmoothScrollLabel
from .typography import fit_dynamic_text, font_px


def geometry():
    size = getDesktop(0).size()
    width, height = int(size.width()), int(size.height())
    return width, height, lambda n: max(1, int(round(n * width / 1920.0))), lambda n: max(1, int(round(n * height / 1080.0)))


def asset(name):
    width, height, unused_x, unused_y = geometry()
    variant = "2160" if width > 1920 or height > 1080 else "1080" if width > 1280 else "720"
    if width <= 720:
        variant = "sd576" if height > 480 else "sd640" if width <= 640 else "sd480"
    return plugin_path("skin", "images", "epg-r15-{}-{}.png".format(name, variant))


def footer():
    return (footer_item("left_right", _("Day")), footer_item("up_down", _("Programme")),
            footer_item("green", _("Today")), footer_item("yellow", _("Now")),
            footer_item("blue", _("Refresh")), footer_item("exit", _("Return to fullscreen")))


def skin():
    width, height, px, py = geometry()
    scale = lambda n: max(1, int(round(n * min(width / 1920.0, height / 1080.0))))
    parts = ['<screen name="GTEPGGuideScreen" position="0,0" size="{},{}" flags="wfNoBorder" backgroundColor="#050E20">'.format(width, height)]
    def picture(name, x, y, w, h, path=None, z=1):
        source = ' pixmap="{}"'.format(path) if path else ''
        parts.append('<widget name="{}" position="{},{}" size="{},{}" scale="1" alphatest="blend" zPosition="{}"{} />'.format(name, px(x) if x else 0, py(y) if y else 0, px(w), py(h), z, source))
    def label(name, x, y, w, h, font=28, color="#FFFFFF", align="left", z=3):
        parts.append('<widget name="{}" position="{},{}" size="{},{}" font="Regular;{}" foregroundColor="{}" transparent="1" valign="center" halign="{}" zPosition="{}" />'.format(name, px(x), py(y), px(w), py(h), font_px(scale, font, max_height=h), color, align, z))
    picture("app_bg", 0, 0, 1920, 1080, z=0)
    label("brand", 48, 20, 1000, 38, 27, "#C4D7EE")
    label("title", 48, 64, 980, 64, 49)
    label("channel", 1000, 50, 520, 64, 38, align="center")
    label("clock", 1640, 17, 225, 65, 52, align="right")
    label("date", 1480, 88, 385, 38, 25, "#C4D7EE", "right")
    label("days_caption", 53, 171, 267, 46, 35)
    label("days_count", 53, 221, 267, 36, 25, "#AFC4DD")
    label("day_title", 383, 171, 809, 52, 37)
    label("schedule_caption", 383, 226, 809, 35, 27, "#AFC4DD")
    for row in range(7):
        y = 280 + row * 90
        picture("day_focus_{}".format(row), 43, y - 3, 278, 83, asset("day-focus"), z=2)
        label("day_label_{}".format(row), 63, y + 4, 238, 35, 28)
        label("day_date_{}".format(row), 63, y + 40, 238, 30, 23, "#C4D7EE")
    for row in range(8):
        y = 282 + row * 78
        picture("programme_focus_{}".format(row), 365, y - 3, 849, 74, asset("programme-focus"), z=2)
        label("programme_time_{}".format(row), 387, y + 5, 254, 52, 29, "#C4D7EE")
        label("programme_title_{}".format(row), 652, y + 5, 534, 52, 31)
    for prefix, x in (("day_scroll", 328), ("programme_scroll", 1224)):
        for suffix, h, colour in (("track", 620, "#173B5A"), ("thumb", 45, "#7896B8")):
            parts.append('<widget name="{}_{}" position="{},{}" size="{},{}" font="Regular;1" backgroundColor="{}" zPosition="4" />'.format(prefix, suffix, px(x), py(281), px(4), py(h), colour))
    if VideoWindow is not None:
        # The receiver video plane sits underneath the OSD. eVideoWidget
        # inherits eLabel: explicitly clear this rectangle to alpha rather
        # than painting the image's opaque default label background. Keep
        # transparent=0 so the clear also cuts through the wallpaper below.
        parts.append('<widget name="preview" position="{},{}" size="{},{}" backgroundColor="#ff000000" transparent="0" zPosition="1" />'.format(px(1264), py(164), px(610), py(343)))
    else:
        label("preview", 1275, 220, 588, 220, 28, "#AFC4DD", "center")
    label("selected_caption", 1278, 538, 570, 37, 26, "#14DFFA")
    label("programme_title", 1278, 582, 570, 83, 35)
    label("programme_time", 1278, 669, 570, 40, 28)
    label("duration", 1278, 712, 570, 34, 24, "#B7C9E1")
    label("description", 1278, 758, 570, 119, 25, "#D1DFEF")
    parts.append('<widget name="progress" position="{},{}" size="{},{}" foregroundColor="#0AC9F5" backgroundColor="#163D60" borderWidth="0" zPosition="3" />'.format(px(1278), py(889), px(570), py(12)))
    label("programme_state", 1278, 909, 270, 31, 23, "#BCD2E8")
    label("remaining", 1550, 909, 298, 31, 23, "#BCD2E8", "right")
    label("message", 375, 387, 819, 190, 30, "#BDD0E8", "center", z=5)
    label("footer", 0, 984, 1920, 96, 24)
    result = decorate_remote_footer(
        "\n".join(parts) + '</screen>', footer(), transparent_panel=True,
        show_dividers=False, skin_fonts_scaled=True, side_padding=16)
    tree = ET.fromstring(result)
    hint = next((node for node in tree if node.attrib.get("name") == "remote_web_hint"), None)
    if hint is not None:
        hint.set("position", "{},{}".format(px(50), py(958)))
        hint.set("size", "{},{}".format(px(1820), py(18)))
        hint.set("font", "Regular;{}".format(font_px(scale, 17, max_height=18)))
    return ET.tostring(tree, encoding="unicode")


def _connect(timer, callback):
    signal = getattr(timer, "timeout", None)
    if callable(getattr(signal, "connect", None)):
        return signal.connect(callback)
    timer.callback.append(callback)
    return None


class GTEPGGuideScreen(Screen):
    def __init__(self, session, client, item, reference, cached=None, seed_events=None):
        self.skin = skin()
        Screen.__init__(self, session)
        self.client, self.item, self.reference = client, item, reference
        self._closed = False
        self._generation = 0
        self._token = None
        self._mailbox = None
        self._lock = threading.Lock()
        self._pending = False
        self._lazy = False
        self._days = []
        self._events = {}
        self._loaded_days = set()
        self._seed_events = clean_events(seed_events)
        self._select_now_after_load = False
        self.day_index = self.programme_index = 0
        self._description_key = None
        self._started = False
        self._timer = eTimer()
        self._clock_timer = eTimer()
        self._connections = [_connect(self._timer, self._poll), _connect(self._clock_timer, self._tick)]
        self["app_bg"] = Pixmap()
        from .background import attach_background
        attach_background(self, "app_bg", asset("background"), stretch_to_widget=True)
        texts = {
            "brand": "GT IPTV PLAYER PRO", "title": localized_upper(_("EPG programme guide")), "channel": getattr(item, "name", ""),
            "clock": "", "date": "", "days_caption": localized_upper(_("Days")), "days_count": "",
            "day_title": "", "schedule_caption": _("Daily schedule"),
            "selected_caption": localized_upper(_("Selected programme")), "programme_title": "", "programme_time": "",
            "duration": "", "programme_state": "", "remaining": "", "message": _("Loading EPG..."),
        }
        for name, text in texts.items():
            self[name] = Label(text)
        self["description"] = GTSmoothScrollLabel("")
        self["progress"] = ProgressBar()
        self["progress"].setValue(0)
        for row in range(7):
            self["day_focus_{}".format(row)] = Pixmap()
            for kind in ("day_label", "day_date"):
                self["{}_{}".format(kind, row)] = Label("")
        for row in range(8):
            self["programme_focus_{}".format(row)] = Pixmap()
            for kind in ("programme_time", "programme_title"):
                self["{}_{}".format(kind, row)] = Label("")
        for prefix in ("day_scroll", "programme_scroll"):
            self[prefix + "_track"] = Label("")
            self[prefix + "_thumb"] = Label("")
            hide_scrollbar(self, prefix)
        width, height, unused_x, unused_y = geometry()
        self["preview"] = VideoWindow(decoder=0, fb_width=width, fb_height=height) if VideoWindow is not None else Label(_("Live preview is unavailable on this image."))
        self["footer"] = Label("")
        install_remote_footer(self, footer(), slot_count=6, fit_horizontal=True)
        self["actions"] = ActionMap(["OkCancelActions", "DirectionActions", "ColorActions", "InfobarEPGActions", "ChannelSelectBaseActions"], {
            "cancel": self.close_guide, "red": self.close_guide, "showEventInfoSingleEPG": lambda: None,
            # Consume the opening key's release on images/remotes that use
            # INFO or EPGPressed for their EPG button, too.
            "info": lambda: None, "showEventInfo": lambda: None, "showEventView": lambda: None,
            "showEventInfoPlugin": lambda: None, "showEventGuidePlugin": lambda: None,
            "EPGPressed": lambda: None, "InfoPressed": lambda: None, "IPressed": lambda: None,
            "left": lambda: self.change_day(-1), "right": lambda: self.change_day(1),
            "leftRepeated": lambda: self.change_day(-1), "rightRepeated": lambda: self.change_day(1),
            "up": lambda: self.change_programme(-1), "down": lambda: self.change_programme(1),
            "upRepeated": lambda: self.change_programme(-1), "downRepeated": lambda: self.change_programme(1),
            "green": self.today, "yellow": self.now, "blue": self.refresh,
            "nextBouquet": lambda: self.change_programme(8), "prevBouquet": lambda: self.change_programme(-8),
        }, -2)
        self.onLayoutFinish.append(self._layout_ready)
        self.onShown.append(self._shown)
        self.onExecBegin.append(self._preview_geometry)
        self.onClose.append(self._cleanup)
        self.setTitle(_("EPG programme guide"))
        if isinstance(cached, dict) and time.monotonic() - cached.get("at", 0) < 120:
            self._days = list(cached.get("days", ()))
            self._events = dict(cached.get("events", {}))
            self._loaded_days = set(cached.get("loaded", ()))
            self._lazy = bool(cached.get("lazy"))
        self._select_now()

    def _layout_ready(self):
        self._preview_geometry()
        for name in ("title", "channel", "days_caption", "schedule_caption", "selected_caption"):
            fit_dynamic_text(self[name], self[name].getText(), max_lines=1)
        self._render()

    def _shown(self):
        self._preview_geometry()
        self._clock_timer.start(1000, False)
        if not self._started:
            self._started = True
            if not self._days:
                self._load()
            elif self._lazy and self._selected_day() not in self._loaded_days:
                self._load(self._selected_day())
        self._render()

    def _preview_geometry(self):
        if VideoWindow is None or self._closed:
            return
        try:
            unused_w, unused_h, px, py = geometry()
            instance = self["preview"].instance
            instance.resize(eSize(px(610), py(343)))
            instance.move(ePoint(px(1264) + 1, py(164)))
            instance.move(ePoint(px(1264), py(164)))
        except Exception:
            pass

    def _load(self, day=None):
        if self._closed:
            return
        from .browser import _WorkerToken
        if self._token is not None:
            self._token.cancel()
        self._generation += 1
        generation = self._generation
        token = _WorkerToken(45)
        self._token = token
        self._pending = True
        self._requested_day = day
        self["message"].setText(_("Loading EPG..."))
        worker = threading.Thread(target=self._worker, args=(generation, token, day))
        worker.daemon = True
        self._worker_thread = worker
        worker.start()
        self._timer.start(100, True)

    def _worker(self, generation, token, day):
        from .browser import _bounded_client_call
        try:
            batch = _bounded_client_call(self.client, token, lambda: load_guide(self.client, self.item, day))
            error = False
        except Exception:
            batch, error = None, True
        with self._lock:
            if not self._closed and generation == self._generation and token.active():
                self._mailbox = (generation, batch, error)

    def _poll(self):
        if self._closed or not self._pending:
            return
        with self._lock:
            result = self._mailbox
            self._mailbox = None
        if result is None:
            if self._token is not None and self._token.expired():
                self._token.cancel()
                self._pending = False
                self["message"].setText(_("Could not load the programme guide."))
                return
            self._timer.start(100, True)
            return
        generation, batch, error = result
        if generation != self._generation:
            self._timer.start(100, True)
            return
        self._pending = False
        self._token = None
        if error:
            self._render()
            self["message"].setText(_("Could not load the programme guide."))
            return
        selected_day = self._selected_day()
        if self._requested_day is None:
            self._days = list(batch.days)
            self._events = {}
            self._loaded_days = set()
            self._lazy = batch.lazy
        grouped = {}
        for event in clean_events(list(batch.events) + self._seed_events):
            for date in event_days(event):
                grouped.setdefault(date, []).append(event)
        for date, events in grouped.items():
            self._events[date] = clean_events(list(self._events.get(date, ())) + events)
        if batch.requested_day is not None:
            self._loaded_days.add(batch.requested_day)
            self._events.setdefault(batch.requested_day, [])
        if not batch.lazy:
            self._loaded_days.update(self._days)
        self._days = sorted(set(self._days).union(grouped))
        if selected_day in self._days:
            self.day_index = self._days.index(selected_day)
        elif self._requested_day in self._days:
            self.day_index = self._days.index(self._requested_day)
        else:
            self._select_now()
        self.programme_index = min(self.programme_index, max(0, len(self._programmes()) - 1))
        if self._select_now_after_load:
            self._select_now_after_load = False
            self._select_now()
        if self._lazy:
            # Retain the server's complete date list while bounding RAM for
            # visited day pages. Evicted days are fetched again on selection.
            selected = self._selected_day()
            while len(self._events) > 1 and sum(len(events) for events in self._events.values()) > 10000:
                discard = max((day for day in self._events if day != selected), key=lambda day: abs((day - selected).days))
                self._events.pop(discard, None)
                self._loaded_days.discard(discard)
        self._render()

    def _selected_day(self):
        return self._days[self.day_index] if self._days and 0 <= self.day_index < len(self._days) else None

    def _programmes(self):
        return self._events.get(self._selected_day(), ())

    def _select_now(self):
        if not self._days:
            return
        today = datetime.date.today()
        day = min(self._days, key=lambda date: abs((date - today).days))
        self.day_index = self._days.index(day)
        events = self._programmes()
        moment = time.time()
        self.programme_index = next((index for index, event in enumerate(events) if event.end_timestamp > moment), max(0, len(events) - 1))

    def change_day(self, step):
        if not self._days:
            return
        self.day_index = (self.day_index + step) % len(self._days)
        self.programme_index = 0
        self._select_now_after_load = False
        self._description_key = None
        day = self._selected_day()
        if self._lazy and day not in self._loaded_days:
            self._load(day)
        self._render()

    def change_programme(self, step):
        events = self._programmes()
        if events:
            self.programme_index = (self.programme_index + step) % len(events)
            self._render()

    def today(self):
        self._select_now()
        day = self._selected_day()
        if self._lazy and day not in self._loaded_days:
            self._select_now_after_load = True
            self._load(day)
        self._render()

    def now(self):
        self.today()

    def refresh(self):
        if not self._pending:
            self._load()

    def _text(self, name, value, lines=1):
        value = str(value or "")
        self[name].setText(value)
        font = next((raw for key, raw in getattr(self[name], "skinAttributes", ()) if key == "font"), "")
        try:
            preferred = int(font.rsplit(";", 1)[-1])
        except (TypeError, ValueError):
            preferred = 0
        fit_dynamic_text(self[name], value, max_lines=lines,
            **({"preferred_size": preferred, "min_size": max(7, int(preferred * .72))} if preferred else {}))

    def _render(self):
        if self._closed:
            return
        today = datetime.date.today()
        self._text("days_count", _("Available days: {}").format(len(self._days)))
        start = (self.day_index // 7) * 7
        for row in range(7):
            index = start + row
            focus = self["day_focus_{}".format(row)]
            focus.show() if index == self.day_index and index < len(self._days) else focus.hide()
            day = self._days[index] if index < len(self._days) else None
            primary = (_("Today") if day == today else _("Tomorrow") if day == today + datetime.timedelta(days=1)
                       else localized_short_weekday(day.isoformat()) if day is not None else "")
            self._text("day_label_{}".format(row), primary)
            self._text("day_date_{}".format(row), localized_date_text(day) if day else "")
        day = self._selected_day()
        self._text("day_title", localized_date_text(day) if day else "")
        events = self._programmes()
        page = (self.programme_index // 8) * 8
        moment = time.time()
        for row in range(8):
            index = page + row
            event = events[index] if index < len(events) else None
            focus = self["programme_focus_{}".format(row)]
            focus.show() if event is not None and index == self.programme_index else focus.hide()
            self._text("programme_time_{}".format(row), event.time_text().replace("-", " – ") if event else "")
            self._text("programme_title_{}".format(row), event.title if event else "")
            if parseColor is not None:
                instance = getattr(self["programme_title_{}".format(row)], "instance", None)
                if instance is not None:
                    instance.setForegroundColor(parseColor("#8295AF" if event and event.end_timestamp <= moment and index != self.programme_index else "#FFFFFF"))
        self["message"].setText(_("Loading EPG...") if self._pending and not events else
            _("No programmes for this day.") if self._days and not events else
            _("EPG information unavailable.") if not self._days else "")
        unused_w, unused_h, px, py = geometry()
        update_scrollbar(self, "day_scroll", len(self._days), self.day_index, 7, (px(328), py(281), px(4), py(620)))
        update_scrollbar(self, "programme_scroll", len(events), self.programme_index, 8, (px(1224), py(281), px(4), py(620)))
        event = events[self.programme_index] if events else None
        self._text("programme_title", event.title if event else "", 2)
        self._text("programme_time", event.time_text().replace("-", " – ") if event else "")
        self._text("duration", _("Duration: {} min").format(max(1, int((event.end_timestamp - event.start_timestamp) / 60))) if event else "")
        description_key = (event.start_timestamp, event.title) if event else None
        if description_key != self._description_key:
            self["description"].setText(event.description or _("Programme description unavailable.") if event else "")
            self._description_key = description_key
        self._render_progress(event, moment)
        self._tick()

    def _render_progress(self, event, moment):
        live = bool(event and event.start_timestamp <= moment < event.end_timestamp)
        progress = min(100, max(0, int((moment - event.start_timestamp) * 100 / (event.end_timestamp - event.start_timestamp)))) if event else 0
        self["progress"].setValue(progress)
        self._text("programme_state", _("On air now") if live else _("Upcoming") if event and event.start_timestamp > moment else _("Finished") if event else "")
        self._text("remaining", _("{} min remaining").format(max(1, int((event.end_timestamp - moment + 59) // 60))) if live else "")

    def _tick(self):
        if self._closed:
            return
        moment = time.time()
        self["clock"].setText(time.strftime("%H:%M", time.localtime(moment)))
        self._text("date", localized_date_text(datetime.datetime.fromtimestamp(moment)))
        events = self._programmes()
        if events and self.programme_index < len(events):
            self._render_progress(events[self.programme_index], moment)

    def close_guide(self):
        self.close({"at": time.monotonic(), "days": list(self._days), "events": dict(self._events),
                    "loaded": set(self._loaded_days), "lazy": self._lazy})

    def _cleanup(self):
        self._closed = True
        self._generation += 1
        if self._token is not None:
            self._token.cancel()
        self._timer.stop()
        self._clock_timer.stop()
        with self._lock:
            self._mailbox = None
        if VideoWindow is not None:
            try:
                instance = self["preview"].instance
                restore = getattr(instance, "restoreFullsize", None)
                if callable(restore):
                    restore()
                width, height, unused_x, unused_y = geometry()
                instance.resize(eSize(width, height))
                instance.move(ePoint(0, 0))
            except Exception:
                pass
