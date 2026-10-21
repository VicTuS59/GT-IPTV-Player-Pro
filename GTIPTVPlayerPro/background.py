# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later

from collections import OrderedDict
import os
import time
import weakref

from .artwork_cache import ArtworkCacheBusy, PICON_CACHE

try:
    from Components.Pixmap import Pixmap
except ImportError:  # Source-side skin tests run without Enigma2 components.
    Pixmap = None

try:
    from enigma import ePicLoad
except ImportError:  # ePicLoad is absent on a few minimal Enigma2 images.
    ePicLoad = None

try:
    from enigma import eTimer
except ImportError:
    eTimer = None


_BACKGROUND_CACHE = OrderedDict()
_DECODE_INFLIGHT = {}
_DECODE_RETIRED = []
_DECODE_RETIRE_TIMER = None
_DECODE_RETIRE_CONNECTION = None
_BACKGROUND_CACHE_LIMIT = 64
_BACKGROUND_CACHE_DEFAULT_BYTES = 32 * 1024 * 1024
_BACKGROUND_CACHE_MAX_BYTES = 192 * 1024 * 1024
_BACKGROUND_CACHE_MAX_PIXELS = _BACKGROUND_CACHE_MAX_BYTES // 4
_BACKGROUND_CACHE_UHD_PIXELS = 3840 * 2160
_BACKGROUND_CACHE_BUDGET_BYTES = [_BACKGROUND_CACHE_DEFAULT_BYTES]
_BACKGROUND_CACHE_UHD_CONFIGURED = [False]
_BACKGROUND_CACHE_RECHECK_AT = [0.0]
_BACKGROUND_CACHE_BYTES = [0]
_BACKGROUND_CACHE_PIXELS = [0]
_BACKGROUND_READY_TIMEOUT_MS = 750
_COVER_MAX_PIXELS = 24 * 1024 * 1024


def cover_decode_size(width, height, ratio):
    """Smallest integer surface covering the widget, independent of source GCD."""
    width, height = max(1, int(width)), max(1, int(height))
    rw, rh = [int(value) for value in ratio]
    if not 0 < rw <= 65536 or not 0 < rh <= 65536:
        raise ValueError("invalid cover ratio")
    if width * rh >= height * rw:
        result = (width, (width * rh + rw - 1) // rw)
    else:
        result = ((height * rw + rh - 1) // rh, height)
    if result[0] * result[1] > _COVER_MAX_PIXELS:
        raise ValueError("cover surface exceeds pixel budget")
    return result


def _release_native_decoders(*args):
    _DECODE_RETIRED.clear()


def _retire_native_decoder(picload, connection):
    """Release the signal emitter after its native callback has returned."""
    global _DECODE_RETIRE_TIMER, _DECODE_RETIRE_CONNECTION
    _DECODE_RETIRED.append((picload, connection))
    if eTimer is None:
        # Only the host source tests have a decoder without a GUI timer.
        del _DECODE_RETIRED[:-2]
        return
    if _DECODE_RETIRE_TIMER is None:
        _DECODE_RETIRE_TIMER = eTimer()
        signal = getattr(_DECODE_RETIRE_TIMER, "timeout", None)
        connect = getattr(signal, "connect", None)
        if callable(connect):
            _DECODE_RETIRE_CONNECTION = connect(_release_native_decoders)
        else:
            _DECODE_RETIRE_TIMER.callback.append(_release_native_decoders)
    _DECODE_RETIRE_TIMER.start(0, True)

_SCREEN_ART_FAMILIES = {
    "global-neon-v0912.png": "screen-global-r89",
    "accounts-neon-v0912.png": "screen-accounts-r89",
    "dashboard-neon-r96.png": "screen-dashboard-r89",
    "live-category-background-r84.png": "screen-live-category-r89",
    "live-guide-background-r83.png": "screen-live-guide-r89",
    "screen-cinematic-glass-r90.png": "screen-cinematic-glass-r90",
    "screen-settings-glass-r90.png": "screen-settings-glass-r90",
    "screen-source-glass-r90.png": "screen-source-glass-r90",
    "screen-continue-glass-r91.png": "screen-continue-glass-r91",
    "screen-favorites-glass-r91.png": "screen-favorites-glass-r91",
}


def _screen_wallpaper_for_desktop(path, width, height):
    """Use the smallest opaque raster that covers the full-screen GUI widget."""
    stem = _SCREEN_ART_FAMILIES.get(os.path.basename(path or ""))
    if not stem:
        return path
    if stem.endswith(("-r90", "-r91")) and (width, height) in (
            (720, 576), (720, 480), (640, 480)):
        variant = {(720, 576): "sd576", (720, 480): "sd480",
                   (640, 480): "sd640"}[(width, height)]
    elif width <= 1280 and height <= 720:
        variant = "720"
    elif width <= 1920 and height <= 1080:
        variant = "1080"
    else:
        variant = "2160"
    candidate = os.path.join(os.path.dirname(path), "{}-{}.png".format(stem, variant))
    return candidate if os.path.isfile(candidate) else path


def _cache_get(key):
    cached = _BACKGROUND_CACHE.pop(key, None)
    if cached is not None:
        _BACKGROUND_CACHE[key] = cached
        return cached[0]
    return None


def _cache_prepare_uhd(pixels):
    """Retain more UHD pages when RAM allows; shrink under memory pressure."""
    now = time.monotonic()
    if (
        pixels < _BACKGROUND_CACHE_UHD_PIXELS
        or (_BACKGROUND_CACHE_UHD_CONFIGURED[0]
            and now < _BACKGROUND_CACHE_RECHECK_AT[0])
    ):
        return
    _BACKGROUND_CACHE_UHD_CONFIGURED[0] = True
    _BACKGROUND_CACHE_RECHECK_AT[0] = now + 5.0
    try:
        with open("/proc/meminfo", "r") as handle:
            lines = handle.read(65536).splitlines()
        memory = {}
        for line in lines:
            fields = line.split()
            if (
                len(fields) == 3
                and fields[0] in ("MemTotal:", "MemAvailable:")
                and fields[2] == "kB"
            ):
                memory[fields[0]] = int(fields[1]) * 1024
        total = memory["MemTotal:"]
        available = memory["MemAvailable:"]
        if total <= 0 or not 0 <= available <= total:
            return
    except (IOError, OSError, KeyError, TypeError, ValueError, OverflowError):
        _BACKGROUND_CACHE_BUDGET_BYTES[0] = _BACKGROUND_CACHE_DEFAULT_BYTES
        _cache_trim()
        return
    # At most one eighth of total RAM and one quarter of the available/cache
    # pool, capped at 192 MiB. Cached bytes are already in MemAvailable's cost;
    # include them once so repeated checks do not continually shrink the pool.
    _BACKGROUND_CACHE_BUDGET_BYTES[0] = max(
        _BACKGROUND_CACHE_DEFAULT_BYTES,
        min(
            _BACKGROUND_CACHE_MAX_BYTES,
            total // 8,
            (available + _BACKGROUND_CACHE_BYTES[0]) // 4,
        ),
    )
    _cache_trim()


def _cache_trim():
    budget = _BACKGROUND_CACHE_BUDGET_BYTES[0]
    while (
        len(_BACKGROUND_CACHE) > _BACKGROUND_CACHE_LIMIT
        or _BACKGROUND_CACHE_BYTES[0] > budget
        or _BACKGROUND_CACHE_PIXELS[0] > budget // 4
    ):
        # Large page surfaces may borrow unused space, but cannot pin the
        # entire pool once poster cards arrive. Keep at least a quarter for
        # small artwork (two visible 14-card pages at FHD).
        large = lambda key: key[1] >= 1280 and key[2] >= 720
        large_bytes = sum(value[1] for key, value in _BACKGROUND_CACHE.items() if large(key))
        large_limit = budget * 3 // 4
        victim = next((key for key in _BACKGROUND_CACHE
                       if large(key) and large_bytes > large_limit), None)
        if victim is None:
            victim = next(iter(_BACKGROUND_CACHE))
        removed = _BACKGROUND_CACHE.pop(victim)
        _BACKGROUND_CACHE_BYTES[0] -= removed[1]
        _BACKGROUND_CACHE_PIXELS[0] -= removed[2]


def _cache_put(key, pixmap, width, height):
    try:
        pixels = max(1, int(width)) * max(1, int(height))
    except (TypeError, ValueError, OverflowError):
        return
    cost = pixels * 4
    if (
        cost > _BACKGROUND_CACHE_MAX_BYTES
        or pixels > _BACKGROUND_CACHE_MAX_PIXELS
    ):
        return
    _cache_prepare_uhd(pixels)
    budget = _BACKGROUND_CACHE_BUDGET_BYTES[0]
    if cost > budget:
        return
    previous = _BACKGROUND_CACHE.pop(key, None)
    if previous is not None:
        _BACKGROUND_CACHE_BYTES[0] -= previous[1]
        _BACKGROUND_CACHE_PIXELS[0] -= previous[2]
    _BACKGROUND_CACHE[key] = (pixmap, cost, pixels)
    _BACKGROUND_CACHE_BYTES[0] += cost
    _BACKGROUND_CACHE_PIXELS[0] += pixels
    _cache_trim()


class AsyncBackgroundLoader(object):
    """Decode a PNG/JPEG away from Enigma2's GUI render path."""

    def __init__(
        self,
        screen,
        widget_name,
        path,
        on_loaded=None,
        cover_ratio=None,
        cover_alignment="center",
        stretch_to_widget=False,
        window_size=None,
    ):
        self.screen = screen
        self.widget_name = widget_name
        self.path = path
        self.on_loaded = on_loaded
        self.cover_ratio = cover_ratio
        self.cover_alignment = str(cover_alignment or "center").lower()
        self.stretch_to_widget = bool(stretch_to_widget)
        self.window_size = window_size
        self.picload = None
        self.connection = None
        self.closed = False
        self._decode_entry = None
        self._cache_release = None

    def _notify(self, loaded):
        release, self._cache_release = self._cache_release, None
        if release is not None:
            release()
        callback = self.on_loaded
        self.on_loaded = None
        if callback is not None:
            try:
                callback(bool(loaded))
            except Exception:
                pass

    @staticmethod
    def _cover_scale_flags(alignment):
        """Map a readable anchor to Enigma2 gPainter blit flags.

        Horizontal centre/right are 16/32 and vertical centre/bottom are
        64/128.  Top and left are represented by the absence of their axis
        flag.  Keeping both axes independent avoids the former `top` path
        accidentally enabling two horizontal flags at once.
        """
        parts = set(
            str(alignment or "center")
            .lower()
            .replace("-", "_")
            .split("_")
        )
        horizontal = 16
        vertical = 64
        if "left" in parts:
            horizontal = 0
        elif "right" in parts:
            horizontal = 32
        if "top" in parts:
            vertical = 0
        elif "bottom" in parts:
            vertical = 128
        return horizontal | vertical

    def start(self):
        if self.closed:
            return
        widget = self.screen[self.widget_name]
        instance = getattr(widget, "instance", None)
        if instance is None:
            return
        try:
            size = instance.size()
            width = int(size.width())
            height = int(size.height())
        except Exception:
            try:
                desktop = self.screen.instance.size()
                width = int(desktop.width())
                height = int(desktop.height())
            except Exception:
                width, height = 1280, 720
        if self.stretch_to_widget:
            self.path = _screen_wallpaper_for_desktop(self.path, width, height)
        if self.window_size is not None:
            # Decode the desktop-sized wallpaper, then let the native pixmap
            # clip its right/top window. No runtime bitmap editing is needed.
            width, height = [max(1, int(v)) for v in self.window_size]
            self.path = _screen_wallpaper_for_desktop(self.path, width, height)
            instance.setPixmapScale(self._cover_scale_flags("right_top"))
        if self._cache_release is not None:
            self._cache_release()
        try:
            self._cache_release = PICON_CACHE.pin(self.path)
        except ArtworkCacheBusy:
            # A worker already claimed this file. Let the normal artwork
            # failure/retry callback revalidate it instead of racing unlink.
            self._notify(False)
            return
        # Decode at the exact covering size, then let ePixmap clip the excess.
        # R112 rounded the scale up to a whole-number source multiple (for
        # example 1280x720 -> 2560x1440), which over-zoomed cinematic heroes.
        # The usual background/poster path retains its existing fit behavior.
        cover = self.cover_ratio and ePicLoad is not None
        if cover:
            try:
                decode_width, decode_height = cover_decode_size(width, height, self.cover_ratio)
                instance.setPixmapScale(
                    self._cover_scale_flags(self.cover_alignment)
                )
                width, height = decode_width, decode_height
            except ValueError:
                # A pathological source ratio must not allocate a giant native
                # surface or silently stretch the approved artwork layout.
                self._notify(False)
                return
            except (AttributeError, TypeError, ZeroDivisionError):
                cover = False
        # Minimal images without the native clipping API still fill the card.
        # A distinct key keeps stretched fallback data out of the normal cache.
        stretch = (self.stretch_to_widget or bool(self.cover_ratio and not cover)
                   or self.window_size is not None)
        if self.stretch_to_widget:
            # ePixmap fills the skin widget even when the PNG differs by a
            # fraction of a pixel from the receiver desktop aspect ratio.
            instance.setScale(1)
        try:
            info = os.stat(self.path)
            identity = (info.st_size, info.st_mtime_ns)
        except OSError:
            identity = None
        key = (self.path, width, height, identity)
        if stretch:
            key += ("window" if self.window_size is not None else "fill",)
        _cache_prepare_uhd(width * height)
        cached = _cache_get(key)
        if cached is not None:
            instance.setPixmap(cached)
            widget.show()
            self._notify(True)
            return
        if ePicLoad is None:
            self._fallback(instance)
            return
        # Parameter identity matters: a clipped window uses a different
        # aspect policy from a full desktop wallpaper, even at equal sizes.
        aspect = 0 if stretch and not self.stretch_to_widget else 1
        self._start_decode(key, aspect, instance)

    def _start_decode(self, key, aspect, instance=None):
        width, height = key[1:3]
        decode_key = key + (aspect,)
        self._detach_decode()
        entry = _DECODE_INFLIGHT.get(decode_key)
        if entry is not None:
            entry["listeners"].add(self)
            self._decode_entry = entry
            self.picload = entry["picload"]
            return
        entry = {"key": decode_key, "cache_key": key, "listeners": weakref.WeakSet([self]),
                 "picload": None, "connection": None}
        self._decode_entry = entry
        _DECODE_INFLIGHT[decode_key] = entry
        try:
            self.picload = entry["picload"] = ePicLoad()
            signal = self.picload.PictureData
            callback = lambda *args: AsyncBackgroundLoader._finish_decode(entry)
            connect = getattr(signal, "connect", None)
            if connect is not None:
                self.connection = entry["connection"] = connect(callback)
            else:
                signal.get().append(callback)
            self.picload.setPara([width, height, aspect, 1, False, 1, "#00000000"])
            result = self.picload.startDecode(self.path)
            if result not in (None, 0):
                raise RuntimeError("image decode failed")
        except Exception:
            self._detach_decode()
            if instance is not None:
                self._fallback(instance)
            else:
                self._notify(False)

    def start_page(self):
        transition = getattr(self, "_page_transition", None)
        if transition is None:
            self.start()
        else:
            transition.prepare(self)

    def _detach_decode(self):
        entry, self._decode_entry = self._decode_entry, None
        if entry is not None:
            entry["listeners"].discard(self)
            if not entry["listeners"] and _DECODE_INFLIGHT.get(entry["key"]) is entry:
                _DECODE_INFLIGHT.pop(entry["key"], None)
                entry["connection"] = entry["picload"] = None
        self.connection = self.picload = None

    @staticmethod
    def _finish_decode(entry):
        if _DECODE_INFLIGHT.get(entry["key"]) is not entry:
            return
        _DECODE_INFLIGHT.pop(entry["key"], None)
        _retire_native_decoder(entry["picload"], entry["connection"])
        try:
            pixmap = entry["picload"].getData()
        except Exception:
            pixmap = None
        key = entry["cache_key"]
        if pixmap is not None:
            _cache_put(key, pixmap, key[1], key[2])
        listeners = list(entry["listeners"])
        entry["listeners"].clear()
        for listener in listeners:
            if listener.closed or listener._decode_entry is not entry:
                continue
            listener._decode_entry = None
            listener.picload = listener.connection = None
            success = False
            try:
                if pixmap is not None:
                    success = listener._accept_pixmap(pixmap)
            except Exception:
                pass
            listener._notify(success)
        entry["connection"] = entry["picload"] = None

    def _accept_pixmap(self, pixmap):
        self.screen[self.widget_name].instance.setPixmap(pixmap)
        self.screen[self.widget_name].show()
        return True

    def _decoded(self, key, *args):
        if self._decode_entry is not None:
            self._finish_decode(self._decode_entry)

    def _fallback(self, instance):
        try:
            if self.cover_ratio or self.stretch_to_widget:
                instance.setScale(1)
            instance.setPixmapFromFile(self.path)
            self.screen[self.widget_name].show()
            self._notify(True)
        except Exception:
            self._notify(False)

    def close(self):
        self.closed = True
        self._detach_decode()
        self._notify(False)


def prepared_pixmap_key(path, size, cover_ratio=None):
    """Use exactly the normal foreground decoder's dimensions and file key."""
    width, height = [max(1, int(value)) for value in size]
    if cover_ratio:
        if ePicLoad is None:
            return None
        width, height = cover_decode_size(width, height, cover_ratio)
    try:
        details = os.stat(path)
    except OSError:
        return None
    return (path, width, height, (details.st_size, details.st_mtime_ns))


def pixmap_prepared(path, size, cover_ratio=None):
    key = prepared_pixmap_key(path, size, cover_ratio)
    return key is not None and _cache_get(key) is not None


class PreparedPixmapLoader(AsyncBackgroundLoader):
    """GUI-scheduled native decode into the existing bounded cache, no widget."""
    def __init__(self, path, size, cover_ratio=None, on_loaded=None):
        super(PreparedPixmapLoader, self).__init__(None, None, path,
            cover_ratio=cover_ratio, on_loaded=on_loaded)
        self.size = size

    def start(self):
        if self.closed:
            return
        try:
            key = prepared_pixmap_key(self.path, self.size, self.cover_ratio)
            if key is None or ePicLoad is None:
                self._notify(False)
                return
            self._cache_release = PICON_CACHE.pin(self.path)
            if _cache_get(key) is not None:
                self._notify(True)
                return
            self._start_decode(key, 1)
        except (OSError, ValueError, TypeError, RuntimeError):
            self._notify(False)

    def _accept_pixmap(self, pixmap):
        return True


class NativeAlphaPixmapLoader(object):
    """Load small RGBA artwork without ePicLoad flattening its alpha."""

    def __init__(self, screen, widget_name, path, on_loaded=None):
        self.screen = screen
        self.widget_name = widget_name
        self.path = path
        self.on_loaded = on_loaded
        self.closed = False
        self._cache_release = None

    def _notify(self, loaded):
        release, self._cache_release = self._cache_release, None
        if release is not None:
            release()
        callback = self.on_loaded
        self.on_loaded = None
        if callback is not None:
            try:
                callback(bool(loaded))
            except Exception:
                pass

    def start(self):
        if self.closed:
            return
        try:
            widget = self.screen[self.widget_name]
            instance = getattr(widget, "instance", None)
            if instance is None:
                return
            if self._cache_release is not None:
                self._cache_release()
            self._cache_release = PICON_CACHE.pin(self.path)
            try:
                instance.setScale(1)
            except Exception:
                pass
            # The native loader retains PNG/GIF alpha. ePicLoad composites
            # transparent pixels onto its decode background, which produces
            # the black rectangles seen around provider channel logos.
            instance.setPixmapFromFile(self.path)
            if self.closed:
                return
            widget.show()
            self._notify(True)
        except Exception:
            self._notify(False)

    def close(self):
        self.closed = True
        self._notify(False)


class _PageBackgroundTransition(object):
    """Keep the previous native window until this page's local art is ready.

    The session still owns execution, actions, summaries and the dialog stack.
    Only this screen's first show is deferred.  Showing the previous *native*
    window does not execute its screen hooks or restart its player/workers.
    """

    def __init__(self, screen):
        self.screen = screen
        self.original_show = screen.show
        self.original_hide = screen.hide
        self.pending = set()
        self.show_requested = False
        self.presented = bool(getattr(screen, "already_shown", False)
                              or getattr(screen, "alreadyShown", False))
        self.closed = False
        self.expired = False
        self.timer_running = False
        self.handoff_timer = None
        self.handoff_connection = None
        self.handoff_pending = False
        self.previous = None
        self.held_parent = None
        session = screen.session
        previous = getattr(session, "current_dialog", None)
        stack = getattr(session, "dialog_stack", ()) or ()
        # pushCurrent remembers visibility before hiding the previous page.
        # An intentionally hidden infobar must stay hidden during entry.
        if previous is not None and previous is not screen:
            was_shown = bool(getattr(previous, "shown", False))
            if stack and stack[-1][0] is previous:
                was_shown = bool(stack[-1][1])
            if was_shown:
                self.previous = weakref.ref(previous)
        self.timer = eTimer()
        signal = getattr(self.timer, "timeout", None)
        connect = getattr(signal, "connect", None)
        if callable(connect):
            self.connection = connect(self._timeout)
        else:
            self.connection = None
            self.timer.callback.append(self._timeout)
        screen.show = self.show
        screen.hide = self.hide
        # This must precede loader.close(): cancellation notifies its listener.
        screen.onClose.append(self.close)

    def add(self, loader):
        if self.presented or self.closed:
            return
        self.pending.add(loader)
        loader.on_loaded = lambda loaded: self._loaded(loader, loaded)

    def prepare(self, loader):
        loader.start()
        if self.pending and not self.presented and not self.closed:
            self._hold_previous()

    def _hold_previous(self):
        if self.held_parent is not None or self.previous is None:
            return
        parent = self.previous()
        visited = set()
        while parent is not None and id(parent) not in visited:
            visited.add(id(parent))
            transition = getattr(parent, "_gt_background_transition", None)
            if transition is None or transition.presented:
                break
            parent = transition.previous() if transition.previous is not None else None
        if parent is None or getattr(parent, "shown", True):
            return
        instance = getattr(parent, "instance", None)
        if instance is None:
            return
        try:
            holders = getattr(parent, "_gt_background_holders", None)
            if holders is None:
                holders = parent._gt_background_holders = weakref.WeakSet()
            first = not holders
            holders.add(self)
            self.held_parent = weakref.ref(parent)
            if first:
                instance.show()
        except (AttributeError, RuntimeError, TypeError):
            self._release_previous()

    def _release_previous(self):
        reference, self.held_parent = self.held_parent, None
        parent = reference() if reference is not None else None
        if parent is None:
            return
        holders = getattr(parent, "_gt_background_holders", None)
        if holders is None:
            return
        holders.discard(self)
        if holders:
            return
        del parent._gt_background_holders
        transition = getattr(parent, "_gt_background_transition", None)
        if transition is not None and transition.handoff_pending:
            transition._finish_handoff()
            return
        # The session may already have restored this page after EXIT.
        if not getattr(parent, "shown", True):
            instance = getattr(parent, "instance", None)
            if instance is not None:
                try:
                    instance.hide()
                except (AttributeError, RuntimeError):
                    pass

    def _defer_window_hide(self):
        # Session.pushCurrent hides a page *before* constructing its child.
        # Cover that synchronous construction too, including warm-cache opens.
        if self.closed or self.screen.instance is None:
            return
        if self.handoff_timer is None:
            self.handoff_timer = eTimer()
            signal = getattr(self.handoff_timer, "timeout", None)
            connect = getattr(signal, "connect", None)
            if callable(connect):
                self.handoff_connection = connect(self._finish_handoff)
            else:
                self.handoff_timer.callback.append(self._finish_handoff)
        try:
            self.screen.instance.show()
            self.handoff_pending = True
            self.handoff_timer.start(0, True)
        except (AttributeError, RuntimeError):
            self.handoff_pending = False

    def _finish_handoff(self):
        if not self.handoff_pending:
            return
        self.handoff_pending = False
        self.handoff_timer.stop()
        screen = self.screen
        if (screen is None or getattr(screen, "shown", True)
                or getattr(screen, "_gt_background_holders", None)):
            return
        instance = getattr(screen, "instance", None)
        if instance is not None:
            try:
                instance.hide()
            except (AttributeError, RuntimeError):
                pass

    def _stop_timer(self):
        self.timer_running = False
        if self.timer is not None:
            self.timer.stop()

    def _foreground(self):
        screen = self.screen
        session = getattr(screen, "session", None)
        return (session is not None
                and getattr(session, "current_dialog", None) is screen
                and getattr(session, "in_exec", True)
                and getattr(screen, "execing", True))

    def _loaded(self, loader, loaded):
        self.pending.discard(loader)
        if self.closed or self.pending:
            return
        self._stop_timer()
        if self.show_requested and self._foreground():
            self.show()

    def _timeout(self):
        # Broken/missing artwork must never trap the user on a hidden page.
        self.timer_running = False
        if self.closed:
            return
        self.expired = True
        if self.show_requested and self._foreground():
            self.show()

    def show(self):
        if self.closed:
            return
        if getattr(self.screen, "instance", None) is None:
            return self.original_show()
        self.show_requested = True
        if (self.pending and not self.presented and not self.expired
                and self._foreground()):
            self._hold_previous()
            if not self.timer_running:
                self.timer_running = True
                self.timer.start(_BACKGROUND_READY_TIMEOUT_MS, True)
            return
        self._stop_timer()
        self._finish_handoff()
        self.presented = True
        try:
            return self.original_show()
        finally:
            # Paint the prepared page before uncovering the old window.
            self._release_previous()

    def hide(self):
        if self.closed:
            return
        self.show_requested = False
        self._stop_timer()
        session = getattr(self.screen, "session", None)
        stack = getattr(session, "dialog_stack", ()) or ()
        opening_child = (getattr(self.screen, "shown", False)
                         and stack and stack[-1][0] is self.screen
                         and getattr(session, "current_dialog", None) is self.screen)
        previous = self.held_parent() if self.held_parent is not None else None
        # A system popup/standby can suspend this page while it is loading.
        # Do not leave a native window visible outside the session's control.
        self._release_previous()
        result = self.original_hide()
        if opening_child:
            if self.presented:
                self._defer_window_hide()
            elif previous is not None:
                transition = getattr(previous, "_gt_background_transition", None)
                if transition is not None:
                    transition._defer_window_hide()
        return result

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.show_requested = False
        self._stop_timer()
        self._finish_handoff()
        self._release_previous()
        screen, self.screen = self.screen, None
        screen.show = self.original_show
        screen.hide = self.original_hide
        self.original_show = self.original_hide = None
        self.pending.clear()
        self.previous = None
        # close() may be reached by a screen hook inside our timeout signal.
        _retire_native_decoder(self.timer, self.connection)
        if self.handoff_timer is not None:
            self.handoff_timer.stop()
            _retire_native_decoder(self.handoff_timer, self.handoff_connection)
        self.handoff_timer = self.handoff_connection = None
        self.connection = self.timer = None


def _prepare_page_background(screen, loader):
    transition = getattr(screen, "_gt_background_transition", None)
    session = getattr(screen, "session", None)
    if (transition is None and eTimer is not None
            and session is not None and hasattr(session, "current_dialog")
            and callable(getattr(screen, "show", None))
            and callable(getattr(screen, "hide", None))
            and hasattr(screen, "onClose")):
        transition = screen._gt_background_transition = _PageBackgroundTransition(screen)
    if transition is not None:
        transition.add(loader)
        # OpenPLi executes plain layout functions as skin code: keep a bound
        # method here, like the original loader.start callback.
        loader._page_transition = transition
    return loader


def attach_background(screen, widget_name, path, stretch_to_widget=True):
    """Fill the full skin widget with its wallpaper after layout.

    Every caller uses this helper for a screen-wide surface.  A receiver's
    desktop can differ slightly from the wallpaper aspect ratio, so the
    historical fit mode left narrow live-video strips along its sides.
    Poster and picon artwork use attach_pixmap and keep their fit behavior.
    """
    if Pixmap is None:
        raise RuntimeError("Enigma2 pixmap widgets are unavailable")
    screen[widget_name] = Pixmap()
    loader = _prepare_page_background(screen, AsyncBackgroundLoader(
        screen, widget_name, path, stretch_to_widget=stretch_to_widget
    ))
    screen._gt_background_loader = loader
    if hasattr(screen, "onLayoutFinish"):
        screen.onLayoutFinish.append(loader.start_page)
    if hasattr(screen, "onClose"):
        screen.onClose.append(loader.close)
    return loader


def attach_background_window(screen, widget_name, path, desktop_size):
    """Use a clean wallpaper only inside a right-hand native UI viewport."""
    screen[widget_name] = Pixmap()
    loader = _prepare_page_background(screen, AsyncBackgroundLoader(
        screen, widget_name, path, window_size=desktop_size))
    screen._gt_window_background_loader = loader
    screen.onLayoutFinish.append(loader.start_page)
    screen.onClose.append(loader.close)
    return loader


def _close_dynamic_pixmaps(screen):
    loaders = getattr(screen, "_gt_pixmap_loaders", {})
    for loader in list(loaders.values()):
        loader.close()
    loaders.clear()


def attach_pixmap(
    screen,
    widget_name,
    path,
    key=None,
    on_loaded=None,
    cover_ratio=None,
    cover_alignment="center",
):
    """Decode a dynamic PNG/JPEG and keep ePicLoad alive until completion."""
    if Pixmap is None:
        raise RuntimeError("Enigma2 pixmap widgets are unavailable")
    loaders = getattr(screen, "_gt_pixmap_loaders", None)
    if loaders is None:
        loaders = {}
        screen._gt_pixmap_loaders = loaders
        if hasattr(screen, "onClose"):
            screen.onClose.append(lambda: _close_dynamic_pixmaps(screen))
    loader_key = key if key is not None else widget_name
    previous = loaders.pop(loader_key, None)
    if previous is not None:
        previous.close()
    loader = AsyncBackgroundLoader(
        screen,
        widget_name,
        path,
        on_loaded=on_loaded,
        cover_ratio=cover_ratio,
        cover_alignment=cover_alignment,
    )
    loaders[loader_key] = loader
    widget = screen[widget_name]
    if (
        getattr(widget, "instance", None) is None
        and hasattr(screen, "onLayoutFinish")
    ):
        screen.onLayoutFinish.append(loader.start)
    else:
        loader.start()
    return loader


def attach_native_alpha_pixmap(
    screen,
    widget_name,
    path,
    key=None,
    on_loaded=None,
):
    """Load small transparent artwork through Enigma2's native alpha path."""
    if Pixmap is None:
        raise RuntimeError("Enigma2 pixmap widgets are unavailable")
    loaders = getattr(screen, "_gt_pixmap_loaders", None)
    if loaders is None:
        loaders = {}
        screen._gt_pixmap_loaders = loaders
        if hasattr(screen, "onClose"):
            screen.onClose.append(lambda: _close_dynamic_pixmaps(screen))
    loader_key = key if key is not None else widget_name
    previous = loaders.pop(loader_key, None)
    if previous is not None:
        previous.close()
    loader = NativeAlphaPixmapLoader(
        screen,
        widget_name,
        path,
        on_loaded=on_loaded,
    )
    loaders[loader_key] = loader
    widget = screen[widget_name]
    if (
        getattr(widget, "instance", None) is None
        and hasattr(screen, "onLayoutFinish")
    ):
        screen.onLayoutFinish.append(loader.start)
    else:
        loader.start()
    return loader
