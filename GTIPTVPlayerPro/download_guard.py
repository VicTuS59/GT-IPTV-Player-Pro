# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Account reservations spanning link resolution, mini TV and full playback."""
import hashlib
import threading
from collections import OrderedDict
import os
import time
import uuid

from . import downloads as store
from .playback import resume_account_scope

_GUARD = None
ACTIVE_POLL_MS = 500
IDLE_POLL_MS = 5000
RECONCILE_SECONDS = 30.0


def _state_signature():
    try:
        info = os.stat(os.path.join(store.RUNTIME, "state.json"))
        return (info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
    except OSError:
        return None


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
    with _STATE.condition:
        _STATE.leases[identity] = (scope, record["until"])
    return identity


class _GuardState:
    """A GUI memory view backed by one serial state-file worker.

    A resolver acquires its cross-process reservation before returning a URL.
    The GUI can therefore start that URL without a file lock. The reservation
    stays live until the worker atomically replaces it with a player record.
    """
    def __init__(self):
        self.condition = threading.Condition()
        self.urls = OrderedDict()
        self.leases = {}
        self.url_leases = {}
        self.handoffs = set()
        self.releases = set()
        self.playing_scope = ""
        self.reference_key = ""
        self.blocked = frozenset()
        self.conflict_key = ""
        self.thread = None
        self.dirty = False
        self.active = False
        self.closed = False
        self.busy = False
        self.revision = 0
        self.last_input = None
        self.last_signature = None
        self.last_reconcile = 0.0

    def wake(self, poll=False):
        # Caller holds the short memory-only condition, never a file lock.
        self.dirty = True
        if not poll:
            self.revision += 1
        if self.thread is None:
            self.thread = threading.Thread(target=self._run, name="GT-SessionGuard", daemon=True)
            try:
                self.thread.start()
            except (OSError, RuntimeError):
                self.thread = None
                raise
        self.condition.notify_all()

    def _run(self):
        while True:
            with self.condition:
                while not self.dirty and not self.closed:
                    self.condition.wait()
                if self.closed:
                    return
                self.dirty = False
                self.active = True
                urls = dict(self.urls)
                releases = set(self.releases)
                handoffs = set(self.handoffs)
                leases = dict(self.leases)
                current_key = self.reference_key
                intended_scope = self.playing_scope
                current_input = (self.revision, current_key, intended_scope)
            try:
                signature = _state_signature()
                now_mono = time.monotonic()
                if (signature is not None and signature == self.last_signature
                        and current_input == self.last_input
                        and now_mono - self.last_reconcile < RECONCILE_SECONDS):
                    continue
                with store.transaction() as state:
                    known = state.setdefault("urls", {})
                    known.update(urls)
                    while len(known) > 4096:
                        known.pop(next(iter(known)))
                    scope = known.get(current_key, "")
                    blocked = frozenset(job.get("scope", "") for job in state.get("jobs", ())
                                        if job.get("status") in store.ACTIVE)
                    conflict = bool(scope and scope in blocked)
                    pid = str(os.getpid())
                    if scope and not conflict:
                        if state["players"].get(pid, {}).get("scope") != scope:
                            state["players"][pid] = _record(scope)
                    elif not intended_scope or conflict:
                        state["players"].pop(pid, None)
                    # Pending cancellation and successful handoff are both
                    # serialized with the player record, closing the gap.
                    handed = set()
                    renewed = {}
                    for identity, record in list(state["reservations"].items()):
                        if identity in releases or identity in handoffs:
                            state["reservations"].pop(identity, None)
                            handed.add(identity)
                        elif identity in leases and record.get("until", 0) > time.time() and record["until"] < time.time() + 30:
                            # A resume question can stay open for minutes.
                            # Renew only its still-valid atomic reservation;
                            # a missing/expired reservation is never invented.
                            record["until"] = time.time() + 90
                            renewed[identity] = (record.get("scope", ""), record["until"])
                    store.playing_in(state, "")
                with self.condition:
                    self.releases.difference_update(releases)
                    self.handoffs.difference_update(handoffs)
                    self.blocked = blocked
                    self.conflict_key = current_key if conflict else ""
                    self.busy = bool(blocked or leases or releases or handoffs)
                    # Restored service URLs also become known without GUI I/O.
                    for key, scope in known.items():
                        self.urls.setdefault(key, scope)
                    while len(self.urls) > 4096:
                        self.urls.popitem(last=False)
                    now = time.time()
                    for identity, value in renewed.items():
                        if self.leases.get(identity) == leases.get(identity):
                            self.leases[identity] = value
                    self.leases = {key: value for key, value in self.leases.items()
                                   if value[1] > now and key not in releases and key not in handed}
                    self.url_leases = {key: ids.intersection(self.leases) for key, ids in self.url_leases.items()
                                       if key in self.urls and ids.intersection(self.leases)}
                    # Remember the signature seen BEFORE acquiring the state
                    # lock. A racing external write can only cause an extra
                    # reconciliation, never be mistaken for our own snapshot.
                    self.last_signature = signature
                    self.last_input = current_input
                    self.last_reconcile = now_mono
            except Exception:
                # Retain releases on transient storage failure. The GUI's next
                # tick retries, without creating threads or blocking on I/O.
                pass
            finally:
                with self.condition:
                    self.active = False
                    self.condition.notify_all()

    def flush(self, timeout=2):
        deadline = time.monotonic() + timeout
        with self.condition:
            while self.dirty or self.active:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self.condition.wait(remaining)
        return True

    def close(self):
        with self.condition:
            self.closed = True
            self.condition.notify_all()


_STATE = _GuardState()


def release(identity):
    if identity:
        with _STATE.condition:
            _STATE.releases.add(identity)
            _STATE.leases.pop(identity, None)
            _STATE.wake()


def cancel_pending(client):
    scope = _scope(client)
    if not scope:
        return
    with _STATE.condition:
        for identity, (account, unused_until) in list(_STATE.leases.items()):
            if account == scope:
                _STATE.releases.add(identity)
                _STATE.leases.pop(identity, None)
        _STATE.wake()


def remember(client, url, ticket=""):
    scope = _scope(client)
    if not scope or not url:
        return
    with _STATE.condition:
        key = _url_key(url)
        _STATE.urls.pop(key, None)
        _STATE.urls[key] = scope
        if ticket:
            _STATE.url_leases.setdefault(key, set()).add(ticket)
        while len(_STATE.urls) > 4096:
            _STATE.urls.popitem(last=False)
        _STATE.wake()


def protect_client(client):
    if not _scope(client) or getattr(client, "_download_protected", False):
        return client

    def wrap(original):
        def guarded(*args, **kwargs):
            ticket = reserve(client)
            try:
                result = original(*args, **kwargs)
                url = result[0] if isinstance(result, tuple) else result
                remember(client, url, ticket)
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
    if not callable(getattr(client, "refresh_playback_url", None)):
        client.refresh_playback_url = client.playback_url
    client.async_playback_resolution = True
    client._download_protected = True
    return client


def allow(session, client):
    install(session)
    protect_client(client)
    with _STATE.condition:
        blocked = _scope(client) in _STATE.blocked
    if blocked:
        from Screens.MessageBox import MessageBox
        from .i18n import _
        session.open(MessageBox, _(store.MESSAGES["busy"]), MessageBox.TYPE_INFO)
        return False
    return True


def play_service(navigation, reference):
    key = _url_key(_reference_url(reference))
    with _STATE.condition:
        scope = _STATE.urls.get(key, "")
        # A valid worker-created lease or an already committed local player
        # protects the start against the detached downloader. Unknown native
        # DVB services do not belong to a provider account.
        leases = {identity for identity in _STATE.url_leases.get(key, ())
                  if _STATE.leases.get(identity, ("", 0))[0] == scope
                  and _STATE.leases[identity][1] > time.time() + 2}
        leased = bool(leases)
        if scope and (scope in _STATE.blocked or (not leased and scope != _STATE.playing_scope)):
            raise store.DownloadError("busy")
        old_scope, old_key = _STATE.playing_scope, _STATE.reference_key
        _STATE.playing_scope, _STATE.reference_key = scope, key
        _STATE.handoffs.update(leases)
    try:
        result = navigation.playService(reference)
        if isinstance(result, int) and result != 0:
            raise RuntimeError("playService returned {}".format(result))
    except Exception:
        with _STATE.condition:
            _STATE.playing_scope, _STATE.reference_key = old_scope, old_key
            _STATE.wake()
        raise
    with _STATE.condition:
        _STATE.wake()
    return result


class SessionGuard(object):
    def __init__(self, session):
        from enigma import eTimer
        self.session = session
        self.timer = eTimer()
        self.closed = False
        self.polls = 0
        self.events = getattr(getattr(session, "nav", None), "event", None)
        if self.events is not None and hasattr(self.events, "append"):
            self.events.append(self._navigation_event)
        try:
            self.connection = self.timer.timeout.connect(self.poll)
        except AttributeError:
            self.timer.callback.append(self.poll)
        self.poll()

    def poll(self):
        if self.closed:
            return
        active = True
        try:
            navigation = getattr(self.session, "nav", None)
            reference = navigation.getCurrentlyPlayingServiceReference() if navigation else None
            url = _reference_url(reference)
            key = _url_key(url)
            with _STATE.condition:
                conflict = key == _STATE.conflict_key
                # During native decoder startup getCurrentReference can be
                # temporarily empty; keep the start reservation for two ticks.
                if reference is not None:
                    _STATE.reference_key = key
                    _STATE.playing_scope = _STATE.urls.get(key, "")
                    self.empty_ticks = 0
                else:
                    self.empty_ticks = getattr(self, "empty_ticks", 0) + 1
                    if self.empty_ticks >= 4:
                        _STATE.reference_key = key
                        _STATE.playing_scope = ""
                _STATE.wake(poll=True)
                active = bool(_STATE.playing_scope or _STATE.busy or _STATE.leases
                              or _STATE.releases or _STATE.handoffs or conflict)
            if conflict and navigation:
                navigation.stopService()
        except Exception:
            pass
        self.polls += 1
        self.timer.start(ACTIVE_POLL_MS if active or self.polls < 3 else IDLE_POLL_MS, True)

    def _navigation_event(self, event):
        try:
            from enigma import iPlayableService
            relevant = event in (iPlayableService.evStart, iPlayableService.evEnd)
        except (ImportError, AttributeError):
            relevant = True
        if relevant:
            self.poll()

    def close(self):
        self.closed = True
        self.timer.stop()
        if self.events is not None:
            try:
                self.events.remove(self._navigation_event)
            except (AttributeError, ValueError):
                pass


def install(session):
    global _GUARD
    if session is not None and (_GUARD is None or _GUARD.session is not session):
        if _GUARD is not None:
            _GUARD.close()
        _GUARD = SessionGuard(session)


def uninstall():
    global _GUARD
    if _GUARD is not None:
        _GUARD.close()
        _GUARD = None

