# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Receiver RX throughput, independent of native playback engine bindings."""

import math
import os
import re
import time


_INTERFACE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
_MAX_FILE_BYTES = 64 * 1024


def _interface_name(value):
    return bool(value not in (".", "..", "lo") and _INTERFACE.fullmatch(value))


def _read_text(path):
    try:
        with open(path, "r", encoding="ascii") as handle:
            value = handle.read(_MAX_FILE_BYTES + 1)
        return value if len(value) <= _MAX_FILE_BYTES else None
    except (OSError, ValueError, UnicodeError):
        return None


def _counter(value):
    try:
        number = int(value)
        return number if 0 <= number < (1 << 64) else None
    except (TypeError, ValueError, OverflowError):
        return None


class NetworkRateSampler(object):
    """Sample one active interface; never sum bridges/slaves or contact a host.

    The value includes all traffic received by the chosen connection, including
    downloads and provider metadata. It is not a per-channel video bitrate.
    procfs is the normal path; sysfs supports images lacking /proc/net/dev.
    """

    def __init__(self, clock=None, proc_root="/proc/net", sysfs_root="/sys/class/net"):
        self.clock = clock or time.monotonic
        self.proc_root = proc_root
        self.sysfs_root = sysfs_root
        self.reset()

    def reset(self):
        self.interface = None
        self._previous = None
        self._rate = None

    def _counters(self):
        text = _read_text(os.path.join(self.proc_root, "dev"))
        counters = {}
        for line in (text or "").splitlines():
            if ":" not in line:
                continue
            name, values = line.rsplit(":", 1)
            name, values = name.strip(), values.split()
            if not _interface_name(name) or len(values) < 16:
                continue
            value = _counter(values[0])
            if value is not None:
                counters[name] = value
        if counters:
            return counters
        try:
            names = sorted(os.listdir(self.sysfs_root))[:64]
        except OSError:
            return {}
        for name in names:
            if not _interface_name(name):
                continue
            value = _counter(_read_text(os.path.join(
                self.sysfs_root, name, "statistics", "rx_bytes")))
            if value is not None:
                counters[name] = value
        return counters

    def _active(self, name):
        state = _read_text(os.path.join(self.sysfs_root, name, "operstate"))
        return state is None or state.strip() in ("up", "unknown")

    def _select_interface(self, counters):
        routes = []
        text = _read_text(os.path.join(self.proc_root, "route"))
        for line in (text or "").splitlines():
            fields = line.split()
            if len(fields) < 8 or not _interface_name(fields[0]):
                continue
            try:
                default = int(fields[1], 16) == 0 and int(fields[7], 16) == 0
                flags, metric = int(fields[3], 16), int(fields[6])
            except ValueError:
                continue
            if default and flags & 1 and not flags & 0x200 and metric >= 0:
                routes.append((metric, fields[0]))
        for unused_metric, name in sorted(routes):
            if self._active(name):
                return name if name in counters else None
        routes = []
        text = _read_text(os.path.join(self.proc_root, "ipv6_route"))
        for line in (text or "").splitlines():
            fields = line.split()
            if len(fields) < 10 or not _interface_name(fields[9]):
                continue
            try:
                default = int(fields[0], 16) == 0 and int(fields[1], 16) == 0
                flags, metric = int(fields[8], 16), int(fields[5], 16)
            except ValueError:
                continue
            if default and flags & 1 and not flags & 0x200 and metric >= 0:
                routes.append((metric, fields[9]))
        for unused_metric, name in sorted(routes):
            if self._active(name):
                return name if name in counters else None
        # Without a default route, use an unambiguous active interface. Bridge
        # and bond slaves must not be counted again alongside their master.
        candidates = [name for name in counters if self._active(name) and not
                      os.path.exists(os.path.join(self.sysfs_root, name, "master"))]
        return candidates[0] if len(candidates) == 1 else None

    def sample(self):
        try:
            now = float(self.clock())
            if not math.isfinite(now):
                self.reset()
                return None
            # Reopening an already-visible banner must not take tiny-interval
            # samples or continually postpone the first complete second.
            if self._previous is not None:
                elapsed = now - self._previous[0]
                if 0 <= elapsed < 0.9:
                    return self._rate
            counters = self._counters()
            name = self._select_interface(counters)
            if name is None:
                self.reset()
                return None
            received = counters[name]
            previous = self._previous
            self._previous = (now, received)
            if previous is None or self.interface != name:
                self.interface = name
                self._rate = None
                return None
            elapsed, delta = now - previous[0], received - previous[1]
            if elapsed <= 0 or elapsed > 5 or delta < 0:
                self._rate = None
                return None
            rate = delta * 8.0 / (elapsed * 1000000.0)
            self._rate = rate if math.isfinite(rate) and rate <= 100000 else None
            return self._rate
        except (OSError, ValueError, TypeError, OverflowError):
            self.reset()
            return None
