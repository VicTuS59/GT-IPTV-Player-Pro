# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Bounded alpha preparation. This module never calls Enigma2 or widgets."""
import hashlib
import io
import os
import stat
import struct

from .artwork_cache import PICON_CACHE

MAX_SOURCE_BYTES = 4 * 1024 * 1024
MAX_SOURCE_PIXELS = 4_000_000
MAX_TARGET_PIXELS = 3840 * 2160
MAX_PREPARED_PIXELS = 2_000_000
MAX_WORKING_PIXEL_BYTES = 32 * 1024 * 1024
PREPARED_ASSET_NAMES = frozenset((
    'cinematic-scrim-r110.png',
))


class PreparationCancelled(Exception):
    pass


def source_identity(path):
    info = os.stat(path, follow_symlinks=False)
    if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_SOURCE_BYTES:
        raise ValueError('Invalid alpha source')
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def prepared_destination(path, size, identity):
    key = repr((os.path.abspath(path), tuple(size), tuple(identity), 'rgba-stretch-r61')).encode('utf-8')
    return os.path.join(PICON_CACHE.directory, 'alpha-r61-' + hashlib.sha256(key).hexdigest() + '.png')


def png_size(path):
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
    with os.fdopen(os.open(path, flags), 'rb') as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_SOURCE_BYTES:
            return (0, 0)
        header = handle.read(33)
    if len(header) != 33 or header[:16] != b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR':
        return (0, 0)
    return struct.unpack('>II', header[16:24])


def bundled_alpha_path(path, size):
    if os.path.basename(path) not in PREPARED_ASSET_NAMES:
        return ''
    width, height = size
    name = '{}-{}x{}.png'.format(os.path.splitext(os.path.basename(path))[0], width, height)
    candidate = os.path.join(os.path.dirname(path), 'alpha-r61', name)
    try:
        return candidate if png_size(candidate) == tuple(size) else ''
    except OSError:
        return ''


def prepare_alpha(path, size, identity, destination, cancel_event):
    """Return (path, actual width, actual height, already scaled).

    Bundled masks need no optional receiver library. Arbitrary logos use
    Pillow when available, otherwise retain their native alpha decoder.
    Generated PNGs share the existing compressed artwork budget.
    """
    def check():
        if cancel_event.is_set():
            raise PreparationCancelled()
    check()
    width, height = [int(n) for n in size]
    if width <= 0 or height <= 0 or width * height > MAX_TARGET_PIXELS:
        raise ValueError('Alpha target exceeds pixel budget')
    if source_identity(path) != tuple(identity):
        raise ValueError('Alpha source changed')
    bundled = bundled_alpha_path(path, (width, height))
    if bundled:
        check()
        return bundled, width, height, True
    try:
        if png_size(destination) == (width, height):
            check()
            PICON_CACHE.touch(destination)
            return destination, width, height, True
    except OSError:
        pass
    try:
        original_size = png_size(path)
    except OSError:
        original_size = (0, 0)
    if original_size[0] * original_size[1] > MAX_SOURCE_PIXELS:
        raise ValueError('Alpha source exceeds pixel budget')
    if width * height > MAX_PREPARED_PIXELS:
        check()
        return path, original_size[0], original_size[1], False
    if original_size == (width, height):
        check()
        return path, width, height, True
    try:
        from PIL import Image, features
    except ImportError:
        check()
        return path, original_size[0], original_size[1], False
    codec = {'.webp': 'webp', '.jpg': 'jpg', '.jpeg': 'jpg'}.get(os.path.splitext(path)[1].lower())
    if codec is not None:
        try:
            supported = features.check(codec)
        except (AttributeError, ValueError):
            supported = False
        if not supported:
            check()
            return path, original_size[0], original_size[1], False
    # Open only a regular source, with a strict byte bound, while the caller
    # pins its artwork-cache entry. Check identity again before publishing.
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
    with os.fdopen(os.open(path, flags), 'rb') as handle:
        opened = os.fstat(handle.fileno())
        actual = (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns)
        # Bind the file descriptor by device/inode/size/mtime. Windows host
        # fstat and stat expose different ctime semantics; the full pathname
        # identity (including ctime) is also verified before and after reading.
        if actual[:4] != tuple(identity)[:4]:
            raise ValueError('Alpha source changed before read')
        body = handle.read(MAX_SOURCE_BYTES + 1)
    check()
    if not 0 < len(body) <= MAX_SOURCE_BYTES:
        raise ValueError('Invalid alpha body size')
    with Image.open(io.BytesIO(body)) as image:
        if (image.width <= 0 or image.height <= 0
                or image.width * image.height > MAX_SOURCE_PIXELS):
            raise ValueError('Alpha source exceeds pixel budget')
        if (image.width * image.height + width * height) * 8 > MAX_WORKING_PIXEL_BYTES:
            check()
            return path, image.width, image.height, False
        image.seek(0)
        check()
        # Premultiplied alpha avoids dark fringes around downsampled logos.
        resampling = getattr(Image, 'Resampling', Image)
        rgba = image if image.mode == 'RGBA' else image.convert('RGBA')
        if rgba is not image:
            image.close()
        try:
            with rgba.convert('RGBa') as premultiplied:
                check()
                with premultiplied.resize((width, height), resampling.LANCZOS) as resized:
                    with resized.convert('RGBA') as output:
                        stream = io.BytesIO()
                        output.save(stream, 'PNG', compress_level=3)
                        payload = stream.getvalue()
        finally:
            if rgba is not image:
                rgba.close()
    check()
    if source_identity(path) != tuple(identity):
        raise ValueError('Alpha source changed during preparation')
    if not payload or len(payload) > MAX_SOURCE_BYTES:
        raise ValueError('Prepared alpha exceeds byte budget')
    PICON_CACHE.store(destination, payload, check=check)
    check()
    return destination, width, height, True
