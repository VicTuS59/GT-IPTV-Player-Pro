# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-2.0-or-later
"""Receiver LCD/VFD summaries, using the dedicated display's actual size."""
import datetime
import time
from Components.Label import Label
from Screens.Screen import Screen
from enigma import eSize, eTimer, getDesktop
from .front_panel_layout import FIELDS, panel_layout, panel_skin
from .front_panel_text import PanelText
from .i18n import _, localized_upper


def front_panel_size():
    try:
        size = getDesktop(1).size()
        width, height = int(size.width()), int(size.height())
        if width > 0 and height > 0:
            return width, height
    except Exception:
        pass
    return 132, 64


def front_panel_layout(live=False, vod=False, series=False):
    return panel_layout(*front_panel_size(), kind='live' if live else 'series' if series else 'movie')


def front_panel_skin(live=False, vod=False, series=False):
    return panel_skin(front_panel_layout(live, vod, series))


def _playback_kind(item):
    if getattr(item, 'youtube_native', False):
        return 'youtube'
    return str(getattr(item, 'content_type', '') or '').lower()


def _connect(timer, callback):
    try:
        timer.callback.append(callback)
    except AttributeError:
        # Keep the signal connection alive on images with the newer API.
        return timer.timeout.connect(callback)


class GTPlayerFrontPanelSummary(Screen):
    """Large text on colour panels; compact and rotating small-panel views."""

    def __init__(self, session, parent):
        item = getattr(parent, 'current_item', None)
        self._youtube_preview = bool(getattr(parent, '_youtube_preview', False))
        kind = 'youtube' if self._youtube_preview else _playback_kind(item)
        self._layout = panel_layout(*front_panel_size(), kind=kind)
        self.skin = panel_skin(self._layout)
        try:
            Screen.__init__(self, session, parent=parent)
        except TypeError:
            Screen.__init__(self, session)
            self.parent = parent
        self._player = parent
        self._active = False
        self._progress_value = 0
        self._motion_pending = False
        self._rotation_key = None
        self._rotation_names = ()
        self._rotation_index = 0
        self._rotation_since = time.monotonic()
        parent._front_panel_summary = self
        for name in FIELDS:
            self[name] = PanelText()
            self[name].configure(self._layout['fields'][name])
        self['progress_bg'] = Label('')
        self['progress_fill'] = Label('')
        self._refresh_timer, self._motion_timer = eTimer(), eTimer()
        self._refresh_connection = _connect(self._refresh_timer, self._refresh)
        self._motion_connection = _connect(self._motion_timer, self._animate)
        # Summary dialogs can be shown again without running Screen.execBegin().
        on_show = getattr(self, 'onShow', None)
        if on_show is None:
            on_show = self.onShown
        on_show.append(self._start)
        # Native Screen inspects bound layout callbacks.
        self.onLayoutFinish.append(self._render)
        self.onClose.append(self._stop)
        self.onHide.append(self._pause)
        self._start()

    @staticmethod
    def _clock_text(seconds):
        try:
            seconds = max(0, int(seconds))
        except (TypeError, ValueError, OverflowError):
            seconds = 0
        hours, remainder = divmod(seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return ('{:02d}:{:02d}:{:02d}'.format(hours, minutes, seconds) if hours
                else '{:02d}:{:02d}'.format(minutes, seconds))

    def _values(self, seek=None):
        item = getattr(self._player, 'current_item', None)
        values = dict.fromkeys(FIELDS, '')
        if self._youtube_preview and (
            item is None or (seek is None and not self._player._preview_owns_service())
        ):
            values['title'] = 'YouTube'
            return values, 0, ('', '', values['title'], '')

        content_type = _playback_kind(item)
        values['title'] = getattr(item, 'name', '') or getattr(self._player, 'title', '') or 'GT IPTV'
        values['clock'] = time.strftime('%H:%M:%S', time.localtime())
        percent = 0
        if content_type == 'live':
            values['kind'] = localized_upper(_('LIVE TV'))
            events = getattr(self._player, '_current_epg_events', ()) or ()
            values['subtitle'] = _('EPG information unavailable')
            if events:
                event = events[0]
                values['subtitle'] = getattr(event, 'title', '') or _('EPG information unavailable')
                try:
                    start = int(getattr(event, 'start_timestamp', 0) or 0)
                    end = int(getattr(event, 'end_timestamp', 0) or 0)
                    if end > start > 0:
                        values['detail'] = '{} - {}'.format(
                            datetime.datetime.fromtimestamp(start).strftime('%H:%M'),
                            datetime.datetime.fromtimestamp(end).strftime('%H:%M'))
                    if start > time.time():
                        values['subtitle'] = '{}: {}'.format(_('NEXT'), values['subtitle'])
                except (TypeError, ValueError, OverflowError, OSError):
                    pass
        else:
            if content_type == 'youtube':
                values['kind'] = 'YouTube'
            else:
                values['kind'] = localized_upper(_('TV Archive') if content_type == 'catchup' else
                    _('SERIES') if content_type == 'series' else _('MOVIES') if content_type == 'movie' else 'GT IPTV')
            # A separate episode row takes space from the readable clocks.
            # Provider titles remain intact, including any episode in the name.
            values['elapsed_label'] = localized_upper(_('Elapsed'))
            values['total_label'] = localized_upper(_('Total'))
            values['remaining_label'] = localized_upper(_('REMAINING'))
            try:
                position, length = self._player._seek_position() if seek is None else seek
                position, length = int(position), int(length)
            except (TypeError, ValueError, OverflowError, AttributeError, RuntimeError):
                position, length = -1, 0
            if position < 0 or length <= 0:
                values['elapsed'] = values['total'] = values['remaining'] = '--:--'
                values['subtitle'] = _('Please wait')
            else:
                position = min(max(0, position), length)
                percent = int(round(100.0 * position / length))
                values['elapsed'] = self._clock_text(position)
                values['total'] = self._clock_text(length)
                values['remaining'] = self._clock_text(length - position)
                values['subtitle'] = '{} / {}'.format(values['elapsed'], values['total'])
            values['percent'] = '{}%'.format(percent)
        return values, percent, (content_type, str(getattr(item, 'id', '')), values['title'], values['episode'])

    def _rotate_compact(self, values, content_key):
        """Small OLED/LCDs show one large caption and value at a time."""
        phases = (
            ('{} · {}'.format(values['kind'], values['percent']), values['title']),
            (values['elapsed_label'], values['elapsed']),
            (values['total_label'], values['total']),
            ('{} · {}'.format(values['remaining_label'], values['percent']), values['remaining']))
        now = time.monotonic()
        if content_key != self._rotation_key:
            self._rotation_key = content_key
            self._rotation_index = 0
            self._rotation_since = now
            self['kind']._since = self['title']._since = now
        duration = max(5.0, self['kind'].cycle_seconds(), self['title'].cycle_seconds())
        if now - self._rotation_since >= duration:
            self._rotation_index = (self._rotation_index + 1) % len(phases)
            self._rotation_since = now
            self['kind']._since = self['title']._since = now
        caption, value = phases[self._rotation_index]
        self['kind'].set_panel_text(caption, preserve_phase=True)
        self['title'].set_panel_text(value, preserve_phase=True)

    def _rotate_minimal(self, values, content_key):
        # One row cannot show every field simultaneously. Keep each field up
        # until its entire overflow has passed before advancing to the next.
        names = ['kind', 'title']
        if values['episode']:
            names.append('episode')
        if content_key[0] == 'live':
            names += ['subtitle', 'detail', 'clock']
        else:
            names += ['elapsed', 'total', 'remaining']
        names = tuple(name for name in names if values[name])
        now = time.monotonic()
        if content_key != self._rotation_key or names != self._rotation_names:
            self._rotation_key, self._rotation_names = content_key, names
            # Start with the programme title, then cycle through all fields.
            self._rotation_index = names.index('title') if 'title' in names else 0
            self._rotation_since = now
            self['title']._since = now
        current = names[self._rotation_index]
        if now - self._rotation_since >= max(5.0, self['title'].cycle_seconds()):
            self._rotation_index = (self._rotation_index + 1) % len(names)
            current = names[self._rotation_index]
            self._rotation_since = now
            self['title']._since = now
        value = values[current]
        if current in ('elapsed', 'total', 'remaining'):
            value = '{} {}'.format(values[current + '_label'], value)
        self['title'].set_panel_text(value, preserve_phase=True)

    def _apply_progress_fill(self, show_bar=True):
        visible = show_bar and self._layout['progress_h'] > 0 and self._layout['kind'] != 'live'
        self['progress_bg'].show() if visible else self['progress_bg'].hide()
        if not visible or self._progress_value <= 0:
            self['progress_fill'].hide()
            return
        width = max(1, int(round(self._layout['content_w'] * self._progress_value / 100.0)))
        if self['progress_fill'].instance is not None:
            self['progress_fill'].instance.resize(eSize(width, self._layout['progress_h']))
        self['progress_fill'].show()

    def _render(self, seek=None):
        values, percent, content_key = self._values(seek)
        preview_idle = self._youtube_preview and not content_key[0]
        compact_vod = self._layout['mode'] == 'compact' and content_key[0] != 'live'
        for name in FIELDS:
            field = self._layout['fields'][name]
            if field['visible']:
                self[name].show()
                if preview_idle or (self._layout['mode'] != 'minimal' and not compact_vod):
                    self[name].set_panel_text(values[name], preserve_phase=name in (
                        'elapsed', 'total', 'remaining', 'percent', 'clock') or
                        (name == 'subtitle' and content_key[0] != 'live'))
            else:
                self[name].hide()
        if preview_idle:
            self._rotation_key = None
        elif self._layout['mode'] == 'minimal':
            self._rotate_minimal(values, content_key)
        elif compact_vod:
            self._rotate_compact(values, content_key)
        self._progress_value = percent
        self._apply_progress_fill(show_bar=not preview_idle)
        self._sync_motion()

    def _moving_fields(self):
        return [self[name] for name in FIELDS
                if self._layout['fields'][name]['visible'] and self[name].moving()]

    def _sync_motion(self):
        if self._active and self._moving_fields():
            if not self._motion_pending:
                self._motion_pending = True
                self._motion_timer.start(80, True)
        elif self._motion_pending:
            self._motion_timer.stop()
            self._motion_pending = False

    def _animate(self):
        self._motion_pending = False
        if not self._active:
            return
        now = time.monotonic()
        for field in self._moving_fields():
            field.tick(now)
        self._sync_motion()

    def _start(self):
        self._active = True
        self._render()
        if not self._youtube_preview:
            self._refresh_timer.start(1000, True)

    def refresh_now(self, seek=None):
        if self._active or not self._youtube_preview:
            self._render(seek)

    def _refresh(self):
        if self._active:
            self._render()
            self._refresh_timer.start(1000, True)

    def _pause(self):
        self._active = False
        self._refresh_timer.stop()
        self._motion_timer.stop()
        self._motion_pending = False

    def _stop(self):
        self._pause()
        if getattr(self._player, '_front_panel_summary', None) is self:
            self._player._front_panel_summary = None
