# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later

"""Provider-backed TV archive browser; GUI updates stay on the timer thread."""

from collections import OrderedDict
import datetime
import os
import threading
import time

from Components.ActionMap import ActionMap
from Components.Label import Label
from Screens.Screen import Screen
from enigma import eTimer, getDesktop

from .background import attach_background
from .content import ContentError
from .i18n import N_, _, localized_date_text, localized_short_weekday, localized_upper
from .paths import plugin_path
from .remote_footer import (
    _text_units, decorate_remote_footer, footer_item, install_remote_footer,
    remote_action_text, set_remote_footer,
)
from .typography import fit_dynamic_text


CHANNEL_ROWS = 12
PROGRAMME_ROWS = 5
DAY_TABS = 4
ARCHIVE_JOB_SECONDS = 30
ARCHIVE_CACHE_SECONDS = 60
ARCHIVE_CACHE_ENTRIES = 8
ARCHIVE_USER_ERROR_MESSAGES = frozenset((
    N_("Configure an XMLTV guide for this M3U source to use TV archive."),
    N_("TV archive is not supported by this source."),
))
ARCHIVE_FOOTER = (
    footer_item("up_down", "navigate"),
    footer_item("left_right", "change"),
    footer_item("channel_page", "page"),
    footer_item("ok", "select"),
    footer_item("exit", "exit"),
)


def _scale():
    try:
        size = getDesktop(0).size()
        width, height = int(size.width()), int(size.height())
    except Exception:
        width, height = 1280, 720
    factor = min(width / 1920.0, height / 1080.0)
    return width, height, lambda value: max(1, int(round(value * factor)))


def _connect_timer(timer, callback):
    try:
        timer.timeout.connect(callback)
    except AttributeError:
        timer.callback.append(callback)


def _archive_skin():
    width, height, px = _scale()
    widgets = []

    def label(name, x, y, w, h, font=34, color="#F8FAFC", **attrs):
        attributes = {
            "name": name, "position": "{},{}".format(px(x) if x else 0, px(y) if y else 0),
            "size": "{},{}".format(px(w), px(h)),
            "font": "Regular;{}".format(px(font)),
            "foregroundColor": color, "transparent": "1", "zPosition": "4",
            "valign": "center", "noWrap": "1",
        }
        attributes.update(attrs)
        widgets.append("<widget {} />".format(" ".join(
            '{}="{}"'.format(key, value) for key, value in attributes.items()
        )))

    def panel(name, x, y, w, h, color="#050B23", z=1):
        label(name, x, y, w, h, 1, backgroundColor=color,
              transparent="0", zPosition=str(z), borderWidth=str(px(2)),
              borderColor="#29375B")

    def focus(name, x, y, w, h):
        for edge, box, color in (
            ("top", (x, y, w, 4), "#00E5FF"),
            ("bottom", (x, y+h-4, w, 4), "#E600FF"),
            ("left", (x, y, 5, h), "#E600FF"),
            ("right", (x+w-4, y, 4, h), "#E600FF"),
        ):
            label(name+"_"+edge, *box, font=1, backgroundColor=color,
                  transparent="0", zPosition="5")

    def scroll_arrow(name, x, y):
        # Keep the guide visible at a boundary, but mute unavailable scrolling.
        label(name+"_disabled", x, y, 36, 36, 25, "#354561", halign="center")
        label(name, x, y, 36, 36, 25, "#00E5FF", halign="center")

    widgets.append('<widget name="archive_bg" position="0,0" size="{},{}" '
                   'zPosition="0" />'.format(width, height))
    label("brand_gt", 40, 14, 72, 70, 46, "#D946EF")
    label("brand", 112, 14, 505, 70, 46)
    label("account", 660, 14, 850, 70, 34, halign="center")
    label("clock", 1710, 14, 170, 70, 39, halign="right")
    label("title", 52, 104, 1740, 62, 42)
    panel("channels_panel", 40, 180, 474, 765)
    panel("programmes_panel", 532, 180, 1348, 765)
    label("channels_heading", 62, 197, 362, 45, 28)
    label("channel_count", 60, 895, 364, 36, 23, "#AEBFD8", halign="right")
    scroll_arrow("channel_scroll_up", 450, 203)
    scroll_arrow("channel_scroll_down", 450, 895)
    for index in range(CHANNEL_ROWS):
        y = 255 + index * 52
        panel("channel_bg_{}".format(index), 57, y, 440, 48, "#07112C", 2)
        panel("channel_selected_{}".format(index), 57, y, 440, 48, "#074468", 3)
        label("channel_{}".format(index), 77, y+3, 394, 42, 28)
        focus("channel_focus_{}".format(index), 57, y, 440, 48)
    for index in range(DAY_TABS):
        x = 553 + index * 326
        panel("day_bg_{}".format(index), x, 200, 310, 58, "#07112C", 2)
        panel("day_selected_{}".format(index), x, 200, 310, 58, "#009EC9", 3)
        label("day_{}".format(index), x+10, 204, 290, 50, 28, halign="center")
        focus("day_focus_{}".format(index), x, 200, 310, 58)
    label("day_caption", 554, 271, 1228, 47, 30)
    label("day_count", 1798, 275, 64, 39, 20, "#AEBFD8", halign="right")
    scroll_arrow("programme_scroll_up", 1826, 333)
    scroll_arrow("programme_scroll_down", 1826, 689)
    label("programme_count", 1580, 689, 224, 36, 23, "#AEBFD8", halign="right")
    for index in range(PROGRAMME_ROWS):
        y = 333 + index * 70
        panel("programme_bg_{}".format(index), 553, y, 1263, 66, "#07112C", 2)
        panel("programme_selected_{}".format(index), 553, y, 1263, 66, "#0C274A", 3)
        label("programme_time_{}".format(index), 577, y+6, 130, 54, 28)
        label("programme_{}".format(index), 719, y+6, 824, 54, 30)
        label("programme_duration_{}".format(index), 1555, y+6, 218, 54, 25,
              halign="right")
        label("programme_play_{}".format(index), 1786, y+6, 24, 54, 25, "#00E5FF")
        focus("programme_focus_{}".format(index), 553, y, 1263, 66)
    panel("details_panel", 553, 751, 1310, 178)
    label("detail_title", 577, 764, 1258, 52, 32)
    label("detail_time", 577, 819, 1258, 42, 26, "#D8E6F4")
    label("detail_description", 577, 865, 1258, 50, 25, "#BCCDE2")
    label("message", 580, 430, 1255, 240, 33, "#D8E6F4", noWrap="0",
          halign="center", valign="center", zPosition="6")
    label("footer", 0, 982, 1920, 96, 28)
    return (
        '<screen name="GTArchiveScreen" position="0,0" size="{},{}" '
        'flags="wfNoBorder" backgroundColor="#020617">\n{}\n</screen>'
    ).format(width, height, "\n".join(widgets))


class _ArchiveJob(object):
    def __init__(self, generation, kind, channel=None, day=None):
        self.generation = generation
        self.kind = kind
        self.channel = channel
        self.day = day
        self.cancel_event = threading.Event()
        self.deadline = time.monotonic() + ARCHIVE_JOB_SECONDS
        self.done = False
        self.result = None
        self.error = None
        self.timed_out = False


class GTArchiveScreen(Screen):
    def __init__(self, session, account, content_client=None, archive_client=None):
        # OK changes between selection, playback and retry without rebuilding
        # the screen.  Reserve the widest localized verb from the start.
        widest_action = max(
            ("select", "play", "retry"),
            key=lambda action: _text_units(localized_upper(remote_action_text(action))),
        )
        widest_footer = ARCHIVE_FOOTER[:3]+(footer_item("ok", widest_action), ARCHIVE_FOOTER[4])
        self.skin = decorate_remote_footer(
            _archive_skin(), ARCHIVE_FOOTER,
            alternate_items=widest_footer,
            transparent_panel=True, show_dividers=False,
        )
        Screen.__init__(self, session)
        self.account = account
        if archive_client is None:
            from .catchup import ArchiveClient
            archive_client = ArchiveClient(account, content_client)
        self.archive_client = archive_client
        self.channels = []
        self.programmes = []
        self.days = []
        self.channel_index = self.day_index = self.programme_index = 0
        self.focus = "channels"
        self._closed = self._started = self._suspended = False
        self._loading = False
        self._error = None
        self._playback_error = False
        self._generation = 0
        self._active_job = self._pending_job = None
        self._programme_key = None
        self._cache = OrderedDict()
        self._timer = eTimer()
        _connect_timer(self._timer, self._poll)
        self._clock_timer = eTimer()
        _connect_timer(self._clock_timer, self._update_clock)

        # Layout is widget-native: no provider text or translations are baked
        # into artwork, and all text is refitted after the GUI is laid out.
        import xml.etree.ElementTree as ElementTree
        for widget in ElementTree.fromstring(_archive_skin()).findall("widget"):
            if widget.attrib["name"] != "archive_bg":
                self[widget.attrib["name"]] = Label("")
        attach_background(
            self, "archive_bg", os.path.join(
                plugin_path(), "skin", "images", "global-neon-v0912.png",
            ),
        )
        install_remote_footer(self, ARCHIVE_FOOTER)
        self["brand_gt"].setText("GT")
        self["brand"].setText("IPTV PLAYER PRO")
        # The physical CH keys use MoviePlayerActions on both OpenATV and
        # OpenPLi.  Their Navigation/ChannelSelect aliases can fire again on
        # release, so binding those too would turn one press into two pages.
        self["actions"] = ActionMap(
            ["OkCancelActions", "DirectionActions", "MoviePlayerActions",
             "ColorActions"],
            {
                "ok": self.open_selected, "cancel": self.back, "back": self.back,
                "up": self.move_up, "upRepeated": self.move_up,
                "down": self.move_down, "downRepeated": self.move_down,
                "left": self.move_left, "leftRepeated": self.move_left,
                "right": self.move_right, "rightRepeated": self.move_right,
                "channelUp": self.page_up, "channelDown": self.page_down,
                "green": self.retry,
            }, -1,
        )
        self.onShown.append(self._shown)
        self.onLayoutFinish.append(self._refresh)
        self.onClose.append(self._stop)
        self.setTitle(_("TV Archive"))
        self._refresh()

    def _channel(self):
        return self.channels[self.channel_index] if self.channels else None

    def _day(self):
        return self.days[self.day_index] if self.days else None

    def _key(self):
        channel, day = self._channel(), self._day()
        if channel is None or day is None:
            return None
        return str(channel.stream_id), day.isoformat()

    def _should_cache_programmes(self, entries):
        policy = getattr(self.archive_client, "should_cache_programmes", None)
        if callable(policy):
            return bool(policy(entries))
        # Compatibility for archive adapters supplied by third-party skins or
        # tests: only the built-in facade opts out of transient empty caching.
        return True

    def _job_is_current(self, job):
        if (
            self._closed or self._suspended or job is None or job.timed_out
            or job.cancel_event.is_set() or job.generation != self._generation
        ):
            return False
        if job.kind != "programmes":
            return job.kind == "channels"
        if job.channel is not self._channel() or job.day != self._day():
            return False
        return (str(job.channel.stream_id), job.day.isoformat()) == self._key()

    def _invalidate_programme_cache(self, channel, day):
        invalidate = getattr(
            self.archive_client, "invalidate_programme_cache", None,
        )
        if callable(invalidate):
            try:
                invalidate(channel, day)
            except Exception:
                # The UI cache is still invalidated below.  A compatibility
                # adapter must not be able to break navigation or EXIT while
                # its optional cache hook is unwinding.
                pass

    def _make_days(self):
        channel = self._channel()
        try:
            count = max(1, min(365, int(channel.days)))
        except (AttributeError, TypeError, ValueError, OverflowError):
            count = 1
        today = datetime.datetime.now().date()
        self.days = [today-datetime.timedelta(days=index) for index in range(count+1)]
        self.day_index = min(self.day_index, len(self.days)-1)

    def _shown(self):
        if self._closed:
            return
        self._suspended = False
        self._update_clock()
        if not self._started:
            self._started = True
            self._queue("channels")
        elif self.channels and self._programme_key != self._key():
            self._load_day()
        else:
            self._refresh()

    def _update_clock(self):
        if self._closed or self._suspended:
            return
        now = datetime.datetime.now()
        self["clock"].setText(now.strftime("%H:%M"))
        self._clock_timer.start(max(250, (60-now.second)*1000), True)

    def _queue(self, kind, channel=None, day=None):
        if self._closed or self._suspended:
            return
        self._generation += 1
        if self._active_job is not None:
            active = self._active_job
            if not active.cancel_event.is_set():
                active.cancel_event.set()
                if active.kind == "programmes":
                    self._invalidate_programme_cache(active.channel, active.day)
        self._pending_job = _ArchiveJob(self._generation, kind, channel, day)
        self._loading, self._error, self._playback_error = True, None, False
        self._start_pending()
        self._timer.start(75, True)
        self._refresh()

    def _start_pending(self):
        # At most one running request and one coalesced pending selection.
        # A cancelled but slow network operation cannot grow a worker queue.
        if self._active_job is not None or self._pending_job is None:
            return
        job, self._pending_job = self._pending_job, None
        job.deadline = time.monotonic() + ARCHIVE_JOB_SECONDS
        self._active_job = job
        client = self.archive_client

        def run():
            try:
                kwargs = {"cancel_event": job.cancel_event, "deadline": job.deadline}
                if job.kind == "channels":
                    job.result = list(client.load_channels(**kwargs) or [])
                else:
                    job.result = list(client.load_programmes(
                        job.channel, day=job.day, **kwargs
                    ) or [])
            except Exception as error:
                job.error = error
            finally:
                job.done = True

        worker = threading.Thread(target=run)
        worker.daemon = True
        worker.start()

    def _poll(self):
        if self._closed or self._suspended:
            return
        job = self._active_job
        if job is not None and not job.done:
            if time.monotonic() >= job.deadline and not job.timed_out:
                publish_timeout = self._job_is_current(job)
                job.timed_out = True
                job.cancel_event.set()
                if job.kind == "programmes":
                    self._invalidate_programme_cache(job.channel, job.day)
                if publish_timeout:
                    self._loading = False
                    self._error = N_("Could not load the TV archive.")
                    self._refresh()
            self._timer.start(75, True)
            return
        if job is not None:
            self._active_job = None
            if self._job_is_current(job):
                self._loading = False
                if job.error is not None:
                    message = str(job.error) if isinstance(job.error, ContentError) else ""
                    self._error = (
                        message if message in ARCHIVE_USER_ERROR_MESSAGES
                        else N_("Could not load the TV archive.")
                    )
                elif job.kind == "channels":
                    self.channels = job.result
                    self.channel_index = self.day_index = 0
                    if self.channels:
                        self._make_days()
                        self._load_day()
                else:
                    key = (str(job.channel.stream_id), job.day.isoformat())
                    if self._should_cache_programmes(job.result):
                        self._cache[key] = (time.monotonic(), job.result)
                        self._cache.move_to_end(key)
                        while len(self._cache) > ARCHIVE_CACHE_ENTRIES:
                            self._cache.popitem(last=False)
                    else:
                        self._cache.pop(key, None)
                    self._apply_programmes(key, job.result)
            self._refresh()
        self._start_pending()
        if self._active_job is not None:
            self._timer.start(75, True)

    def _apply_programmes(self, key, entries):
        if key != self._key():
            return
        day = self._day()
        self.programmes = sorted(
            (item for item in entries
             if datetime.datetime.fromtimestamp(item.start).date() == day),
            key=lambda item: (item.start, item.end),
        )
        self._programme_key = key
        self.programme_index = min(self.programme_index, max(0, len(self.programmes)-1))

    def _load_day(self):
        self.programmes = []
        self.programme_index = 0
        self._programme_key = None
        self._error = None
        self._playback_error = False
        key = self._key()
        if key is None:
            return
        cached = self._cache.get(key)
        if (
            cached is not None
            and time.monotonic()-cached[0] < ARCHIVE_CACHE_SECONDS
            and self._should_cache_programmes(cached[1])
        ):
            self._generation += 1
            if self._active_job is not None:
                active = self._active_job
                if not active.cancel_event.is_set():
                    active.cancel_event.set()
                    if active.kind == "programmes":
                        self._invalidate_programme_cache(
                            active.channel, active.day,
                        )
            self._pending_job = None
            self._loading = False
            self._cache.move_to_end(key)
            self._apply_programmes(key, cached[1])
            self._refresh()
        else:
            # Drop legacy/transient empty Stalker entries before queueing the
            # explicit selection request; there is no timer-driven retry.
            self._cache.pop(key, None)
            self._queue("programmes", self._channel(), self._day())

    def _set_visible(self, name, visible):
        (self[name].show if visible else self[name].hide)()

    def _show_focus(self, prefix, visible):
        for edge in ("top", "bottom", "left", "right"):
            self._set_visible(prefix+"_"+edge, visible)

    def _show_scroll(self, prefix, first, rows, count):
        for direction, symbol, available in (
            ("up", "▲", first > 0),
            ("down", "▼", first+rows < count),
        ):
            name = prefix+"_scroll_"+direction
            self[name].setText(symbol)
            self[name+"_disabled"].setText(symbol)
            self._set_visible(name, available)
            self._set_visible(name+"_disabled", not available)

    def _text(self, name, value, chars=55, lines=1):
        fit_dynamic_text(self[name], value, max_lines=lines, fallback_chars=chars)

    def _refresh(self):
        if self._closed:
            return
        self._text("title", localized_upper(_("TV Archive")))
        self._text("channels_heading", localized_upper(_("Channels")), 24)
        # Account host is already the credential-free endpoint representation.
        self._text("account", str(getattr(self.account, "host", "") or ""), 55)
        first = (self.channel_index // CHANNEL_ROWS) * CHANNEL_ROWS
        self._show_scroll("channel", first, CHANNEL_ROWS, len(self.channels))
        for row in range(CHANNEL_ROWS):
            index = first+row
            present = index < len(self.channels)
            selected = present and index == self.channel_index
            self._text("channel_{}".format(row), self.channels[index].name if present else "", 27)
            self._set_visible("channel_bg_{}".format(row), present)
            self._set_visible("channel_selected_{}".format(row), selected)
            self._show_focus("channel_focus_{}".format(row), selected and self.focus == "channels")
        self._text("channel_count", "{}/{}".format(self.channel_index+1, len(self.channels)) if self.channels else "")
        first_day = (self.day_index // DAY_TABS) * DAY_TABS
        for tab in range(DAY_TABS):
            index = first_day+tab
            present = index < len(self.days)
            selected = present and index == self.day_index
            day = self.days[index] if present else None
            text = "{}  {}".format(localized_short_weekday(day.isoformat()), day.strftime("%d.%m")) if day else ""
            self._text("day_{}".format(tab), text, 20)
            self._set_visible("day_bg_{}".format(tab), present)
            self._set_visible("day_selected_{}".format(tab), selected)
            self._show_focus("day_focus_{}".format(tab), selected and self.focus == "days")
        channel, day = self._channel(), self._day()
        caption = "{}  ·  {}".format(channel.name, localized_date_text(day)) if channel and day else ""
        self._text("day_caption", caption, 72)
        self._text("day_count", "{}/{}".format(self.day_index+1, len(self.days)) if self.days else "", 10)
        first = (self.programme_index // PROGRAMME_ROWS) * PROGRAMME_ROWS
        self._show_scroll("programme", first, PROGRAMME_ROWS, len(self.programmes))
        self._text("programme_count", "{}/{}".format(self.programme_index+1, len(self.programmes)) if self.programmes else "")
        for row in range(PROGRAMME_ROWS):
            index = first+row
            present = index < len(self.programmes)
            selected = present and index == self.programme_index
            programme = self.programmes[index] if present else None
            self._text("programme_{}".format(row), programme.title if programme else "", 65)
            start = datetime.datetime.fromtimestamp(programme.start).strftime("%H:%M") if programme else ""
            duration = _("{} min").format(max(1, int((programme.end-programme.start+59)//60))) if programme else ""
            self._text("programme_time_{}".format(row), start, 8)
            self._text("programme_duration_{}".format(row), duration, 15)
            self["programme_play_{}".format(row)].setText("▶")
            self._set_visible("programme_play_{}".format(row), selected and self.focus == "programmes")
            self._set_visible("programme_bg_{}".format(row), present)
            self._set_visible("programme_selected_{}".format(row), selected)
            self._show_focus("programme_focus_{}".format(row), selected and self.focus == "programmes")
        programme = self.programmes[self.programme_index] if self.programmes else None
        self._text("detail_title", programme.title if programme else "", 85)
        time_text = ""
        if programme:
            time_text = "{} – {}  ·  {}".format(
                datetime.datetime.fromtimestamp(programme.start).strftime("%H:%M"),
                datetime.datetime.fromtimestamp(programme.end).strftime("%H:%M"),
                _("Watch from archive"),
            )
        self._text("detail_time", time_text, 95)
        self._text("detail_description", programme.description if programme else "", 120)
        message = ""
        if self._playback_error:
            message = _("Could not open the archived programme.")
        elif self._error:
            message = _(self._error)
        elif self._loading:
            message = _("Loading programmes...") if self.channels else _("Loading TV archive...")
        elif not self.channels:
            message = _("No archived channels are available for this account.")
        elif not self.programmes:
            message = _("No archived programmes are available for this day.")
        self._text("message", message, 200, 4)
        empty_selection = bool(
            self.focus == "programmes" and not self._loading
            and self.channels and not self.programmes
        )
        action = "retry" if self._error or empty_selection else ("play" if self.focus == "programmes" else "select")
        set_remote_footer(self, ARCHIVE_FOOTER[:3]+(footer_item("ok", action), ARCHIVE_FOOTER[4]))

    def _can_navigate(self):
        return not self._closed and not self._suspended

    def _move_channel(self, amount, wrap=True):
        if self.channels:
            index = self.channel_index+amount
            index = index % len(self.channels) if wrap else max(0, min(len(self.channels)-1, index))
            if index == self.channel_index:
                return
            self.channel_index = index
            self._make_days()
            self._load_day()

    def _move_day(self, amount, wrap=True):
        if self.days:
            index = self.day_index+amount
            index = index % len(self.days) if wrap else max(0, min(len(self.days)-1, index))
            if index == self.day_index:
                return
            self.day_index = index
            self._load_day()

    def move_up(self):
        if not self._can_navigate():
            return
        if self.focus == "channels":
            self._move_channel(-1)
        elif self.focus == "programmes":
            if not self.programmes or self.programme_index == 0:
                self.focus = "days"
            else:
                self.programme_index -= 1
        self._refresh()

    def move_down(self):
        if not self._can_navigate():
            return
        if self.focus == "channels":
            self._move_channel(1)
        elif self.focus == "days":
            self.focus = "programmes"
        elif self.programmes:
            self.programme_index = min(len(self.programmes)-1, self.programme_index+1)
        self._refresh()

    def move_left(self):
        if not self._can_navigate():
            return
        if self.focus == "days":
            self._move_day(-1)
        elif self.focus == "programmes":
            self.focus = "channels"
        self._refresh()

    def move_right(self):
        if not self._can_navigate():
            return
        if self.focus == "channels":
            self.focus = "days"
        elif self.focus == "days":
            self._move_day(1)
        self._refresh()

    def _page(self, amount):
        if not self._can_navigate():
            return
        if self.focus == "channels":
            self._move_channel(amount*CHANNEL_ROWS, wrap=False)
        elif self.focus == "days":
            self._move_day(amount*DAY_TABS, wrap=False)
        elif self.programmes:
            self.programme_index = max(0, min(len(self.programmes)-1, self.programme_index+amount*PROGRAMME_ROWS))
        self._refresh()

    def page_up(self):
        self._page(-1)

    def page_down(self):
        self._page(1)

    def open_selected(self):
        if not self._can_navigate():
            return
        if self._error:
            self.retry()
            return
        if self.focus == "channels":
            if self.channels:
                self.focus = "days"
        elif self.focus == "days":
            self.focus = "programmes"
        elif self.programmes and not self._loading:
            from .browser import open_extplayer
            try:
                item = self.archive_client.playback_item(
                    self._channel(), self.programmes[self.programme_index],
                )
                open_extplayer(self.session, self.archive_client, item, owner=self)
            except Exception:
                self._resume_after_playback_resolution_failure()
            return
        elif self.focus == "programmes" and not self._loading:
            # An empty Stalker table may be a transient authenticated HTTP-200
            # response.  Keep refresh explicit and bounded, but make it
            # reachable through the advertised OK action as well as GREEN.
            self.retry()
            return
        self._refresh()

    def retry(self):
        if not self._can_navigate():
            return
        if self.channels:
            channel, day = self._channel(), self._day()
            self._cache.pop(self._key(), None)
            self._invalidate_programme_cache(channel, day)
            self._load_day()
        else:
            self._queue("channels")

    def back(self):
        if self._closed:
            return
        self._stop()
        self.close()

    def _cancel_jobs(self):
        self._generation += 1
        if self._active_job is not None:
            active = self._active_job
            if not active.cancel_event.is_set():
                active.cancel_event.set()
                if active.kind == "programmes":
                    self._invalidate_programme_cache(active.channel, active.day)
        if self._pending_job is not None:
            self._pending_job.cancel_event.set()
        self._pending_job = None
        self._loading = False
        self._timer.stop()

    def _suspend_for_playback(self):
        self._suspended = True
        self._cancel_jobs()
        self._clock_timer.stop()

    def _resume_after_playback_resolution_failure(self):
        self._playback_error = True
        self._shown()

    def _stop(self):
        if self._closed:
            return
        self._closed = True
        self._cancel_jobs()
        self._clock_timer.stop()
        self._cache.clear()
        controller = getattr(self, "_playback_link_controller", None)
        if controller is not None:
            controller.cancel()
