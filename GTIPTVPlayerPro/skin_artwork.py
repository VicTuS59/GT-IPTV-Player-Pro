# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-2.0-or-later
"""Reuse bundled skin PNGs in the existing bounded native artwork pool.

Only plain plugin Pixmap components participate, during their own screen's
applySkin. The receiver's skin module and other screens are never patched.
Attribute handling, native geometry, alpha/scaling and initial visibility still
belong to the image's normal Pixmap.applySkin implementation.
"""
import os
from types import MethodType

from .paths import plugin_path


class _PageImages(object):
    def __init__(self):
        self.root = os.path.abspath(plugin_path('skin', 'images')) + os.sep
        self.identities = {}

    def path(self, attributes):
        paths = [value for key, value in attributes if key == 'pixmap']
        if len(paths) != 1 or any(key in ('offset', 'pixmaps') for key, value in attributes):
            return None
        path = paths[0]
        if (not isinstance(path, str) or not os.path.isabs(path)
                or not path.endswith('.png') or not os.path.abspath(path).startswith(self.root)):
            return None
        return path

    def load(self, path, desktop, width, height):
        from skin import loadPixmap
        from .background import _cache_get, _cache_put

        if path not in self.identities:
            info = os.stat(path)
            self.identities[path] = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
        key = (path, width, height, self.identities[path],
               'skin-png-r29', id(desktop), id(loadPixmap))
        pixmap = _cache_get(key)
        if pixmap is not None:
            return pixmap
        # Delegate to the receiver's own loader, preserving PNG conversion and
        # geometry support instead of selecting a different decoder or asset.
        pixmap = loadPixmap(path, desktop, width, height)
        if pixmap is not None:
            try:
                size = pixmap.size()
                _cache_put(key, pixmap, size.width(), size.height())
            except (AttributeError, TypeError, ValueError, RuntimeError):
                # Minimal images may expose a bitmap without size(). It is
                # still valid for the native widget; simply do not retain it.
                pass
        return pixmap


def _apply_pixmap(widget, original, images, desktop, screen, *args, **kwargs):
    attributes = getattr(widget, 'skinAttributes', None)
    if not attributes:
        return original(desktop, screen, *args, **kwargs)
    path = images.path(attributes)
    if path is None:
        return original(desktop, screen, *args, **kwargs)
    widget.skinAttributes = [(key, value) for key, value in attributes if key != 'pixmap']
    try:
        original(desktop, screen, *args, **kwargs)
    finally:
        widget.skinAttributes = attributes
    try:
        size = widget.instance.size()
        width, height = int(size.width()), int(size.height())
        if width <= 1 or height <= 1:
            return original(desktop, screen, *args, **kwargs)
        pixmap = images.load(path, desktop, width, height)
        if pixmap is None:
            return original(desktop, screen, *args, **kwargs)
        widget.instance.setPixmap(pixmap)
    except Exception:
        # Missing files, older loader signatures or unknown native APIs retain
        # the receiver's exact normal application/error path.
        return original(desktop, screen, *args, **kwargs)
    return True


def _apply_screen(screen, *args, **kwargs):
    from Components.Pixmap import Pixmap

    images = _PageImages()
    wrapped = []
    try:
        for widget in tuple(screen.values()):
            if type(widget) is not Pixmap:
                continue
            original = getattr(widget, 'applySkin', None)
            if not callable(original):
                continue
            override = widget.__dict__.get('applySkin')

            def apply(target, desktop, parent, *values, _original=original, **options):
                return _apply_pixmap(target, _original, images, desktop, parent, *values, **options)

            callback = MethodType(apply, widget)
            widget.applySkin = callback
            wrapped.append((widget, override, callback))
        return screen._gt_skin_artwork_original(*args, **kwargs)
    finally:
        for widget, override, callback in wrapped:
            if widget.__dict__.get('applySkin') is callback:
                if override is None:
                    widget.__dict__.pop('applySkin', None)
                else:
                    widget.applySkin = override


def install(screen):
    """Install once on plugin-owned pages using the shared footer setup."""
    if hasattr(screen, '_gt_skin_artwork_original'):
        return
    original = getattr(screen, 'applySkin', None)
    if not callable(original) or not callable(getattr(screen, 'values', None)):
        return
    screen._gt_skin_artwork_original = original
    screen.applySkin = MethodType(_apply_screen, screen)
