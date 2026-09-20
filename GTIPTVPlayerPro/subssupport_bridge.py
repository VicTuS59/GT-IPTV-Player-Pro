# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Lazy, optional bridge to OE-Alliance SubsSupport.

Nothing from SubsSupport is imported while GT IPTV Player Pro starts.  The
bridge also never passes an IPTV service reference or URL to a subtitle
provider: searches receive only bounded movie or episode metadata.
"""

import os
import stat
import unicodedata

from .i18n import N_


_API_UNSET = object()
_SUBSSUPPORT_API = _API_UNSET


def _log(message, error=None):
    # Keep optional-provider diagnostics local and free of paths/search terms.
    try:
        from .diagnostics import log_event

        log_event("subtitles", message, error=error)
    except Exception:
        pass


def _load_subssupport_api():
    """Return the public API needed by the bridge, or ``None`` safely."""
    global _SUBSSUPPORT_API
    if _SUBSSUPPORT_API is not _API_UNSET:
        return _SUBSSUPPORT_API
    from importlib import import_module

    providers = (
        ("SubsSupportPro", "SubsProSupport", "SubsProSearch", "initSubsProSettings"),
        ("SubsSupport", "SubsSupport", "SubsSearch", "initSubsSettings"),
    )
    api = None
    for name, support_name, search_name, settings_name in providers:
        try:
            package_name = "Plugins.Extensions." + name
            package = import_module(package_name)
            module = import_module(package_name + ".subtitles")
            support_class = getattr(package, support_name, None)
            if not callable(support_class):
                support_class = getattr(module, support_name)
            candidate = {
                "name": name,
                "SubsSupport": support_class,
                "E2SubsSeeker": getattr(module, "E2SubsSeeker"),
                "SubsSearch": getattr(module, search_name),
                "initSubsSettings": getattr(module, settings_name),
                # Standard and Pro 1.3 use the same optional screen name.
                "SubsStatusScreen": getattr(module, "SubsStatusScreen", None),
            }
            if not all(callable(candidate[key]) for key in (
                "SubsSupport", "E2SubsSeeker", "SubsSearch", "initSubsSettings"
            )):
                raise AttributeError("incomplete subtitle provider API")
        except Exception as error:
            _log(name + " unavailable", error)
            continue
        api = candidate
        _log(name + " selected")
        break
    _SUBSSUPPORT_API = api
    return api


def _bounded_text(value, limit=120):
    text = "".join(
        " " if unicodedata.category(character).startswith("C") else character
        for character in str(value or "")
    )
    text = " ".join(text.split())
    # A provider title must never accidentally become an IPTV/account URL.
    if "://" in text or text.lower().startswith(("http:", "https:")):
        return ""
    return text[:limit].strip()


def _bounded_episode_number(value):
    """Return one small non-negative season/episode number, or ``None``."""
    value = _bounded_text(value, limit=12)
    if not value or not value.isdigit():
        return None
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if 0 <= number <= 999 else None


def subtitle_search_titles(
    title,
    year="",
    season="",
    episode="",
    content_type="movie",
    episode_title="",
):
    """Build provider-safe movie or TV-episode candidates without a URL."""
    content_type = str(content_type or "").strip().lower()
    is_series = content_type == "series"
    title = _bounded_text(title, limit=100 if is_series else 120)
    if not title:
        title = "Series" if is_series else "Movie"
    candidates = []
    if is_series:
        season_number = _bounded_episode_number(season)
        episode_number = _bounded_episode_number(episode)
        if season_number is not None and episode_number is not None:
            candidates.append(
                "{} S{:02d}E{:02d}".format(
                    title,
                    season_number,
                    episode_number,
                )
            )
        episode_title = _bounded_text(episode_title)
        if episode_title and episode_title != title:
            candidates.append(episode_title)
        candidates.append(title)
        return candidates

    try:
        numeric_year = int(str(year or "").strip()[:4])
    except (TypeError, ValueError, OverflowError):
        numeric_year = 0
    if 1888 <= numeric_year <= 2100:
        candidates.append("{} ({})".format(title, numeric_year))
    candidates.append(title)
    return candidates


def _local_subtitle_path(args):
    """Accept only a real, local regular file returned by SubsSearch."""
    candidate = args[0] if args else None
    if isinstance(candidate, (tuple, list)):
        candidate = candidate[0] if candidate else None
    if isinstance(candidate, bytes):
        try:
            candidate = candidate.decode("utf-8")
        except UnicodeDecodeError:
            return None
    if not isinstance(candidate, str):
        return None
    candidate = candidate.strip()
    if (
        not candidate
        or not os.path.isabs(candidate)
        or "://" in candidate
        or "\x00" in candidate
    ):
        return None
    try:
        info = os.lstat(candidate)
    except (OSError, ValueError):
        return None
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        return None
    return candidate


class SubsSupportBridge(object):
    """Own a composition-style SubsSupport engine for one VOD service."""

    def __init__(self, session, on_message=None, api_loader=None):
        self.session = session
        self.on_message = on_message
        self._api_loader = api_loader or _load_subssupport_api
        self._api_value = _API_UNSET
        self._engine = None
        self.loaded_path = None
        self._search_generation = 0
        self._search_dialog = None
        self._search_seeker = None
        self._sync_dialog = None
        self._sync_generation = 0
        self._closed = False

    def _notify(self, message):
        callback = self.on_message
        if callable(callback):
            try:
                callback(message)
            except Exception:
                pass

    def _api(self):
        if self._api_value is _API_UNSET:
            try:
                self._api_value = self._api_loader()
            except Exception:
                self._api_value = None
        return self._api_value

    def available(self):
        return not self._closed and bool(self._api())

    @property
    def name(self):
        api = self._api()
        return api.get("name", "SubsSupport") if api else "SubsSupport"

    @property
    def is_loaded(self):
        return self._engine is not None and self.loaded_path is not None

    def open_search(self, titles, callback):
        """Open SubsSearch directly; never expose the current service URL."""
        if self._closed or self._search_dialog is not None:
            return False
        api = self._api()
        opener = getattr(self.session, "openWithCallback", None)
        if not api or not callable(opener):
            self._notify(N_("Could not open SubsSupport subtitle search."))
            return False
        try:
            settings = api["initSubsSettings"]()
            search_settings = getattr(settings, "search")
            seeker = api["E2SubsSeeker"](
                self.session,
                search_settings,
                debug=False,
            )
        except Exception:
            self._notify(N_("Could not open SubsSupport subtitle search."))
            return False

        self._search_generation += 1
        generation = self._search_generation
        self._search_seeker = seeker

        def search_finished(*args):
            if self._closed or generation != self._search_generation:
                return
            self._search_generation += 1
            self._stop_search_timers(self._search_dialog)
            self._search_dialog = None
            self._search_seeker = None
            result = _local_subtitle_path(args)
            if callable(callback):
                try:
                    callback(result)
                except Exception:
                    pass

        try:
            safe_titles = []
            for value in titles or ():
                value = _bounded_text(value)
                if value and value not in safe_titles:
                    safe_titles.append(value)
            if not safe_titles:
                safe_titles = ["Movie"]
            dialog = opener(
                search_finished,
                api["SubsSearch"],
                seeker,
                search_settings,
                None,
                safe_titles,
                True,
                False,
            )
            if generation == self._search_generation:
                self._search_dialog = dialog
                on_close = getattr(dialog, "onClose", None)
                if isinstance(on_close, list):
                    on_close.append(lambda: self._stop_search_timers(dialog))
        except Exception as error:
            _log(self.name + " search open failed", error)
            self._search_generation += 1
            self._search_dialog = None
            self._search_seeker = None
            self._close_seeker(seeker)
            self._notify(N_("Could not open SubsSupport subtitle search."))
            return False
        return True

    def cancel_pending_search(self):
        self._search_generation += 1
        dialog = self._search_dialog
        seeker = self._search_seeker
        self._search_dialog = None
        self._search_seeker = None
        self._stop_search_timers(dialog)
        close = getattr(dialog, "close", None)
        if callable(close):
            try:
                close()
                return
            except Exception:
                pass
        self._close_seeker(seeker)

    @staticmethod
    def _stop_search_timers(dialog):
        # Pro 1.3 stops its refresh timer on close but leaves the deferred
        # poster timer active. Stop both before Screen destroys its widgets.
        for name in ("posterTimer", "posterRefreshTimer"):
            timer = getattr(dialog, name, None)
            stop = getattr(timer, "stop", None)
            if callable(stop):
                try:
                    stop()
                except Exception:
                    pass

    @staticmethod
    def _close_seeker(seeker):
        for provider in getattr(seeker, "seekers", ()) or ():
            close = getattr(provider, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass

    def _new_engine(self):
        api = self._api()
        if not api:
            return None
        engine_class = api["SubsSupport"]
        candidates = (
            {
                "session": self.session,
                "autoLoad": False,
                "showGUIInfoMessages": False,
                "embeddedSupport": False,
                "preferEmbedded": False,
                "searchSupport": False,
            },
            {
                "session": self.session,
                "autoLoad": False,
                "showGUIInfoMessages": False,
                "embeddedSupport": False,
            },
            {
                "session": self.session,
                "autoLoad": False,
                "embeddedSupport": False,
            },
            {
                "session": self.session,
                "autoLoad": False,
            },
        )
        last_error = None
        for arguments in candidates:
            try:
                return engine_class(**arguments)
            except TypeError as error:
                message = str(error).lower()
                signature_mismatch = (
                    "unexpected keyword" in message
                    or "invalid keyword" in message
                    or "takes no keyword" in message
                    or "keyword arguments" in message
                )
                if not signature_mismatch:
                    raise
                last_error = error
        if last_error is not None:
            raise last_error
        return None

    def _play_position_available(self):
        """Avoid attaching an engine that would spin forever without PTS."""
        navigation = getattr(self.session, "nav", None)
        current = getattr(navigation, "getCurrentService", None)
        if not callable(current):
            return False
        try:
            service = current()
            seek_getter = getattr(service, "seek", None)
            seeker = seek_getter() if callable(seek_getter) else None
            position_getter = getattr(seeker, "getPlayPosition", None)
            result = position_getter() if callable(position_getter) else None
            return bool(
                isinstance(result, (tuple, list))
                and len(result) >= 2
                and int(result[0]) == 0
                and int(result[1]) >= 0
            )
        except Exception:
            return False

    @staticmethod
    def _call(engine, name, *args, **kwargs):
        method = getattr(engine, name, None)
        if not callable(method):
            raise AttributeError(name)
        return method(*args, **kwargs)

    def load(self, path, paused=False, notify=True, before_activate=None):
        """Load a downloaded file, retaining the old external file on error."""
        if self._closed or _local_subtitle_path((path,)) is None:
            if notify:
                self._notify(N_("Could not load the downloaded subtitle."))
            return False
        if not self._play_position_available():
            if notify:
                self._notify(
                    N_("External subtitle synchronization is unavailable.")
                )
            return False
        old_engine = self._engine
        old_path = self.loaded_path
        old_sync = {}
        if old_engine is not None:
            for getter, setter in (("getSubsDelay", "setSubsDelay"),
                                   ("getSubsFps", "setSubsFps")):
                method = getattr(old_engine, getter, None)
                if callable(method):
                    try:
                        value = method()
                        if value is not None:
                            old_sync[setter] = value
                    except Exception:
                        pass
        engine = old_engine
        if engine is None:
            try:
                engine = self._new_engine()
            except Exception as error:
                _log(self.name + " engine initialization failed", error)
                engine = None
            if engine is None:
                if notify:
                    self._notify(N_("Could not load the downloaded subtitle."))
                return False

        try:
            # This flag resets the subtitle engine, not the playing service.
            # Reusing a cue index from a longer file crashes the next timer.
            loaded = self._call(engine, "loadSubs", path, newService=True)
            if loaded is not True:
                raise RuntimeError("SubsSupport rejected subtitle")
            if callable(before_activate) and before_activate() is False:
                raise RuntimeError("subtitle activation was cancelled")
            if paused:
                self._call(engine, "pauseSubs")
            else:
                self._call(engine, "resumeSubs")
        except Exception as error:
            _log(self.name + " subtitle load failed", error)
            if old_engine is None:
                self._destroy_engine(engine)
                engine = None
            elif old_path:
                try:
                    restored = self._call(
                        old_engine,
                        "loadSubs",
                        old_path,
                        newService=True,
                    )
                    if restored is not True:
                        raise RuntimeError("SubsSupport restore failed")
                    for setter, value in old_sync.items():
                        self._call(old_engine, setter, value)
                    if paused:
                        self._call(old_engine, "pauseSubs")
                    else:
                        self._call(old_engine, "resumeSubs")
                except Exception:
                    self._close_sync_dialog()
                    self._destroy_engine(old_engine)
                    self._engine = None
                    self.loaded_path = None
            if notify:
                self._notify(N_("Could not load the downloaded subtitle."))
            return False

        self._engine = engine
        self.loaded_path = path
        return True

    def pause(self):
        if self.is_loaded:
            try:
                self._call(self._engine, "pauseSubs")
            except Exception:
                pass

    def resume(self):
        if self.is_loaded:
            try:
                self._call(self._engine, "resumeSubs")
            except Exception:
                pass

    def after_seek(self):
        if self.is_loaded:
            try:
                self._call(self._engine, "playAfterSeek")
            except Exception:
                pass

    def open_sync(self, on_finished=None):
        if not self.is_loaded:
            self._notify(N_("External subtitle synchronization is unavailable."))
            return False
        api = self._api()
        screen = api.get("SubsStatusScreen") if api else None
        callback_opener = getattr(self.session, "openWithCallback", None)
        opener = getattr(self.session, "open", None)
        engine = self._engine
        methods = (
            "setSubsDelay",
            "getSubsDelay",
            "subscribeOnSubsDelayChanged",
            "unsubscribeOnSubsDelayChanged",
            "setSubsDelayToNextSubtitle",
            "setSubsDelayToPrevSubtitle",
            "setSubsFps",
            "getSubsFps",
        )
        if (
            screen is None
            or (not callable(callback_opener) and not callable(opener))
            or any(not callable(getattr(engine, name, None)) for name in methods)
        ):
            self._notify(N_("External subtitle synchronization is unavailable."))
            return False
        self._close_sync_dialog()
        self._sync_generation += 1
        generation = self._sync_generation

        def sync_finished(*args):
            if generation != self._sync_generation:
                return
            self._sync_generation += 1
            self._sync_dialog = None
            if callable(on_finished):
                on_finished()

        def safe_engine_call(name, *args):
            if self._engine is not engine:
                return 0 if name == "getSubsDelay" else None
            method = getattr(engine, name, None)
            if not callable(method):
                return None
            try:
                return method(*args)
            except Exception:
                return 0 if name == "getSubsDelay" else None

        callbacks = tuple(
            (lambda name: lambda *args: safe_engine_call(name, *args))(name)
            for name in methods
        )

        try:
            arguments = (screen,) + callbacks
            if callable(callback_opener):
                dialog = callback_opener(sync_finished, *arguments)
            else:
                dialog = opener(*arguments)
                on_close = getattr(dialog, "onClose", None)
                if not isinstance(on_close, list):
                    close = getattr(dialog, "close", None)
                    if callable(close):
                        close()
                    raise RuntimeError("subtitle synchronization has no close hook")
                on_close.append(sync_finished)
            if generation == self._sync_generation:
                self._sync_dialog = dialog
        except Exception:
            self._sync_generation += 1
            self._sync_dialog = None
            self._notify(N_("External subtitle synchronization is unavailable."))
            return False
        return True

    def _close_sync_dialog(self):
        self._sync_generation += 1
        dialog = self._sync_dialog
        self._sync_dialog = None
        close = getattr(dialog, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass

    @staticmethod
    def _destroy_engine(engine):
        if engine is None:
            return
        # Composition is not a Screen, so neither cleanup hook is automatic.
        # Both calls are required; exitSubs alone leaves the hidden embedded
        # dialog allocated.  The caller guarantees one invocation per engine.
        for name in ("pauseSubs", "exitSubs", "exitEmbeddedSubs"):
            method = getattr(engine, name, None)
            if callable(method):
                try:
                    method()
                except Exception:
                    pass

    def disable(self):
        # SubsStatusScreen unsubscribes from engine callbacks in onClose, so it
        # must close while the composed SubsSupport instance is still alive.
        self._close_sync_dialog()
        engine = self._engine
        self._engine = None
        self.loaded_path = None
        self._destroy_engine(engine)
        return True

    def reset_for_service_change(self):
        self.cancel_pending_search()
        self.disable()

    def close(self):
        if self._closed:
            return
        self._closed = True
        self.cancel_pending_search()
        self.disable()
