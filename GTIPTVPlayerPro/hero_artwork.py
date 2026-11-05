# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Bounded worker-only hero composition; no GUI, thread or second image pool."""
from collections import namedtuple
import hashlib
import io
import os
import re
import stat
import struct
import threading

from .alpha_artwork import (MAX_SOURCE_BYTES, MAX_SOURCE_PIXELS,
                            MAX_PREPARED_PIXELS, MAX_WORKING_PIXEL_BYTES)
from .artwork_cache import PICON_CACHE
from .artwork_file import ArtworkPath
from .hero_assets import PLANS

HeroPlan = namedtuple('HeroPlan', 'size mask key')
_COMPOSING = threading.Lock()


def hero_plan(width, height):
    value = PLANS.get((int(width), int(height)))
    if value is None:
        return None
    size, name, fingerprint = value
    return HeroPlan(tuple(size), os.path.join(os.path.dirname(__file__), 'skin', 'images',
                                            'hero-r82', name), (tuple(size), fingerprint))


def vod_hero_plan(width, height):
    """Cap UHD hero decoding at the FHD card's dimensions."""
    size = (min(width, 932), min(height, 503))
    return HeroPlan(size, None, (size, 'vod-list-shadow-v1'))


def _vod_shadow(Image, size):
    from PIL import ImageChops
    with Image.new('L', (256, 1)) as horizontal, Image.new('L', (1, 256)) as vertical:
        horizontal.putdata([round(215 * (1 - offset / 255) ** 2) for offset in range(256)])
        vertical.putdata([round(232 * (offset / 255) ** 4) for offset in range(256)])
        with horizontal.resize(size) as left, vertical.resize(size) as bottom:
            return ImageChops.screen(left, bottom)


def strip_overlay_skin(skin, plan):
    if plan is None:
        return skin
    def widget(match):
        text = match.group(0)
        name = re.search(r'\bname="([^"]+)"', text)
        if name and re.fullmatch(r'(?:movie|series)_(?:backdrop_scrim|detail_shadow|people_shadow|rail_shadow)', name[1]):
            return re.sub(r'\s+pixmap="[^"]*"', '', text)
        return text
    return re.sub(r'<widget\b[^>]*?/>', widget, skin)


def is_composite(path, plan):
    return bool(plan is not None and getattr(path, 'hero_composite_key', None) == plan.key
                and getattr(path, 'hero_decode_size', None) == plan.size
                and tuple(getattr(path, 'image_header', ())) == ('.png', *plan.size))


def valid_composite(path):
    key = getattr(path, 'hero_composite_key', None)
    size = getattr(path, 'hero_decode_size', None)
    return bool(key is not None and size is not None
                and any(key == (tuple(value[0]), value[2]) for value in PLANS.values())
                and tuple(getattr(path, 'image_header', ())) == ('.png', *size)
                and os.path.dirname(path) == PICON_CACHE.directory
                and re.fullmatch(r'hero-r82-[0-9a-f]{64}\.png', os.path.basename(path)))


def prepared_size(path):
    size = getattr(path, 'hero_decode_size', None)
    return tuple(size) if size else None


def preparation_allowance():
    from .background import _BACKGROUND_CACHE_BUDGET_BYTES, _BACKGROUND_MEMORY
    from .graphics_policy import MEMORY_RESERVE_BYTES
    limit = min(MAX_WORKING_PIXEL_BYTES, _BACKGROUND_CACHE_BUDGET_BYTES[0])
    available = _BACKGROUND_MEMORY[1]
    if available is not None:
        limit = min(limit, max(0, int(available)-MEMORY_RESERVE_BYTES))
    return max(0, limit)


class _Lease(object):
    def __init__(self, release): self.release = release
    def close(self):
        release, self.release = self.release, None
        if release is not None:
            release()
    def __del__(self):
        try: self.close()
        except Exception: pass


def release_prepared(path):
    lease = getattr(path, '_hero_lease', None)
    if lease is not None:
        path._hero_lease = None
        lease.close()


class _BoundedPNG(io.BytesIO):
    def write(self, body):
        if self.tell() + len(body) > MAX_SOURCE_BYTES:
            raise ValueError('prepared hero exceeds existing artwork byte guard')
        return super(_BoundedPNG, self).write(body)


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _safe(info):
    return (stat.S_ISREG(info.st_mode) and info.st_nlink == 1
            and info.st_uid == getattr(os, 'geteuid', lambda: info.st_uid)()
            and 0 < info.st_size <= MAX_SOURCE_BYTES)


def _prepared(path, source, plan, lease):
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
    with os.fdopen(os.open(path, flags), 'rb') as handle:
        info = os.fstat(handle.fileno())
        header = handle.read(33)
    if (not _safe(info) or len(header) != 33
            or header[:16] != b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR'
            or struct.unpack('>II', header[16:24]) != plan.size
            or header[24:26] != b'\x08\x02'):
        raise ValueError('invalid prepared hero')
    result = ArtworkPath(path, _identity(info), ('.png', *plan.size), False)
    result.hero_composite_key = plan.key
    result.hero_decode_size = plan.size
    result.hero_source_path = source
    result._hero_lease = lease
    return result


def prepare_hero(source, plan, token, allowance):
    """Return a prepared opaque RGB PNG, or the original path for native fallback.

    Called by the existing foreground/nearby artwork workers. One bounded
    operation runs at a time. The source lease, PNG output, temporary body,
    decoded pixels and cancellation are all covered by existing budgets.
    """
    if not source or plan is None or is_composite(source, plan):
        return source
    token.check()
    width, height = plan.size
    pixels = width * height
    if (pixels > MAX_PREPARED_PIXELS or allowance <= 0
            or not _COMPOSING.acquire(False)):
        return source
    input_release = output_lease = None
    picture = resized = cropped = rgba = rgb = mask = alpha = stream = None
    try:
        input_release = PICON_CACHE.pin(source)
        info = os.stat(source, follow_symlinks=False)
        if not _safe(info):
            return source
        identity = _identity(info)
        key = repr((os.path.abspath(source), identity, plan.key, 'rgb-right-top-r88')).encode('utf-8')
        destination = os.path.join(PICON_CACHE.directory, 'hero-r82-' + hashlib.sha256(key).hexdigest() + '.png')
        output_lease = _Lease(PICON_CACHE.pin(destination))
        try:
            result = _prepared(destination, source, plan, output_lease)
            token.check()
            PICON_CACHE.touch(destination)
            output_lease = None
            return result
        except (OSError, ValueError):
            pass
        try:
            from PIL import Image
        except ImportError:
            return source
        token.check()
        flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
        with os.fdopen(os.open(source, flags), 'rb') as handle:
            opened = os.fstat(handle.fileno())
            if not _safe(opened) or _identity(opened)[:4] != identity[:4]:
                return source
            try:
                picture = Image.open(handle)
            except Image.DecompressionBombError:
                return source
            sw, sh = picture.size
            if not 0 < sw * sh <= MAX_SOURCE_PIXELS:
                return source
            if width * sh >= height * sw:
                cover = (width, (width * sh + sw - 1) // sw)
            else:
                cover = ((height * sw + sh - 1) // sh, height)
            picture.draft('RGB', cover)
            # Conservative peak bound for source conversion, resize/crop,
            # RGBA result, palette+alpha and two encoded-body references.
            source_factor = 4 if picture.mode == 'RGB' else 8
            working = max(source_factor * picture.width * picture.height + 4 * cover[0] * cover[1],
                          4 * cover[0] * cover[1] + 4 * pixels, 13 * pixels)
            if working > min(MAX_WORKING_PIXEL_BYTES, int(allowance)):
                return source
            picture.load()
            token.check()
            if 'A' in picture.getbands():
                channel = picture.getchannel('A')
                opaque = channel.getextrema() == (255, 255)
                channel.close()
                if not opaque:
                    return source
            elif 'transparency' in picture.info:
                return source
            if picture.mode != 'RGB':
                converted = picture.convert('RGB')
                picture.close(); picture = converted
            try: resample = Image.Resampling.BICUBIC
            except AttributeError: resample = Image.BICUBIC
            resized = picture.resize(cover, resample)
            picture.close(); picture = None
        token.check()
        cropped = resized.crop((cover[0] - width, 0, cover[0], height))
        resized.close(); resized = None
        rgba = cropped.convert('RGBA')
        cropped.close(); cropped = None
        token.check()
        if plan.mask is None:
            alpha = _vod_shadow(Image, plan.size)
        else:
            mask = Image.open(plan.mask)
            if mask.mode != 'P' or mask.size != plan.size:
                return source
            # Generated palette index == alpha, with the existing RGB tint.
            alpha = mask.point(list(range(256)), 'L')
            mask.close(); mask = None
        rgba.paste((2, 6, 23, 255), (0, 0, width, height), alpha)
        alpha.close(); alpha = None
        token.check()
        # Composition is fully opaque. Drop only the redundant alpha channel;
        # retain the same RGB samples and fast, uncompressed PNG encoding.
        rgb = rgba.convert('RGB')
        rgba.close(); rgba = None
        stream = _BoundedPNG()
        rgb.save(stream, 'PNG', compress_level=0)
        rgb.close(); rgb = None
        token.check()
        if _identity(os.stat(source, follow_symlinks=False)) != identity:
            return source
        body = stream.getvalue()
        PICON_CACHE.store(destination, body, check=token.check)
        del body
        stream.close(); stream = None
        result = _prepared(destination, source, plan, output_lease)
        token.check()
        output_lease = None
        return result
    except (OSError, ValueError, TypeError, RuntimeError):
        token.check()
        return source
    finally:
        for image in (picture, resized, cropped, rgba, rgb, mask, alpha, stream):
            if image is not None:
                image.close()
        if input_release is not None:
            input_release()
        if output_lease is not None:
            output_lease.close()
        _COMPOSING.release()
