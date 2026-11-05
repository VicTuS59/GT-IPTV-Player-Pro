# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
from .ui_update import set_visible, set_geometry


def scrollbar_metrics(total, selected, visible, track_height, minimum=28):
    """Return ``(thumb_y, thumb_height)`` for a vertical list scrollbar."""
    try:
        total = max(0, int(total))
        selected = int(selected)
        visible = max(1, int(visible))
        track_height = max(0, int(track_height))
        minimum = max(1, int(minimum))
    except (TypeError, ValueError, OverflowError):
        return None
    if total <= visible or track_height <= 0:
        return None

    thumb_height = int(round(float(track_height) * visible / total))
    thumb_height = min(track_height, max(minimum, thumb_height))
    travel = max(0, track_height - thumb_height)
    selected = min(max(0, selected), total - 1)
    thumb_y = int(round(float(travel) * selected / max(1, total - 1)))
    return thumb_y, thumb_height


def horizontal_scrollbar_metrics(
    total, selected, visible, track_width, minimum=48
):
    """Return ``(thumb_x, thumb_width)`` for a horizontal list scrollbar."""
    return scrollbar_metrics(
        total,
        selected,
        visible,
        track_width,
        minimum=minimum,
    )


def update_scrollbar(screen, prefix, total, selected, visible, geometry):
    """Move and resize the Enigma2 Label widgets that form a scrollbar."""
    track = screen["{}_track".format(prefix)]
    thumb = screen["{}_thumb".format(prefix)]
    try:
        x, y, width, height = [int(value) for value in geometry]
    except (TypeError, ValueError, OverflowError):
        set_visible(track, False)
        set_visible(thumb, False)
        return None

    metrics = scrollbar_metrics(total, selected, visible, height)
    if metrics is None:
        set_visible(track, False)
        set_visible(thumb, False)
        return None

    thumb_offset, thumb_height = metrics
    try:
        from enigma import ePoint, eSize

        set_geometry(track, (x, y, width, height))
        set_geometry(thumb, (x, y + thumb_offset, width, thumb_height))
    except Exception:
        # The list may refresh once before the skin renderer has created the
        # native widget instances. The next navigation refresh applies it.
        pass
    set_visible(track, True)
    set_visible(thumb, True)
    return metrics


def update_horizontal_scrollbar(
    screen,
    prefix,
    total,
    selected,
    visible,
    geometry,
    minimum=48,
):
    """Move and resize the widgets that form a horizontal list scrollbar."""
    track = screen["{}_track".format(prefix)]
    thumb = screen["{}_thumb".format(prefix)]
    try:
        x, y, width, height = [int(value) for value in geometry]
    except (TypeError, ValueError, OverflowError):
        set_visible(track, False)
        set_visible(thumb, False)
        return None

    if height <= 0:
        set_visible(track, False)
        set_visible(thumb, False)
        return None
    metrics = horizontal_scrollbar_metrics(
        total,
        selected,
        visible,
        width,
        minimum=minimum,
    )
    if metrics is None:
        set_visible(track, False)
        set_visible(thumb, False)
        return None

    thumb_offset, thumb_width = metrics
    try:
        from enigma import ePoint, eSize

        set_geometry(track, (x, y, width, height))
        set_geometry(thumb, (x + thumb_offset, y, thumb_width, height))
    except Exception:
        # The native widget instances can be created after the first refresh.
        # A following navigation/render pass applies the final geometry.
        pass
    set_visible(track, True)
    set_visible(thumb, True)
    return metrics


def hide_scrollbar(screen, prefix):
    for suffix in ("track", "thumb"):
        try:
            set_visible(screen["{}_{}".format(prefix, suffix)], False)
        except Exception:
            pass

