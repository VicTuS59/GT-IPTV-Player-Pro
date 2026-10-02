# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Bounded file/container checks; never decode or transcode a video.

These checks catch empty/truncated structures and wrong download responses.
They do not certify codec support or inspect every compressed media packet.
No Enigma2 imports: the detached download worker uses the same checks.
"""
import os
import stat
import struct


class MediaFileError(ValueError):
    pass


def _read(handle, offset, amount):
    handle.seek(offset)
    return handle.read(amount)


def _vint(handle, keep_marker=False):
    first = handle.read(1)
    if not first or not first[0]:
        raise MediaFileError("invalid-ebml-integer")
    marker, width = 128, 1
    while not first[0] & marker:
        marker >>= 1
        width += 1
    if width > (4 if keep_marker else 8):
        raise MediaFileError("invalid-ebml-width")
    rest = handle.read(width - 1)
    if len(rest) != width - 1:
        raise MediaFileError("truncated-ebml-integer")
    value = int.from_bytes(first + rest, "big")
    if not keep_marker:
        value &= (1 << (7 * width)) - 1
        if value == (1 << (7 * width)) - 1:
            return None
    return value


def _element(handle, offset, end):
    handle.seek(offset)
    identity, length = _vint(handle, True), _vint(handle)
    start = handle.tell()
    stop = end if length is None else start + length
    if start > end or stop > end:
        raise MediaFileError("truncated-ebml-element")
    return identity, start, stop, length is None


def _matroska(handle, size):
    identity, start, stop, unknown = _element(handle, 0, size)
    if identity != 0x1A45DFA3 or unknown or stop - start > 65536:
        raise MediaFileError("invalid-ebml-header")
    header = _read(handle, start, stop - start)
    if b"matroska" not in header and b"webm" not in header:
        raise MediaFileError("unsupported-ebml-document")
    offset = stop
    for unused in range(32):
        identity, start, stop, unknown = _element(handle, offset, size)
        if identity == 0x18538067:
            break
        if unknown or identity not in (0xEC, 0xBF):
            raise MediaFileError("missing-matroska-segment")
        offset = stop
    else:
        raise MediaFileError("missing-matroska-segment")
    end, offset = stop, start
    tracks = cluster = False
    # Seek past clusters; compressed frames are not copied into RAM.
    for unused in range(8192):
        if offset >= end:
            break
        identity, start, stop, unknown = _element(handle, offset, end)
        if identity == 0x1654AE6B:
            tracks = stop > start
        elif identity == 0x1F43B675:
            cluster = cluster or stop > start
        if unknown:
            # Unknown-size clusters are legal. Their boundary cannot safely
            # be inferred by scanning arbitrary compressed video bytes.
            if identity != 0x1F43B675:
                raise MediaFileError("invalid-unknown-element")
            break
        offset = stop
    if not tracks or not cluster:
        raise MediaFileError("missing-matroska-tracks-or-media")
    return "webm" if b"webm" in header else "mkv"


def _mp4(handle, size):
    offset, movie, media = 0, False, False
    for unused in range(8192):
        if offset == size:
            break
        header = _read(handle, offset, 16)
        if len(header) < 8:
            raise MediaFileError("truncated-mp4-box")
        length, kind = struct.unpack(">I4s", header[:8])
        header_size = 8
        if length == 1:
            if len(header) < 16:
                raise MediaFileError("truncated-mp4-size")
            length, header_size = struct.unpack(">Q", header[8:16])[0], 16
        elif length == 0:
            length = size - offset
        if length < header_size or offset + length > size:
            raise MediaFileError("truncated-mp4-payload")
        movie = movie or (kind == b"moov" and length > header_size)
        media = media or (kind == b"mdat" and length > header_size)
        offset += length
    if not movie or not media:
        raise MediaFileError("missing-mp4-index-or-media")
    return "mp4"


def inspect_media(path, expected_size=0):
    """Return basic format/size metadata, or raise for a detected defect."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    fd = os.open(path, flags)
    with os.fdopen(fd, "rb") as handle:
        info = os.fstat(handle.fileno())
        size = info.st_size
        if not stat.S_ISREG(info.st_mode) or size < 32:
            raise MediaFileError("empty-or-nonregular-file")
        if expected_size and size != int(expected_size):
            raise MediaFileError("file-size-mismatch")
        head = handle.read(min(size, 65536))
        kind = ""
        if head.startswith(b"\x1a\x45\xdf\xa3"):
            kind = _matroska(handle, size)
        elif head[4:8] in (b"ftyp", b"moov", b"mdat", b"free", b"wide", b"skip", b"styp"):
            kind = _mp4(handle, size)
        elif head.startswith(b"RIFF") and head[8:12] == b"AVI ":
            declared = struct.unpack("<I", head[4:8])[0] + 8
            if declared > size or b"hdrl" not in head:
                raise MediaFileError("truncated-avi")
            kind = "avi"
        elif head.startswith(b"\x30\x26\xb2\x75\x8e\x66\xcf\x11"):
            if struct.unpack("<Q", head[16:24])[0] > size:
                raise MediaFileError("truncated-asf")
            kind = "wmv"
        elif head.startswith(b"FLV\x01"):
            if struct.unpack(">I", head[5:9])[0] + 4 >= size:
                raise MediaFileError("truncated-flv")
            kind = "flv"
        elif head.startswith((b"\x00\x00\x01\xba", b"\x00\x00\x01\xb3")):
            kind = "mpg"
        else:
            for stride, first in ((188, 0), (192, 4), (204, 0)):
                if len(head) > first + 4 * stride and all(head[first + n * stride] == 0x47 for n in range(5)):
                    kind = "m2ts" if stride == 192 else "ts"
                    break
        if not kind:
            raise MediaFileError("unrecognized-video-container")
        return {"format": kind, "size": size, "device": info.st_dev,
                "inode": info.st_ino, "mtime_ns": info.st_mtime_ns}


def matching_extension(path, kind):
    """Keep a valid alias; correct a provider extension only when it differs."""
    aliases = {"mp4": (".mp4", ".mov", ".m4v"), "mkv": (".mkv",),
               "webm": (".webm", ".mkv"), "ts": (".ts",),
               "m2ts": (".m2ts", ".mts"), "mpg": (".mpg", ".mpeg", ".vob")}
    suffix = os.path.splitext(path)[1].lower()
    return suffix if suffix in aliases.get(kind, ("." + kind,)) else "." + kind

