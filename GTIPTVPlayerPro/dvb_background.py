# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Keep a live DVB picture from showing around receiver UI artwork."""


def _live_dvb_reference(reference):
    """DVB service type 1 with no media path denotes live broadcast TV."""
    if reference is None:
        return False
    try:
        return int(reference.type) == 1 and not reference.getPath()
    except (AttributeError, TypeError, ValueError):
        return False


class DVBBackgroundGuard(object):
    """Pause only the entering DVB channel; restore it only if still idle."""

    def __init__(self, session):
        self._session = session
        self._reference = None
        self._paused = False
        self._closed = False

    def pause(self):
        if self._closed or self._paused:
            return False
        navigation = getattr(self._session, "nav", None)
        if navigation is None:
            return False
        try:
            reference = navigation.getCurrentlyPlayingServiceReference()
            if not _live_dvb_reference(reference):
                return False
            result = navigation.stopService()
            if isinstance(result, int) and result != 0:
                return False
        except Exception:
            return False
        self._reference = reference
        self._paused = True
        return True

    def restore(self):
        if self._closed:
            return False
        self._closed = True
        if not self._paused:
            return False
        reference = self._reference
        self._reference = None
        self._paused = False
        navigation = getattr(self._session, "nav", None)
        if navigation is None:
            return False
        try:
            # A later user/plug-in channel change takes precedence.
            if navigation.getCurrentlyPlayingServiceReference() is not None:
                return False
            navigation.playService(reference)
            return True
        except Exception:
            return False

