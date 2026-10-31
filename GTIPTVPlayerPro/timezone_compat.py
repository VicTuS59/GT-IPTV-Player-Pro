"""Prefer the image ZoneInfo; use the bundled CPython fallback when split out."""
try:
    from zoneinfo import ZoneInfo
except ImportError:
    from .tz_compat import ZoneInfo
