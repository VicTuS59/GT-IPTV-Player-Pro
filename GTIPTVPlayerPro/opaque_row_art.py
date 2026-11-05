# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-2.0-or-later
"""Reuse precomposited copies of the skin's fixed row backgrounds."""
from functools import lru_cache
from hashlib import blake2s
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic
from xml.etree import ElementTree as ET

from enigma import loadPNG

from .ui_update import set_visible
from .utility_design import CANVAS


def _set_opaque(instance):
    instance.setAlphatest(0)
    instance.setScale(0)
    instance.setTransparent(False)


def _nodes(skin):
    return {node.get('name'): node for node in ET.fromstring(skin).findall('widget')}


def _box(node):
    return tuple(map(int, (node.get('position') + ',' + node.get('size')).split(',')))


def _composite_panel(Image, node, resample):
    size = _box(node)[2:]
    canvas = Image.new('RGBA', size, CANVAS)
    with Image.open(node.get('pixmap')) as original:
        canvas.alpha_composite(original.convert('RGBA').resize(size, resample))
    return canvas


def _load_opaque(artwork, path):
    artwork.convert('RGB').save(path, format='PNG', compress_level=0)
    return loadPNG(str(path))


def prepare_opaque_panels(skin, names, *, loader=_load_opaque):
    try:
        from PIL import Image
    except ImportError:
        return {}
    nodes = _nodes(skin)
    resample = getattr(Image, 'Resampling', Image).NEAREST
    with TemporaryDirectory(prefix='gt-opaque-panels-') as tmp:
        pixmaps = {}
        for name in names:
            try:
                pixmap = loader(_composite_panel(Image, nodes[name], resample), Path(tmp) / (name + '.png'))
            except (OSError, KeyError, TypeError, ValueError):
                continue
            if pixmap is not None:
                pixmaps[name] = pixmap
    return pixmaps


def install_opaque_panels(screen, pixmaps):
    for name, pixmap in pixmaps.items():
        widget = screen[name]
        instance = widget.instance
        if instance is not None and getattr(widget, '_gt_opaque_panel', None) != id(instance):
            _set_opaque(instance)
            instance.setPixmap(pixmap)
            widget._gt_opaque_panel = id(instance)


class OpaqueRowArt:
    def __init__(self, prefix, overlay_prefixes, pixmaps):
        self.prefix = prefix
        self.overlay_prefixes = overlay_prefixes
        self.pixmaps = pixmaps

    def paint(self, screen, slot, present, state):
        widget = screen[self.prefix + str(slot)]
        instance = widget.instance
        if instance is None:
            return False
        if present:
            key = (id(instance), state)
            previous = getattr(widget, '_gt_opaque_row', None)
            if key != previous:
                if previous is None or previous[0] != key[0]:
                    _set_opaque(instance)
                instance.setPixmap(self.pixmaps[slot][state])
                widget._gt_opaque_row = key
        set_visible(widget, present)
        for prefix in self.overlay_prefixes:
            set_visible(screen[prefix + str(slot)], False)
        return True


def prepare_opaque_rows(skin, panel_name, prefix, count, variants, *, loader=_load_opaque, reuse_equal=False):
    try:
        from PIL import Image
    except ImportError:
        return None

    nodes = _nodes(skin)
    resampling = getattr(Image, 'Resampling', Image)
    resized = {}
    decoded = {}

    def image(node, size):
        path = node.get('pixmap')
        color = node.get('backgroundColor') if path is None else None
        key = (path, color, size)
        if key not in resized:
            if path is None and color is not None:
                resized[key] = Image.new('RGBA', size, color)
            else:
                with Image.open(path) as original:
                    resized[key] = original.convert('RGBA').resize(size, resampling.NEAREST)
        return resized[key]

    try:
        panel = None
        if panel_name is not None:
            panel_node = nodes[panel_name]
            panel_x, panel_y, panel_w, panel_h = _box(panel_node)
            panel = _composite_panel(Image, panel_node, resampling.NEAREST)
        pixmaps = []
        with TemporaryDirectory(prefix='gt-opaque-rows-') as tmp:
            for slot in range(count):
                row = nodes[prefix + str(slot)]
                x, y, w, h = _box(row)
                if panel is None:
                    base = Image.new('RGBA', (w, h), CANVAS)
                else:
                    left, top = x - panel_x, y - panel_y
                    if left < 0 or top < 0 or left + w > panel_w or top + h > panel_h:
                        return None
                    base = panel.crop((left, top, left + w, top + h))
                if row.get('pixmap') is not None or row.get('backgroundColor') is not None:
                    base.alpha_composite(image(row, base.size))
                row_pixmaps = []
                for state, overlays in enumerate(variants):
                    artwork = base.copy()
                    for overlay in overlays:
                        node = nodes[overlay + str(slot)]
                        ox, oy, ow, oh = _box(node)
                        artwork.alpha_composite(image(node, (ow, oh)), (ox - x, oy - y))
                    path = Path(tmp) / f'row-{slot}-{state}.png'
                    if reuse_equal:
                        key = (artwork.size, blake2s(artwork.tobytes()).digest())
                        if key not in decoded:
                            decoded[key] = loader(artwork, path)
                        pixmap = decoded[key]
                    else:
                        pixmap = loader(artwork, path)
                    if pixmap is None:
                        return None
                    row_pixmaps.append(pixmap)
                pixmaps.append(row_pixmaps)
    except (OSError, KeyError, TypeError, ValueError):
        return None
    overlay_prefixes = tuple(dict.fromkeys(prefix for variant in variants for prefix in variant))
    return OpaqueRowArt(prefix, overlay_prefixes, pixmaps)


def _encode(artwork, unused_path):
    buffer = BytesIO()
    artwork.convert('RGB').save(buffer, format='PNG', compress_level=1)
    data = buffer.getvalue()
    return artwork.size, blake2s(data).digest(), data


@lru_cache(maxsize=6)
def _encoded_art(skin, panels, row_groups):
    return (prepare_opaque_panels(skin, panels, loader=_encode) if panels else {},
            tuple(prepare_opaque_rows(skin, *group, loader=_encode, reuse_equal=True)
                  for group in row_groups))


class DeferredOpaqueArt:
    """Upgrade native skin artwork after the first frame without blocking input."""

    def __init__(self, screen, panels, row_groups, apply, *, start=True):
        from enigma import eTimer
        from .source_loader import SourceLoader

        self.screen = screen
        self.spec = (screen.skin, tuple(panels), tuple(row_groups))
        self.apply = apply
        self.timer = eTimer()
        self.connections = []
        self._connect(self.timer, self._step)
        self.loader = SourceLoader(eTimer(), self._connect, self._ready)
        self.steps = None
        self.requested = False
        self.enabled = start
        screen.onShown.append(self.start)
        screen.onHide.append(self.timer.stop)
        screen.onClose.append(self.close)

    def _connect(self, timer, callback):
        try:
            self.connections.append(timer.timeout.connect(callback))
        except AttributeError:
            timer.callback.append(callback)

    def start(self):
        if self.enabled and self.screen is not None:
            if self.steps is not None or not self.requested:
                self.timer.start(120 if not self.requested else 16, True)

    def enable(self):
        if not self.enabled:
            self.enabled = True
            self.start()

    def _ready(self, value, error):
        if self.screen is not None and error is None:
            self.steps = self._decode(*value)
            self.start()

    def _decode(self, panels, groups):
        from .background import _cache_get, _cache_put

        with TemporaryDirectory(prefix='gt-opaque-native-') as tmp:
            path = Path(tmp)
            index = 0

            def decode(entry):
                nonlocal index
                (width, height), digest, data = entry
                key = ('opaque-skin', width, height, digest)
                pixmap = _cache_get(key)
                if pixmap is None:
                    index += 1
                    filename = path / f'{index}.png'
                    filename.write_bytes(data)
                    pixmap = loadPNG(str(filename))
                    if pixmap is not None:
                        _cache_put(key, pixmap, width, height)
                return pixmap

            native_panels = {}
            for name, entry in panels.items():
                pixmap = decode(entry)
                if pixmap is not None:
                    native_panels[name] = pixmap
                yield
            native_rows = []
            for group in groups:
                rows = []
                if group is not None:
                    for variants in group.pixmaps:
                        states = []
                        for entry in variants:
                            states.append(decode(entry))
                            yield
                        rows.append(states)
                ready = group is not None and all(state is not None for row in rows for state in row)
                native_rows.append(OpaqueRowArt(group.prefix, group.overlay_prefixes, rows) if ready else None)
        self.apply(native_panels, tuple(native_rows))

    def _step(self):
        if self.screen is None or not getattr(self.screen, 'shown', True):
            return
        if not self.requested:
            self.requested = True
            spec = self.spec
            self.loader.request(lambda: _encoded_art(*spec))
            return
        steps = self.steps
        if steps is None:
            return
        deadline = monotonic() + .006
        try:
            while True:
                next(steps)
                if monotonic() >= deadline:
                    break
        except (StopIteration, OSError, RuntimeError):
            steps.close()
            self.steps = None
            return
        self.timer.start(16, True)

    def close(self):
        self.timer.stop()
        self.loader.close()
        if self.steps is not None:
            self.steps.close()
            self.steps = None
        self.screen = self.apply = None
