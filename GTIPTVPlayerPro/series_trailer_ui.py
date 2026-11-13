# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Native PLAY hint and cancelable next-episode trailer dialog."""
import os
import re
import threading
import time
from xml.sax.saxutils import escape

from Components.ActionMap import ActionMap
from Components.Label import Label
from Components.Pixmap import Pixmap
from Screens.Screen import Screen
from enigma import eTimer
from keymapparser import readKeymap

from .i18n import _, N_
from .typography import fit_dynamic_text
from .series_trailer import service_for

ASSET = os.path.join(os.path.dirname(__file__), 'skin', 'images', 'series-trailer-hint-r29.png')
MESSAGES = (
    N_('Trailer'), N_('Searching for the next episode trailer...'),
    N_('No trailer found for the next episode.'), N_('Next episode information is unavailable.'),
    N_('Could not search for a trailer. Please try again.'),
    N_('General trailer'), N_('Searching for a trailer...'), N_('No official trailer found.'),
)


PLAY_KEY_CONTEXT = 'GTIPTVPlayerProSeriesTrailerActions'
_play_keys_loaded = False


def _ensure_play_keys():
    global _play_keys_loaded
    if not _play_keys_loaded:
        # Private context contains physical PLAY keys only. InfobarSeekActions
        # aliases OK to unPauseService on OpenATV and cannot be used here.
        readKeymap(os.path.join(os.path.dirname(__file__), 'series_trailer_keys.xml'))
        _play_keys_loaded = True


def hint_geometry(width, height, px, cinematic=True):
    """Bottom frame border and backdrop lower edge share the same coordinate."""
    w, h = px(380), px(74)
    bottom = px(616 if cinematic else 318)
    return width - px(24) - w, bottom - h, w, h, bottom


def add_hint_skin(skin, width, height, px, cinematic=True, prefix="series"):
    x, y, w, h, bottom = hint_geometry(width, height, px, cinematic)
    if not cinematic:
        # The grid has text instead of a photo: reserve its lower-right corner.
        def fit_people(match):
            widget = match.group(0)
            pos = re.search(r'position="(\d+),(\d+)"', widget)
            size = re.search(r'size="(\d+),(\d+)"', widget)
            if pos and size:
                available = max(px(100), x-int(pos.group(1))-px(12))
                widget = widget[:size.start()]+'size="{},{}"'.format(min(int(size.group(1)),available),size.group(2))+widget[size.end():]
            return widget
        skin = re.sub(r'<widget\s+name="'+re.escape(prefix)+r'_people"(?=\s)[^>]*/>',fit_people,skin)
    markup = ('<widget name="{prefix}_trailer_hint_bg" position="{x},{y}" size="{w},{h}" '
        'pixmap="{asset}" alphatest="blend" scale="1" zPosition="31" />'
        '<widget name="{prefix}_trailer_hint_text" position="{tx},{ty}" size="{tw},{th}" '
        'font="Regular;{font}" foregroundColor="#FFFFFF" transparent="1" '
        'valign="center" halign="center" zPosition="32" />').format(
        prefix=prefix,x=x,y=y,w=w,h=h,asset=escape(ASSET, {'"':'&quot;'}),tx=x+px(76),ty=y+px(8),
        tw=w-px(94),th=h-px(16),font=px(25))
    return skin.replace('</screen>', markup+'</screen>')


class SeriesTrailerHint:
    def __init__(self, screen):
        self.screen = screen
        screen['series_trailer_hint_bg'] = Label('') if 'light_play_key' in screen else Pixmap()
        screen['series_trailer_hint_text'] = Label(_('Trailer'))
        _ensure_play_keys()
        screen['series_trailer_actions'] = ActionMap([PLAY_KEY_CONTEXT], {
            'playTrailer': self.start,
        }, -2)
        screen.onLayoutFinish.append(self.refresh_hint)
        screen.onShown.append(self.refresh_hint)
        screen.onClose.append(self.close)

    def refresh_hint(self):
        screen = self.screen
        if screen is None:
            return
        visible = bool(not getattr(screen,'_closed',False) and
            getattr(screen,'_series_entries',None) and not getattr(screen,'_series_loading',False)
            and getattr(screen,'_series_focus','categories')=='series')
        if getattr(self, '_hint_visible', None) == visible:
            return
        self._hint_visible = visible
        names = ('series_trailer_hint_bg', 'series_trailer_hint_text')
        if 'light_play_key' in screen:
            names += ('light_play_key',)
        for name in names:
            screen[name].show() if visible else screen[name].hide()
        if visible:
            from .main import _scale
            px = _scale()[2]
            fit_dynamic_text(screen['series_trailer_hint_text'], _('Trailer'),max_lines=2,
                preferred_size=px(25),min_size=max(8,px(14)))

    def start(self):
        screen = self.screen
        if (screen is None or getattr(screen,'_closed',False) or getattr(screen,'_series_loading',False)
                or getattr(screen,'_series_child_open',False) or getattr(screen,'_series_focus','')!='series'):
            return
        entries = getattr(screen,'_series_entries',())
        index = getattr(screen,'_series_selected_index',0)
        if not 0 <= index < len(entries):
            return
        item = entries[index]
        # Capture only this selected series; no provider request is made here.
        info = screen._cached_grid_detail('series')
        screen._open_series_child(SeriesTrailerScreen, item, info, screen._metadata_client)

    def close(self):
        self.screen = None


class SeriesTrailerScreen(Screen):
    def __init__(self, session, item, info, metadata):
        from .main import _scale, _connect_timer
        from .web_youtube import WEB_YOUTUBE
        width,height,px = _scale()
        w,h = px(1000),px(220)
        self.skin = ('<screen name="GTSeriesTrailer" position="{x},{y}" size="{w},{h}" '
            'title="{title}" backgroundColor="#071124">'
            '<widget name="status" position="{m},{m}" size="{tw},{th}" font="Regular;{f}" '
            'foregroundColor="#FFFFFF" transparent="1" valign="center" halign="center" />'
            '<widget name="back" position="{m},{by}" size="{tw},{bh}" font="Regular;{bf}" '
            'foregroundColor="#22D3EE" transparent="1" halign="center" /></screen>').format(
            x=(width-w)//2,y=(height-h)//2,w=w,h=h,title=escape(_('Trailer'),{'"':'&quot;'}),m=px(20),
            tw=w-px(40),th=px(130),f=px(28),by=px(170),bh=px(38),bf=px(22))
        Screen.__init__(self, session)
        self._closed = False
        self._cancel = threading.Event()
        self._result = None
        self._play_token = ''
        self._started = False
        self._deadline = time.monotonic()+80
        self._youtube = WEB_YOUTUBE
        self['status'] = Label(_('Searching for a trailer...'))
        self['back'] = Label('EXIT · '+_('Back'))
        self['actions'] = ActionMap(['OkCancelActions'],{'cancel':self.close},-3)
        self.timer = eTimer()
        _connect_timer(self.timer,self._poll)
        self.onClose.append(self._stop)
        self.onShown.append(self._shown)
        self._item,self._info,self._metadata = item,info,metadata

    def _service(self):
        return service_for(self._metadata)

    def _shown(self):
        if self._closed or self._started:
            return
        self._started = True
        def work():
            result = self._service().find(self._item,self._info,self._cancel)
            if not self._cancel.is_set():
                self._result = result
        threading.Thread(target=work,name='GT-Series-Trailer',daemon=True).start()
        self.timer.start(100,True)

    def _message(self, text):
        from .main import _scale
        px = _scale()[2]
        fit_dynamic_text(self['status'],_(text),max_lines=3,preferred_size=px(28),min_size=max(10,px(17)))

    def _poll(self):
        if self._closed:
            return
        if time.monotonic()>self._deadline:
            self._cancel.set()
            if self._play_token:
                self._youtube.cancel_play(self._play_token)
            self._message('Could not search for a trailer. Please try again.')
            return
        if self._result is not None:
            result,self._result = self._result,None
            state = result.get('state')
            if state=='found':
                try:
                    general = result.get('kind')=='general'
                    title = '{} · {}'.format(_('General trailer'),result['title']) if general else result['title']
                    value = self._youtube.play(result['id'],title,_request_owner=self,
                        _on_closed=lambda *args:self.close(),_original_audio=True)
                    self._play_token = value['token']
                    self._message('General trailer' if general else 'Please wait')
                except Exception:
                    self._message('Could not start the player.')
                    return
            elif state=='cancelled':
                self.close()
                return
            else:
                self._message({'no_next':'Next episode information is unavailable.',
                    'not_found':'No official trailer found.'}.get(state,
                    'Could not search for a trailer. Please try again.'))
                return
        if self._play_token:
            try:
                state = (self._youtube.status(self._play_token).get('job') or {}).get('state')
            except Exception:
                state = 'failed'
            if state in ('failed','cancelled'):
                self._message('Could not start the player.')
                return
            if state=='playing':
                return  # Player close callback returns directly to the series screen.
        self.timer.start(150,True)

    def _stop(self):
        self._closed = True
        self._cancel.set()
        self.timer.stop()
        if self._play_token:
            self._youtube.cancel_play(self._play_token)
