# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Account reservations spanning link resolution, mini TV and full playback."""
import hashlib
import os
import time
import uuid

from . import downloads as store
from .playback import resume_account_scope

_GUARD = None


def _scope(client):
    account = getattr(client, "account", None)
    if getattr(account, "source_type", "") not in ("xtream", "stalker"):
        return ""
    return resume_account_scope(client)


def _record(scope):
    return dict(scope=scope, pid=os.getpid(), stamp=store.process_stamp())


def _url_key(url):
    return hashlib.sha256(str(url or "").split("#", 1)[0].split("|", 1)[0].encode("utf-8")).hexdigest()


def _reference_url(reference):
    if reference is None:
        return ""
    for name in ("getPath", "getUrl"):
        method = getattr(reference, name, None)
        if callable(method):
            try:
                value = method()
                if value:
                    return str(value)
            except Exception:
                pass
    return str(getattr(reference, "url", "") or "")


def reserve(client):
    scope = _scope(client)
    if not scope:
        return ""
    identity = uuid.uuid4().hex
    with store.transaction() as state:
        if store.active_for(state, scope):
            raise store.DownloadError("busy")
        record = _record(scope)
        record["until"] = time.time() + 90
        state["reservations"][identity] = record
    return identity


def release(identity):
    if identity:
        with store.transaction() as state:
            state["reservations"].pop(identity, None)


def cancel_pending(client):
    scope = _scope(client)
    if not scope:
        return
    with store.transaction() as state:
        for identity, record in list(state["reservations"].items()):
            if record.get("pid") == os.getpid() and record.get("scope") == scope:
                state["reservations"].pop(identity, None)


def remember(client, url):
    scope = _scope(client)
    if not scope or not url:
        return
    with store.transaction() as state:
        urls = state.setdefault("urls", {})
        urls[_url_key(url)] = scope
        while len(urls) > 4096:
            urls.pop(next(iter(urls)))


def protect_client(client):
    if not _scope(client) or getattr(client, "_download_protected", False):
        return client

    def wrap(original):
        def guarded(*args, **kwargs):
            ticket = reserve(client)
            try:
                result = original(*args, **kwargs)
                url = result[0] if isinstance(result, tuple) else result
                remember(client, url)
                # Keep the reservation until playService commits. This closes
                # the interval between an async result and the GUI callback.
                return result
            except Exception:
                release(ticket)
                raise
        return guarded

    for name in ("playback_url", "refresh_playback_url", "decoder_retry_playback_url"):
        method = getattr(client, name, None)
        if callable(method):
            setattr(client, name, wrap(method))
    client._download_protected = True
    return client


def allow(session, client):
    install(session)
    protect_client(client)
    try:
        with store.transaction() as state:
            blocked = bool(_scope(client) and store.active_for(state, _scope(client)))
    except Exception:
        blocked = True
    if blocked:
        from Screens.MessageBox import MessageBox
        from .i18n import _
        session.open(MessageBox, _(store.MESSAGES["busy"]), MessageBox.TYPE_INFO)
        return False
    return True


def play_service(navigation, reference):
    url = _reference_url(reference)
    pid = str(os.getpid())
    with store.transaction() as state:
        scope = state.get("urls", {}).get(_url_key(url), "") if url else ""
        old = state["players"].get(pid)
        if scope:
            if store.active_for(state, scope):
                raise store.DownloadError("busy")
            state["players"][pid] = _record(scope)
    try:
        result = navigation.playService(reference)
        if isinstance(result, int) and result != 0:
            raise RuntimeError("playService returned {}".format(result))
    except Exception:
        with store.transaction() as state:
            if old:
                state["players"][pid] = old
            else:
                state["players"].pop(pid, None)
        raise
    with store.transaction() as state:
        if not scope:
            state["players"].pop(pid, None)
        elif pid in state["players"]:
            state["players"][pid]["settle_until"] = time.time() + 2
        for key, value in list(state["reservations"].items()):
            if value.get("pid") == os.getpid() and value.get("scope") == scope:
                state["reservations"].pop(key, None)
    return result


class SessionGuard(object):
    def __init__(self, session):
        from enigma import eTimer
        self.session = session
        self.timer = eTimer()
        try:
            self.connection = self.timer.timeout.connect(self.poll)
        except AttributeError:
            self.timer.callback.append(self.poll)
        self.poll()

    def poll(self):
        try:
            navigation = getattr(self.session, "nav", None)
            reference = navigation.getCurrentlyPlayingServiceReference() if navigation else None
            url = _reference_url(reference)
            with store.transaction() as state:
                scope = state.get("urls", {}).get(_url_key(url), "") if url else ""
                conflict = bool(scope and store.active_for(state, scope))
                if scope and not conflict:
                    record = state["players"].get(str(os.getpid()), {})
                    if record.get("scope") != scope:
                        state["players"][str(os.getpid())] = _record(scope)
                else:
                    record = state["players"].get(str(os.getpid()), {})
                    if reference is not None or time.time() >= record.get("settle_until", 0):
                        state["players"].pop(str(os.getpid()), None)
                store.playing_in(state, "")  # reap exited GUI processes
            # An image can restore its last service before SESSIONSTART after
            # a GUI restart. Downloads survive that restart; stop this one
            # known conflicting IPTV service before accepting further actions.
            if conflict and navigation:
                navigation.stopService()
        except Exception:
            pass
        self.timer.start(500, True)


def install(session):
    global _GUARD
    if session is not None and (_GUARD is None or _GUARD.session is not session):
        if _GUARD is not None:
            _GUARD.timer.stop()
        _GUARD = SessionGuard(session)

