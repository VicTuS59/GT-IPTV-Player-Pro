# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Bounded local receiver counters and small, stationary native usage dials."""

import math
import time


def _read_cpu():
    try:
        with open("/proc/stat", "r") as handle:
            fields = handle.readline(4096).split()
        if not fields or fields[0] != "cpu" or len(fields) < 5:
            return None
        # Guest times are already included in user/nice. Count the first eight
        # aggregate counters only, covering every core without double counting.
        values = [int(value) for value in fields[1:9]]
        if any(value < 0 for value in values):
            return None
        idle = values[3] + (values[4] if len(values) > 4 else 0)
        return sum(values), idle
    except (OSError, ValueError, OverflowError):
        return None


def _read_ram():
    try:
        with open("/proc/meminfo", "r") as handle:
            lines = handle.read(16384).splitlines()
        memory = {}
        for line in lines:
            fields = line.split()
            if (len(fields) == 3 and fields[2] == "kB" and fields[0] in (
                    "MemTotal:", "MemAvailable:", "MemFree:", "Buffers:",
                    "Cached:", "SReclaimable:", "Shmem:")):
                memory[fields[0]] = int(fields[1])
        total = memory.get("MemTotal:", 0)
        if total <= 0 or any(value < 0 for value in memory.values()):
            return None
        if "MemAvailable:" in memory:
            available = memory["MemAvailable:"]
            if available > total:
                return None
        else:
            # Older kernels omit MemAvailable. Reclaimable caches are not
            # permanently occupied RAM; shared memory is not counted twice.
            if "MemFree:" not in memory:
                return None
            available = (memory["MemFree:"] + memory.get("Buffers:", 0)
                         + memory.get("Cached:", 0) + memory.get("SReclaimable:", 0)
                         - memory.get("Shmem:", 0))
            available = max(0, min(total, available))
        return 100.0 * (total - available) / total
    except (OSError, ValueError, OverflowError):
        return None


class DeviceUsageSampler(object):
    """Read at most two tiny proc snapshots per visible one-second sample."""

    def __init__(self, cpu_reader=None, ram_reader=None, clock=None):
        self.cpu_reader = cpu_reader or _read_cpu
        self.ram_reader = ram_reader or _read_ram
        self.clock = clock or time.monotonic
        self.reset()

    def reset(self):
        self._previous_cpu = None
        self._sampled_at = None
        self._last = (None, None)

    def sample(self):
        now = self.clock()
        if self._sampled_at is not None and 0 <= now - self._sampled_at < 0.9:
            return self._last
        if self._sampled_at is not None and now < self._sampled_at:
            self._previous_cpu = None
        current = self.cpu_reader()
        previous, self._previous_cpu = self._previous_cpu, current
        cpu = None
        if previous is not None and current is not None:
            total, idle = current[0] - previous[0], current[1] - previous[1]
            if total > 0 and 0 <= idle <= total:
                cpu = 100.0 * (total - idle) / total
        self._last = (cpu, self.ram_reader())
        self._sampled_at = now
        return self._last


def _percent(value):
    try:
        if value is None or isinstance(value, bool):
            return None
        value = float(value)
        if not math.isfinite(value):
            return None
        return max(0, min(100, int(round(value))))
    except (TypeError, ValueError, OverflowError):
        return None


def usage_text(kind, value):
    # CPU/RAM are the same technical abbreviations in every device language,
    # like the neighboring FPS, H.264 and Mbps fields, rather than prose labels.
    percent = _percent(value)
    return "{} {}%".format(kind, "--" if percent is None else percent)


try:
    from Components.GUIComponent import GUIComponent
    from enigma import eCanvas, eRect, eSize, gRGB
    CANVAS_AVAILABLE = True
except (ImportError, AttributeError):
    CANVAS_AVAILABLE = False


if CANVAS_AVAILABLE:
    class DeviceUsageGauge(GUIComponent):
        """A fixed tiny canvas; only its arc/needle change, at most once a second."""

        GUI_WIDGET = eCanvas

        def __init__(self):
            GUIComponent.__init__(self)
            self.value = None
            self._surface_size = None

        def applySkin(self, desktop, parent):
            result = GUIComponent.applySkin(self, desktop, parent)
            self._surface_size = None
            self._draw()
            return result

        def set_value(self, value):
            value = _percent(value)
            if value != self.value:
                self.value = value
                self._draw()

        def _draw(self):
            instance = self.instance
            if instance is None:
                return
            try:
                size = instance.size()
                width, height = int(size.width()), int(size.height())
                if width <= 0 or height <= 0:
                    return
                if self._surface_size != (width, height):
                    instance.setSize(eSize(width, height))
                    self._surface_size = (width, height)
                instance.clear(gRGB(0x00061321))
                cyan, track = gRGB(0x0022D3EE), gRGB(0x002C435B)
                unit = height / 38.0
                corner = max(1, int(round(3 * unit)))
                points = ((corner, 0), (width - corner - 1, 0), (width - 1, corner),
                          (width - 1, height - corner - 1), (width - corner - 1, height - 1),
                          (corner, height - 1), (0, height - corner - 1), (0, corner))
                for first, second in zip(points, points[1:] + points[:1]):
                    instance.drawLine(first[0], first[1], second[0], second[1], cyan)
                small = height <= 15
                cx, cy = (12 if small else 18) * unit, (height - 1) / 2.0
                radius = (7.5 if small else 11) * unit
                segments = 32
                amount = (self.value or 0) * segments / 100.0
                for stroke in range(max(1, int(round(unit)))):
                    r = max(1, radius - stroke)
                    for index in range(segments):
                        a = math.radians(135 + 270.0 * index / segments)
                        b = math.radians(135 + 270.0 * (index + 1) / segments)
                        instance.drawLine(
                            int(round(cx + r * math.cos(a))), int(round(cy + r * math.sin(a))),
                            int(round(cx + r * math.cos(b))), int(round(cy + r * math.sin(b))),
                            cyan if index < amount else track)
                if self.value is not None:
                    angle = math.radians(135 + 2.7 * self.value)
                    instance.drawLine(int(round(cx)), int(round(cy)),
                        int(round(cx + radius * 0.72 * math.cos(angle))),
                        int(round(cy + radius * 0.72 * math.sin(angle))), gRGB(0x00E2E8F0))
                dot = max(1, int(round(2 * unit)))
                instance.fillRect(eRect(int(round(cx - dot / 2.0)),
                                       int(round(cy - dot / 2.0)), dot, dot), cyan)
            except (AttributeError, RuntimeError, TypeError, ValueError, OverflowError):
                # Older/minimal canvases retain the ordinary percentage label.
                pass
else:
    try:
        from Components.Label import Label as _GaugeFallback
    except ImportError:
        _GaugeFallback = object

    class DeviceUsageGauge(_GaugeFallback):
        def __init__(self):
            if _GaugeFallback is not object:
                _GaugeFallback.__init__(self, "")

        def set_value(self, value):
            pass
