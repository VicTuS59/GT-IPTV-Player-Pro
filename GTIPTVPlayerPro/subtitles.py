# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Small, image-compatible controller for embedded VOD subtitles."""

from .i18n import N_, _, localized_language_name
from .subtitle_runtime import OnlineSubtitleController
from .subssupport_bridge import SubsSupportBridge, subtitle_search_titles


_SUBTITLE_TYPES = {
    0: N_("DVB"),
    1: N_("Teletext"),
}

_PANGO_TYPES = {
    0: N_("Unknown"),
    1: N_("Embedded"),
    2: N_("SSA"),
    3: N_("ASS"),
    4: N_("SRT"),
    5: N_("VOB"),
    6: N_("PGS"),
    7: N_("WebVTT"),
}


def _language_name(code):
    """Return an Enigma2 language label, falling back to the track code."""
    return localized_language_name(code)


def subtitle_track_label(track, index):
    """Build a bounded label for the tuple formats used by Enigma2 images."""
    try:
        values = list(track)
    except TypeError:
        values = []

    language = _language_name(values[4] if len(values) > 4 else "")
    title = ""
    if len(values) > 5 and isinstance(values[5], str):
        title = " ".join(values[5].split())[:48]
    description = ""
    try:
        track_type = int(values[0])
    except (IndexError, TypeError, ValueError, OverflowError):
        track_type = -1

    if track_type == 2:
        try:
            description = _(_PANGO_TYPES.get(int(values[2]), N_("Embedded")))
        except (IndexError, TypeError, ValueError, OverflowError):
            description = _("Embedded")
    elif track_type in _SUBTITLE_TYPES:
        description = _(_SUBTITLE_TYPES[track_type])

    details = []
    for value in (language, title, description):
        if value and value not in details:
            details.append(value)
    detail = " • ".join(details)
    if not detail:
        detail = _("Subtitle")
    try:
        number = max(1, int(index))
    except (TypeError, ValueError, OverflowError):
        number = 1
    return "{}. {}".format(number, detail)[:120]


def subtitle_track_payload(track):
    """Normalise OpenATV's optional sixth field to Enigma2's five fields."""
    try:
        values = tuple(track)
    except TypeError:
        return None
    return values[:5] if len(values) >= 5 else None


def _choice_box_class():
    try:
        from Screens.ChoiceBox import ChoiceBox

        return ChoiceBox
    except Exception:
        return None


def _subtitle_display_class():
    try:
        from Screens.SubtitleDisplay import SubtitleDisplay

        return SubtitleDisplay
    except Exception:
        return None


def _online_subtitle_browser_class():
    try:
        from .subtitle_browser import GTOnlineSubtitleBrowserScreen

        return GTOnlineSubtitleBrowserScreen
    except Exception:
        return None


def _shared_subtitle_window():
    """Reuse Enigma2's singleton subtitle renderer when it is available."""
    try:
        from Screens.InfoBar import InfoBar

        instance = getattr(InfoBar, "instance", None)
        return getattr(instance, "subtitle_window", None)
    except Exception:
        return None


class EmbeddedSubtitleController(object):
    """Select and render only subtitle tracks exposed by the active service."""

    def __init__(
        self,
        session,
        on_message=None,
        on_changed=None,
        on_before_enable=None,
    ):
        self.session = session
        self.on_message = on_message
        self.on_changed = on_changed
        self.on_before_enable = on_before_enable
        self.selected_track = None
        self.subtitle_window = None
        self._owns_subtitle_window = False
        self._service_generation = 0
        self._closed = False

    def _notify(self, message):
        callback = self.on_message
        if callable(callback):
            try:
                callback(message)
            except Exception:
                pass

    def _changed(self):
        callback = self.on_changed
        if callable(callback):
            try:
                callback(self.selected_track)
            except Exception:
                pass

    def _subtitle_interface(self):
        navigation = getattr(self.session, "nav", None)
        getter = getattr(navigation, "getCurrentService", None)
        if not callable(getter):
            return None
        try:
            service = getter()
            subtitle_getter = getattr(service, "subtitle", None)
            return subtitle_getter() if callable(subtitle_getter) else None
        except Exception:
            return None

    def available_tracks(self):
        """Read decoder-local subtitle metadata only when explicitly asked."""
        subtitle = self._subtitle_interface()
        getter = getattr(subtitle, "getSubtitleList", None)
        if not callable(getter):
            return []
        try:
            tracks = getter() or []
            return [
                track
                for track in tracks
                if subtitle_track_payload(track) is not None
            ]
        except Exception:
            return []

    def open_selection(self, include_off=True, on_finished=None):
        if self._closed:
            return False
        tracks = self.available_tracks()
        if not tracks:
            self._notify(N_("No embedded subtitles are available."))
            return False

        choice_box = _choice_box_class()
        opener = getattr(self.session, "openWithCallback", None)
        if choice_box is None or not callable(opener):
            self._notify(N_("Subtitle selection is unavailable on this image."))
            return False

        choices = []
        if include_off:
            choices.append((_('Off'), None))
        selected_index = 0
        for index, track in enumerate(tracks, 1):
            choices.append((subtitle_track_label(track, index), track))
            payload = subtitle_track_payload(track)
            if (
                self.selected_track is not None
                and payload is not None
                and payload[:4] == self.selected_track[:4]
            ):
                selected_index = len(choices) - 1
        generation = self._service_generation
        completed = False

        def selection_finished(selection):
            nonlocal completed
            if completed:
                return
            completed = True
            try:
                if not self._closed and generation == self._service_generation:
                    self._selection_made(selection)
            finally:
                if callable(on_finished):
                    on_finished()

        try:
            opener(
                selection_finished,
                choice_box,
                _("Subtitles"),
                choices,
                selection=selected_index,
            )
        except TypeError:
            # Very old ChoiceBox variants have no initial-selection keyword;
            # title and list are the stable shared positional arguments.
            try:
                opener(
                    selection_finished,
                    choice_box,
                    _("Subtitles"),
                    choices,
                )
            except Exception:
                self._notify(N_("Subtitle selection is unavailable on this image."))
                return False
        except Exception:
            self._notify(N_("Subtitle selection is unavailable on this image."))
            return False
        return True

    def _selection_made(self, selection):
        if self._closed or not selection:
            return
        try:
            track = selection[1]
        except (IndexError, KeyError, TypeError):
            return
        if track is None:
            self.disable()
        else:
            self.enable(track)

    def _ensure_window(self):
        if self.subtitle_window is not None:
            return self.subtitle_window
        window = _shared_subtitle_window()
        owns_window = False
        if window is None:
            display_class = _subtitle_display_class()
            instantiate = getattr(self.session, "instantiateDialog", None)
            if display_class is None or not callable(instantiate):
                return None
            try:
                window = instantiate(display_class)
                owns_window = True
                animation = getattr(window, "setAnimationMode", None)
                if callable(animation):
                    animation(0)
            except Exception:
                return None
        try:
            window.hide()
        except Exception:
            return None
        self.subtitle_window = window
        self._owns_subtitle_window = owns_window
        return window

    def enable(self, track):
        payload = subtitle_track_payload(track)
        if self._closed or payload is None:
            return False
        subtitle = self._subtitle_interface()
        window = self._ensure_window()
        enable = getattr(subtitle, "enableSubtitles", None)
        instance = getattr(window, "instance", None)
        if not callable(enable) or window is None or instance is None:
            self._notify(N_("Subtitle selection is unavailable on this image."))
            return False
        undo_before_enable = None
        callback = self.on_before_enable
        if callable(callback):
            try:
                undo_before_enable = callback()
            except Exception:
                self._notify(N_("Subtitle selection is unavailable on this image."))
                return False
        try:
            result = enable(instance, payload)
            if isinstance(result, int) and result != 0:
                raise RuntimeError("enableSubtitles returned {}".format(result))
            window.show()
        except Exception:
            # A renderer may fail after the decoder accepted the track. Undo
            # both the decoder and controller state instead of leaving a
            # hidden or stale subtitle active.
            self.disable()
            if callable(undo_before_enable):
                try:
                    undo_before_enable()
                except Exception:
                    pass
            self._notify(N_("Could not enable the selected subtitle."))
            return False
        self.selected_track = payload
        self._changed()
        return True

    def disable(self):
        window = self.subtitle_window
        subtitle = self._subtitle_interface()
        disable = getattr(subtitle, "disableSubtitles", None)
        instance = getattr(window, "instance", None)
        if callable(disable) and instance is not None:
            try:
                disable(instance)
            except Exception:
                pass
        if window is not None:
            try:
                window.hide()
            except Exception:
                pass
        changed = self.selected_track is not None
        self.selected_track = None
        if changed:
            self._changed()
        return True

    def restore(self, track):
        """Restore a saved track without needlessly re-enabling its decoder."""
        payload = subtitle_track_payload(track)
        if self._closed or payload is None:
            return False
        if self.selected_track == payload:
            # SubsSupport construction hides InfoBar.subtitle_window even when
            # its load later fails.  The native decoder is still enabled in
            # this branch, so showing its window is safer than enabling the
            # same track a second time on image-specific subtitle interfaces.
            window = self._ensure_window()
            if window is None:
                self._notify(N_("Could not enable the selected subtitle."))
                return False
            try:
                window.show()
            except Exception:
                self._notify(N_("Could not enable the selected subtitle."))
                return False
            return True
        return self.enable(track)

    def reset_for_service_change(self):
        """Detach the active track before Enigma2 replaces the service."""
        self._service_generation += 1
        if self.selected_track is not None:
            self.disable()
        elif self.subtitle_window is not None:
            try:
                self.subtitle_window.hide()
            except Exception:
                pass

    def close(self):
        if self._closed:
            return
        self.reset_for_service_change()
        self._closed = True
        window = self.subtitle_window
        owns_window = self._owns_subtitle_window
        self.subtitle_window = None
        self._owns_subtitle_window = False
        if window is None:
            return
        deleter = getattr(self.session, "deleteDialog", None)
        if owns_window and callable(deleter):
            try:
                deleter(window)
                return
            except Exception:
                pass
        try:
            window.hide()
        except Exception:
            pass


class MovieSubtitleController(object):
    """Coordinate embedded VOD tracks with optional external SubsSupport."""

    def __init__(
        self,
        session,
        on_message=None,
        on_changed=None,
        title_provider=None,
        is_paused=None,
        pause_for_search=None,
        resume_after_search=None,
        bridge=None,
        online=None,
        position_provider=None,
        on_menu_open=None,
        on_menu_close=None,
    ):
        self.session = session
        self.on_message = on_message
        self.on_changed = on_changed
        self.title_provider = title_provider
        self.is_paused = is_paused
        self.pause_for_search = pause_for_search
        self.resume_after_search = resume_after_search
        self.on_menu_open = on_menu_open
        self.on_menu_close = on_menu_close
        self.bridge = bridge or SubsSupportBridge(
            session,
            on_message=on_message,
        )
        self.embedded = EmbeddedSubtitleController(
            session,
            on_message=on_message,
            on_changed=on_changed,
            on_before_enable=self._before_embedded_enable,
        )
        self.online = online or OnlineSubtitleController(
            session,
            metadata_provider=self._search_metadata,
            position_provider=position_provider,
            on_message=on_message,
            on_changed=on_changed,
            before_activate=self._before_online_enable,
            is_paused=self._paused,
        )
        self._search_generation = 0
        self._search_paused_by_us = False
        self._service_generation = 0
        self._menu_generation = 0
        self._menu_open = False
        self._closed = False

    def _begin_menu(self):
        if self._closed or self._menu_open:
            return False
        self._menu_generation += 1
        self._menu_open = True
        if callable(self.on_menu_open):
            try:
                self.on_menu_open()
            except Exception:
                pass
        return True

    def _notify(self, message):
        if callable(self.on_message):
            try:
                self.on_message(message)
            except Exception:
                pass

    def _end_menu(self, generation=None):
        if generation is not None and generation != self._menu_generation:
            return
        if not self._menu_open:
            return
        self._menu_open = False
        self._menu_generation += 1
        if callable(self.on_menu_close):
            try:
                self.on_menu_close()
            except Exception:
                pass

    def _open_embedded_selection(self, include_off=True):
        generation = self._menu_generation

        def finished():
            self._end_menu(generation)

        try:
            opened = self.embedded.open_selection(
                include_off=include_off, on_finished=finished,
            )
        except Exception:
            self._notify(N_("Subtitle selection is unavailable on this image."))
            opened = False
        if not opened:
            finished()
        return opened

    @property
    def selected_track(self):
        """Retain the R64 attribute used by older callers and tests."""
        return self.embedded.selected_track

    @property
    def external_loaded(self):
        return bool(self.bridge.is_loaded or self.online.is_loaded)

    def set_infobar_visible(self, visible):
        setter = getattr(self.online, "set_infobar_visible", None)
        if callable(setter):
            try:
                setter(visible)
            except Exception:
                pass

    def _changed(self, value):
        callback = self.on_changed
        if callable(callback):
            try:
                callback(value)
            except Exception:
                pass

    def _paused(self):
        callback = self.is_paused
        if callable(callback):
            try:
                return bool(callback())
            except Exception:
                pass
        return False

    def _search_metadata(self):
        callback = self.title_provider
        if not callable(callback):
            return {
                "title": "Movie",
                "year": "",
                "season": "",
                "episode": "",
                "content_type": "movie",
                "episode_title": "",
            }
        try:
            value = callback()
        except Exception:
            value = None
        if isinstance(value, dict):
            content_type = str(
                value.get("content_type", "movie") or "movie"
            ).strip().lower()
            return {
                "title": value.get("title", "Movie"),
                "year": value.get("year", ""),
                "season": value.get("season", ""),
                "episode": value.get("episode", ""),
                "content_type": content_type,
                "episode_title": value.get("episode_title", ""),
                "tmdb_id": value.get("tmdb_id", ""),
                "imdb_id": value.get("imdb_id", ""),
                "sd_id": value.get("sd_id", ""),
                "release_hint": value.get("release_hint", ""),
                "fps": value.get("fps", 0),
                "resolution": value.get("resolution", 0),
                "duration_seconds": value.get("duration_seconds", 0),
            }
        if isinstance(value, (tuple, list)):
            title = value[0] if value else "Movie"
            year = value[1] if len(value) > 1 else ""
            return {
                "title": title,
                "year": year,
                "season": value[2] if len(value) > 2 else "",
                "episode": value[3] if len(value) > 3 else "",
                "content_type": value[4] if len(value) > 4 else "movie",
                "episode_title": value[5] if len(value) > 5 else "",
                "tmdb_id": value[6] if len(value) > 6 else "",
                "imdb_id": value[7] if len(value) > 7 else "",
                "sd_id": value[8] if len(value) > 8 else "",
                "release_hint": value[9] if len(value) > 9 else "",
                "fps": value[10] if len(value) > 10 else 0,
                "resolution": value[11] if len(value) > 11 else 0,
                "duration_seconds": value[12] if len(value) > 12 else 0,
            }
        return {
            "title": getattr(value, "name", value) or "Movie",
            "year": getattr(value, "year", ""),
            "season": getattr(value, "season", ""),
            "episode": getattr(value, "episode", ""),
            "content_type": getattr(value, "content_type", "movie"),
            "episode_title": getattr(value, "episode_title", ""),
            "tmdb_id": getattr(value, "tmdb_id", ""),
            "imdb_id": getattr(value, "imdb_id", ""),
            "sd_id": getattr(value, "sd_id", ""),
            "release_hint": getattr(value, "release_hint", ""),
            "fps": getattr(value, "fps", 0),
            "resolution": getattr(value, "resolution", 0),
            "duration_seconds": getattr(value, "duration_seconds", 0),
        }

    def _movie_title_and_year(self):
        """Keep the R65 helper available for older callers and tests."""
        metadata = self._search_metadata()
        return metadata["title"], metadata["year"]

    def open_selection(self):
        if not self._begin_menu():
            return False

        choice_box = _choice_box_class()
        opener = getattr(self.session, "openWithCallback", None)
        if choice_box is None or not callable(opener):
            return self._open_embedded_selection()
        available = self.bridge.available()
        provider_name = getattr(self.bridge, "name", "SubsSupport")
        if not available:
            provider_name = "SubsSupport / SubsSupportPro"
        search_label = (
            _("Search subtitles with SubsSupport")
            if provider_name == "SubsSupport"
            else _("Search subtitles with %s") % provider_name
        )
        choices = [
            (_("Embedded subtitles"), "embedded"),
            ("GT IPTV PLAYER PRO • " + _("Subtitles"), "online_browser"),
        ]
        choices.append((search_label, "search"))
        if self.online.is_loaded:
            choices.append(
                (_("Independent subtitle synchronization"), "online_synchronize")
            )
        if self.bridge.is_loaded:
            choices.append(
                (_("External subtitle synchronization"), "synchronize")
            )
        choices.append((_('Off'), "off"))
        generation = self._menu_generation
        completed = False

        def selection_finished(selection):
            nonlocal completed
            if completed:
                return
            completed = True
            if not self._closed and generation == self._menu_generation:
                self._menu_selection(selection)

        try:
            opener(
                selection_finished,
                choice_box,
                _("Subtitles"),
                choices,
            )
        except Exception:
            return self._open_embedded_selection()
        return True

    def _menu_selection(self, selection):
        if self._closed or not selection:
            self._end_menu()
            return
        try:
            action = selection[1]
        except (IndexError, KeyError, TypeError):
            self._end_menu()
            return
        if action == "embedded":
            # The unified menu owns the single Off action.  Canceling this
            # submenu therefore leaves an active external subtitle untouched.
            self._open_embedded_selection(include_off=False)
        elif action == "online_browser":
            self._open_online_browser()
        elif (
            isinstance(action, (tuple, list))
            and len(action) == 2
            and action[0] == "online_search"
        ):
            self._open_online_search(action[1])
        elif action == "online_search":
            # Retain compatibility with an already-open pre-R106 ChoiceBox.
            self._open_online_search()
        elif action == "search":
            self._open_external_search()
        elif action == "online_synchronize":
            generation = self._menu_generation
            try:
                opened = self.online.open_sync(
                    on_finished=lambda: self._end_menu(generation)
                )
            except Exception:
                self._notify(N_("Subtitle synchronization is unavailable on this image."))
                opened = False
            if not opened:
                self._end_menu(generation)
        elif action == "synchronize":
            generation = self._menu_generation
            try:
                opened = self.bridge.open_sync(
                    on_finished=lambda: self._end_menu(generation)
                )
            except Exception:
                self._notify(N_("External subtitle synchronization is unavailable."))
                opened = False
            if not opened:
                self._end_menu(generation)
        elif action == "off":
            self.disable()
            self._end_menu()
        else:
            self._end_menu()

    def _open_online_browser(self):
        if self._closed:
            return False
        browser_class = _online_subtitle_browser_class()
        opener = getattr(self.session, "openWithCallback", None)
        if browser_class is None or not callable(opener):
            self._notify(N_("Subtitle result selection is unavailable on this image."))
            self._end_menu()
            return False
        self._search_generation += 1
        generation = self._search_generation
        menu_generation = self._menu_generation
        self._search_paused_by_us = False
        if callable(self.pause_for_search):
            try:
                self._search_paused_by_us = bool(self.pause_for_search())
            except Exception:
                self._search_paused_by_us = False
        obscurer = getattr(self.online, "set_ui_obscured", None)
        if callable(obscurer):
            try:
                obscurer(True)
            except Exception:
                pass
        completed = False

        def finished(unused_result=None):
            nonlocal completed
            if completed:
                return
            completed = True
            self._resume_after_search(generation)
            if (
                not self._closed
                and generation == self._search_generation
                and callable(obscurer)
            ):
                try:
                    obscurer(False)
                except Exception:
                    pass
            if generation == self._search_generation:
                self._search_generation += 1
            self._end_menu(menu_generation)

        try:
            opener(
                finished,
                browser_class,
                self.online,
                self._search_metadata(),
            )
            return True
        except Exception:
            self._notify(N_("Subtitle result selection is unavailable on this image."))
            finished()
            return False

    def _open_online_search(self, provider=None):
        if self._closed:
            return False
        self._search_generation += 1
        generation = self._search_generation
        menu_generation = self._menu_generation
        self._search_paused_by_us = False
        if callable(self.pause_for_search):
            try:
                self._search_paused_by_us = bool(self.pause_for_search())
            except Exception:
                self._search_paused_by_us = False

        completed = False

        def finished():
            nonlocal completed
            if completed:
                return
            completed = True
            self._resume_after_search(generation)
            if generation == self._search_generation:
                self._search_generation += 1
            self._end_menu(menu_generation)

        try:
            opened = self.online.search(
                automatic=False,
                on_finished=finished,
                provider=provider,
            )
        except Exception:
            self._notify(N_("Could not start the online subtitle search."))
            opened = False
        if not opened:
            finished()
        return opened

    def _open_external_search(self):
        if self._closed:
            return False
        if not self.bridge.available():
            self._notify(N_(
                "Subtitle search requires a working SubsSupport or SubsSupportPro installation."
            ))
            self._end_menu()
            return False
        self._search_generation += 1
        generation = self._search_generation
        self._search_paused_by_us = False
        if callable(self.pause_for_search):
            try:
                self._search_paused_by_us = bool(self.pause_for_search())
            except Exception:
                self._search_paused_by_us = False
        metadata = self._search_metadata()
        titles = subtitle_search_titles(
            title=metadata.get("title", ""),
            year=metadata.get("year", ""),
            season=metadata.get("season", ""),
            episode=metadata.get("episode", ""),
            content_type=metadata.get("content_type", "movie"),
            episode_title=metadata.get("episode_title", ""),
        )

        def finished(path):
            self._external_search_finished(generation, path)

        try:
            opened = self.bridge.open_search(titles, finished)
        except Exception:
            self._notify(N_("Could not open SubsSupport subtitle search."))
            opened = False
        if not opened:
            self._resume_after_search(generation)
            self._end_menu()
            return False
        return True

    def _resume_after_search(self, generation):
        if generation != self._search_generation:
            return
        paused_by_us = self._search_paused_by_us
        self._search_paused_by_us = False
        if callable(self.resume_after_search):
            try:
                self.resume_after_search(paused_by_us)
            except TypeError:
                if paused_by_us and self._paused():
                    try:
                        self.resume_after_search()
                    except Exception:
                        pass
            except Exception:
                pass

    def _external_search_finished(self, generation, path):
        if self._closed or generation != self._search_generation:
            return
        menu_generation = self._menu_generation
        try:
            if path:
                previous_embedded = self.embedded.selected_track
                previous_online = self.online.snapshot()

                def before_activate():
                    if previous_embedded is not None:
                        self.embedded.disable()
                    if previous_online is not None:
                        self.online.disable(notify=False)
                    return True

                try:
                    loaded = self.bridge.load(
                        path,
                        paused=self._paused(),
                        before_activate=before_activate,
                    )
                except Exception:
                    self._notify(N_("Could not load the downloaded subtitle."))
                    loaded = False
                if loaded:
                    self._changed(("external", path))
                else:
                    if previous_online is not None:
                        self.online.restore(previous_online)
                    elif previous_embedded is not None:
                        # Construction can hide the embedded window before load.
                        self.embedded.restore(previous_embedded)
        finally:
            self._resume_after_search(generation)
            if generation == self._search_generation:
                self._search_generation += 1
            self._end_menu(menu_generation)

    def _before_embedded_enable(self):
        """Turn off external rendering and return a failure rollback hook."""
        path = self.bridge.loaded_path
        online_state = self.online.snapshot()
        if not path and online_state is None:
            return None
        paused = self._paused()
        service_generation = self._service_generation
        self.bridge.disable()
        self.online.disable(notify=False)

        def restore_external():
            if (
                self._closed
                or service_generation != self._service_generation
                or self.bridge.is_loaded
                or self.online.is_loaded
            ):
                return
            if online_state is not None and self.online.restore(online_state):
                self._changed(("online", online_state.get("path")))
            elif path and self.bridge.load(path, paused=paused, notify=False):
                self._changed(("external", path))

        return restore_external

    def _before_online_enable(self):
        """Disable native/SubsSupport rendering and provide atomic rollback."""
        embedded_track = self.embedded.selected_track
        external_path = self.bridge.loaded_path
        paused = self._paused()
        service_generation = self._service_generation
        self.embedded.disable()
        self.bridge.disable()

        def restore_previous():
            if (
                self._closed
                or service_generation != self._service_generation
                or self.online.is_loaded
            ):
                return
            if external_path and self.bridge.load(
                external_path, paused=paused, notify=False
            ):
                self._changed(("external", external_path))
            elif embedded_track is not None:
                self.embedded.restore(embedded_track)

        return restore_previous

    def disable(self):
        had_external = self.bridge.is_loaded or self.online.is_loaded
        self.bridge.disable()
        self.online.disable(notify=False)
        self.embedded.disable()
        if had_external:
            self._changed(None)
        return True

    def snapshot_state(self):
        return {
            "embedded": self.embedded.selected_track,
            "external": self.bridge.loaded_path,
            "online": self.online.snapshot(),
        }

    def restore_state(self, state):
        if self._closed or not isinstance(state, dict):
            return False
        external_path = state.get("external")
        online_state = state.get("online")
        if online_state:
            self.embedded.disable()
            self.bridge.disable()
            if self.online.restore(online_state):
                self._changed(("online", online_state.get("path")))
                return True
            return False
        if external_path:
            self.embedded.disable()
            self.online.disable(notify=False)
            if self.bridge.load(external_path, paused=self._paused()):
                self._changed(("external", external_path))
                return True
            return False
        embedded_track = state.get("embedded")
        if embedded_track is not None:
            return self.embedded.enable(embedded_track)
        return True

    def pause(self):
        self.bridge.pause()
        self.online.pause()

    def resume(self):
        self.bridge.resume()
        self.online.resume()

    def prepare_seek(self, target_seconds, origin_seconds=None):
        return self.online.prepare_seek(target_seconds, origin_seconds=origin_seconds)

    def after_seek(self, position_seconds=None):
        self.bridge.after_seek()
        self.online.after_seek(position_seconds)

    def cancel_seek(self):
        self.bridge.after_seek()
        self.online.cancel_seek()

    def start_automatic_search(self):
        if self._closed:
            return False
        return self.online.start_automatic_search()

    def reset_for_service_change(self, resume_search=False):
        if (
            resume_search
            and self._search_paused_by_us
            and callable(self.resume_after_search)
        ):
            try:
                self.resume_after_search(True)
            except TypeError:
                try:
                    self.resume_after_search()
                except Exception:
                    pass
            except Exception:
                pass
        self._service_generation += 1
        self._search_generation += 1
        self._search_paused_by_us = False
        self.bridge.reset_for_service_change()
        self.online.reset_for_service_change()
        self.embedded.reset_for_service_change()
        self._end_menu()

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._service_generation += 1
        self._search_generation += 1
        self.bridge.close()
        self.online.close()
        self.embedded.close()
        self._end_menu()
