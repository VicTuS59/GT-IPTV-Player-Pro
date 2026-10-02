# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later

from collections import OrderedDict
from math import ceil, gcd
import os

try:
    from Components.Pixmap import Pixmap
except ImportError:  # Source-side skin tests run without Enigma2 components.
    Pixmap = None

try:
    from enigma import ePicLoad
except ImportError:  # ePicLoad is absent on a few minimal Enigma2 images.
    ePicLoad = None


_BACKGROUND_CACHE = OrderedDict()
_BACKGROUND_CACHE_LIMIT = 32
_BACKGROUND_CACHE_MAX_BYTES = 32 * 1024 * 1024
_BACKGROUND_CACHE_MAX_PIXELS = 8 * 1000 * 1000
_BACKGROUND_CACHE_BYTES = [0]
_BACKGROUND_CACHE_PIXELS = [0]

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
    previous = _BACKGROUND_CACHE.pop(key, None)
    if previous is not None:
        _BACKGROUND_CACHE_BYTES[0] -= previous[1]
        _BACKGROUND_CACHE_PIXELS[0] -= previous[2]
    _BACKGROUND_CACHE[key] = (pixmap, cost, pixels)
    _BACKGROUND_CACHE_BYTES[0] += cost
    _BACKGROUND_CACHE_PIXELS[0] += pixels
    while (
        len(_BACKGROUND_CACHE) > _BACKGROUND_CACHE_LIMIT
        or _BACKGROUND_CACHE_BYTES[0] > _BACKGROUND_CACHE_MAX_BYTES
        or _BACKGROUND_CACHE_PIXELS[0] > _BACKGROUND_CACHE_MAX_PIXELS
    ):
        removed = _BACKGROUND_CACHE.popitem(last=False)[1]
        _BACKGROUND_CACHE_BYTES[0] -= removed[1]
        _BACKGROUND_CACHE_PIXELS[0] -= removed[2]


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
    ):
        self.screen = screen
        self.widget_name = widget_name
        self.path = path
        self.on_loaded = on_loaded
        self.cover_ratio = cover_ratio
        self.cover_alignment = str(cover_alignment or "center").lower()
        self.stretch_to_widget = bool(stretch_to_widget)
        self.picload = None
        self.connection = None
        self.closed = False

    def _notify(self, loaded):
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
        # Decode at the exact covering size, then let ePixmap clip the excess.
        # R112 rounded the scale up to a whole-number source multiple (for
        # example 1280x720 -> 2560x1440), which over-zoomed cinematic heroes.
        # The usual background/poster path retains its existing fit behavior.
        cover = self.cover_ratio and ePicLoad is not None
        if cover:
            try:
                ratio_w, ratio_h = self.cover_ratio
                ratio_w = int(ratio_w)
                ratio_h = int(ratio_h)
                divisor = gcd(ratio_w, ratio_h)
                unit_w = ratio_w // divisor
                unit_h = ratio_h // divisor
                multiple = int(ceil(max(
                    width / float(unit_w),
                    height / float(unit_h),
                )))
                decode_width = unit_w * multiple
                decode_height = unit_h * multiple
                instance.setPixmapScale(
                    self._cover_scale_flags(self.cover_alignment)
                )
                width, height = decode_width, decode_height
            except (AttributeError, TypeError, ValueError, ZeroDivisionError):
                cover = False
        # Minimal images without the native clipping API still fill the card.
        # A distinct key keeps stretched fallback data out of the normal cache.
        stretch = self.stretch_to_widget or bool(self.cover_ratio and not cover)
        if self.stretch_to_widget:
            # ePixmap fills the skin widget even when the PNG differs by a
            # fraction of a pixel from the receiver desktop aspect ratio.
            instance.setScale(1)
        key = (self.path, width, height)
        if stretch:
            key += ("fill",)
        cached = _cache_get(key)
        if cached is not None:
            instance.setPixmap(cached)
            widget.show()
            self._notify(True)
            return
        if ePicLoad is None:
            self._fallback(instance)
            return
        try:
            self.picload = ePicLoad()
            signal = self.picload.PictureData
            connect = getattr(signal, "connect", None)
            if connect is not None:
                self.connection = connect(
                    lambda *args: self._decoded(key, *args)
                )
            else:
                signal.get().append(
                    lambda *args: self._decoded(key, *args)
                )
            self.picload.setPara(
                [width, height, 0 if stretch and not self.stretch_to_widget else 1,
                 1, False, 1, "#00000000"]
            )
            result = self.picload.startDecode(self.path)
            if result not in (None, 0):
                raise RuntimeError("image decode failed")
        except Exception:
            self.picload = None
            self._fallback(instance)

    def _decoded(self, key, *args):
        if self.closed or self.picload is None:
            return
        try:
            pixmap = self.picload.getData()
            if pixmap is not None:
                _cache_put(key, pixmap, key[1], key[2])
                self.screen[self.widget_name].instance.setPixmap(pixmap)
                self.screen[self.widget_name].show()
                self._notify(True)
                return
        except Exception:
            pass
        self._notify(False)

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
        self._notify(False)
        self.connection = None
        self.picload = None


class NativeAlphaPixmapLoader(object):
    """Load small RGBA artwork without ePicLoad flattening its alpha."""

    def __init__(self, screen, widget_name, path, on_loaded=None):
        self.screen = screen
        self.widget_name = widget_name
        self.path = path
        self.on_loaded = on_loaded
        self.closed = False

    def _notify(self, loaded):
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
    loader = AsyncBackgroundLoader(
        screen, widget_name, path, stretch_to_widget=stretch_to_widget
    )
    screen._gt_background_loader = loader
    if hasattr(screen, "onLayoutFinish"):
        screen.onLayoutFinish.append(loader.start)
    if hasattr(screen, "onClose"):
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
