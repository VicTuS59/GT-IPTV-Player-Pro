#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Keep Python brace-format flags consistent in gettext catalogs."""

import argparse
import ast
import datetime
import os
import pathlib
import string
import sys


FORMAT_FLAG = "python-brace-format"
TEMPLATE_COMMENT = (
    "# GT IPTV Player Pro translation template.\n"
    "# Copyright (C) 2026 VicTuS59\n"
    "# This file is distributed under the same license as GT IPTV Player Pro.\n"
    "#\n"
)
TEMPLATE_PROJECT_HEADER = (
    '"Project-Id-Version: GT IPTV Player Pro 1.1.0\\n"'
)
CATALOG_PROJECT_HEADER = (
    '"Project-Id-Version: GT IPTV Player Pro 1.1.0\\n"'
)
BUGS_HEADER = (
    '"Report-Msgid-Bugs-To: '
    'https://github.com/VicTuS59/GT-IPTV-Player-Pro/issues\\n"'
)
DEFAULT_SOURCE_DATE_EPOCH = 1700000000


def _pot_creation_header():
    value = os.environ.get(
        "SOURCE_DATE_EPOCH",
        str(DEFAULT_SOURCE_DATE_EPOCH),
    )
    try:
        epoch = int(value)
        created = datetime.datetime.fromtimestamp(
            epoch,
            datetime.timezone.utc,
        )
    except (OverflowError, TypeError, ValueError) as error:
        raise ValueError("SOURCE_DATE_EPOCH must be a valid integer") from error
    return '"POT-Creation-Date: {}\\n"'.format(
        created.strftime("%Y-%m-%d %H:%M%z")
    )


def _replace_header(text, prefix, replacement):
    lines = text.splitlines()
    marker = '"{}:'.format(prefix)
    for index, line in enumerate(lines):
        if line.startswith(marker):
            lines[index] = replacement
            break
    trailing_newline = text.endswith("\n")
    return "\n".join(lines) + ("\n" if trailing_newline else "")


def _quoted_value(line):
    try:
        value = ast.literal_eval(line.strip())
    except (SyntaxError, ValueError):
        return ""
    return value if isinstance(value, str) else ""


def _message_id(lines):
    start = None
    value = ""
    for index, line in enumerate(lines):
        if line.startswith("msgid "):
            start = index
            value = _quoted_value(line[6:])
            break
    if start is None:
        return ""
    for line in lines[start + 1:]:
        if not line.startswith('"'):
            break
        value += _quoted_value(line)
    return value


def _uses_brace_format(message):
    if not message:
        return False
    try:
        return any(
            field_name is not None
            for unused_text, field_name, unused_spec, unused_conversion
            in string.Formatter().parse(message)
        )
    except ValueError:
        return False


def _normalise_block(block):
    lines = block.splitlines()
    message = _message_id(lines)
    required = _uses_brace_format(message)
    flag_indexes = [
        index for index, line in enumerate(lines) if line.startswith("#,")
    ]
    flags = []
    for index in flag_indexes:
        for flag in lines[index][2:].split(","):
            flag = flag.strip()
            if flag and flag not in flags:
                flags.append(flag)
    if required and FORMAT_FLAG not in flags:
        flags.append(FORMAT_FLAG)
    if not required and FORMAT_FLAG in flags:
        flags.remove(FORMAT_FLAG)

    if flag_indexes:
        first = flag_indexes[0]
        lines = [
            line for index, line in enumerate(lines) if index not in flag_indexes
        ]
        if flags:
            lines.insert(first, "#, {}".format(", ".join(flags)))
    elif flags:
        message_index = next(
            (index for index, line in enumerate(lines) if line.startswith("msgid ")),
            len(lines),
        )
        lines.insert(message_index, "#, {}".format(", ".join(flags)))
    return "\n".join(lines)


def _normalise_flags(text):
    trailing_newline = text.endswith("\n")
    blocks = text.rstrip("\n").split("\n\n")
    result = "\n\n".join(_normalise_block(block) for block in blocks)
    return result + ("\n" if trailing_newline else "")


def _normalise_template(text):
    marker = 'msgid ""'
    marker_index = text.find(marker)
    if marker_index >= 0:
        text = TEMPLATE_COMMENT + text[marker_index:]
    if BUGS_HEADER not in text:
        text = text.replace(
            TEMPLATE_PROJECT_HEADER + "\n",
            TEMPLATE_PROJECT_HEADER + "\n" + BUGS_HEADER + "\n",
            1,
        )
    return text


def _normalise_headers(text, template=False):
    project_header = (
        TEMPLATE_PROJECT_HEADER if template else CATALOG_PROJECT_HEADER
    )
    text = _replace_header(text, "Project-Id-Version", project_header)
    return _replace_header(
        text,
        "POT-Creation-Date",
        _pot_creation_header(),
    )


def _expected_text(path, template=False):
    template = template or path.suffix == ".pot"
    text = path.read_text(encoding="utf-8")
    if template:
        text = _normalise_template(text)
    text = _normalise_headers(text, template=template)
    return _normalise_flags(text)


def _write_if_changed(path, expected):
    current = path.read_text(encoding="utf-8")
    if current != expected:
        path.write_text(expected, encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser()
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--fix", action="store_true")
    action.add_argument("--fix-template", action="store_true")
    action.add_argument("--check", action="store_true")
    parser.add_argument("paths", nargs="+")
    arguments = parser.parse_args(argv)

    changed = []
    for value in arguments.paths:
        path = pathlib.Path(value)
        expected = _expected_text(path, template=arguments.fix_template)
        current = path.read_text(encoding="utf-8")
        if current == expected:
            continue
        if arguments.check:
            changed.append(str(path))
        else:
            _write_if_changed(path, expected)
    if changed:
        for value in changed:
            print("gettext format flags are out of date: {}".format(value))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
