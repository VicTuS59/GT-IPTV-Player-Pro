# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Bounded subtitle decoding shared by native and web providers."""

from .subtitle_language import normalize_subtitle_language

MAX_SUBTITLE_BYTES = 2 * 1024 * 1024
_CENTRAL_EUROPEAN_LETTERS = {
    "pl": frozenset("ąćęłńóśźżĄĆĘŁŃÓŚŹŻ"),
    "cs": frozenset("áčďéěíňóřšťúůýžÁČĎÉĚÍŇÓŘŠŤÚŮÝŽ"),
    "sk": frozenset("áäčďéíĺľňóôŕšťúýžÁÄČĎÉÍĹĽŇÓÔŔŠŤÚÝŽ"),
    "sl": frozenset("čšžČŠŽ"),
    "hr": frozenset("čćđšžČĆĐŠŽ"),
    "hu": frozenset("áéíóöőúüűÁÉÍÓÖŐÚÜŰ"),
    "ro": frozenset("ăâîşţșțĂÂÎŞŢȘȚ"),
}


def _error(code):
    from .web_subtitles import SubtitleError
    return SubtitleError(code, 422)


def _checked(text):
    if len(text.encode("utf-8")) > MAX_SUBTITLE_BYTES:
        raise _error("subtitle_too_large")
    if "\x00" in text:
        raise _error("subtitle_format_unsupported")
    return text


def decode_subtitle_content(content, language=""):
    """Prefer declared Unicode BOMs, then UTF-8, then a language code page.

    Legacy code pages do not identify a language. Provider labels and the
    parsed subtitle text are checked separately by their callers.
    """
    if isinstance(content, str):
        return _checked(content.lstrip("\ufeff"))
    if not isinstance(content, bytes):
        raise _error("subtitle_format_unsupported")
    if len(content) > MAX_SUBTITLE_BYTES:
        raise _error("subtitle_too_large")
    for marker, encoding in ((b"\xff\xfe\x00\x00", "utf-32"),
                             (b"\x00\x00\xfe\xff", "utf-32"),
                             (b"\xff\xfe", "utf-16"),
                             (b"\xfe\xff", "utf-16")):
        if content.startswith(marker):
            try:
                return _checked(content.decode(encoding))
            except UnicodeDecodeError:
                raise _error("subtitle_format_unsupported")
    try:
        return _checked(content.decode("utf-8-sig"))
    except UnicodeDecodeError:
        pass
    code = normalize_subtitle_language(language)
    if code in ("pl", "cs", "sk", "sl", "hr", "hu", "ro"):
        encodings = ("cp1250", "iso-8859-2")
    elif code in ("ru", "uk", "bg", "mk", "sr"):
        encodings = ("cp1251",)
    elif code in ("ar", "fa", "ur"):
        encodings = ("cp1256",)
    elif code == "el":
        encodings = ("cp1253", "iso-8859-7")
    elif code == "he":
        encodings = ("cp1255", "iso-8859-8")
    elif code == "th":
        encodings = ("cp874",)
    elif code == "ja":
        encodings = ("cp932",)
    elif code == "ko":
        encodings = ("cp949",)
    elif code == "zh":
        encodings = ("gb18030",)
    elif code == "tr" or not code:
        encodings = ("cp1254", "iso-8859-9", "latin-1")
    else:
        encodings = ("cp1252", "latin-1")
    decoded = []
    for encoding in encodings:
        try:
            text = content.decode(encoding)
        except UnicodeDecodeError:
            continue
        # Both Central European code pages can decode the same bytes. Prefer
        # evidence from the declared language (e.g. Czech š/ž/ť, Slovak Ľ/ľ),
        # while preserving a stable default when the text is indistinguishable.
        controls = sum(1 for char in text if 0x80 <= ord(char) <= 0x9f)
        alphabet = _CENTRAL_EUROPEAN_LETTERS.get(code, ())
        letters = sum(char in alphabet for char in text)
        decoded.append(((controls, -letters, len(decoded)), text))
    if not decoded:
        raise _error("subtitle_format_unsupported")
    return _checked(min(decoded, key=lambda item: item[0])[1])
