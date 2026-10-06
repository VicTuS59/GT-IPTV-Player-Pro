# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Thread-local cancellation between bounded subtitle HTTP requests."""

from contextlib import contextmanager
import threading

_STATE = threading.local()


class SubtitleJobCancelled(Exception):
    """An obsolete subtitle job must not start another network request."""


def check_subtitle_job():
    callback = getattr(_STATE, "should_continue", None)
    if callable(callback) and not callback():
        raise SubtitleJobCancelled()


@contextmanager
def subtitle_request_scope(should_continue):
    previous = getattr(_STATE, "should_continue", None)
    _STATE.should_continue = should_continue
    try:
        check_subtitle_job()
        yield
    finally:
        _STATE.should_continue = previous
