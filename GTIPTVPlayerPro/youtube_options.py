# -*- coding: utf-8 -*-
"""Shared receiver/web quality choices for YouTube."""

YOUTUBE_QUALITIES = ("2160", "1440", "1080", "720", "480", "360")
DEFAULT_YOUTUBE_QUALITY = "1440"


def quality_label(value):
    value = str(value)
    return {"2160": "4K (2160p)", "1440": "2K (1440p)"}.get(value, value + "p")
