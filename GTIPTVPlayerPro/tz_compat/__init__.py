"""CPython pure-Python ZoneInfo fallback; see PROVENANCE.txt and LICENSE.txt."""
import os
from . import _tzpath
from ._common import ZoneInfoNotFoundError
if not _tzpath.TZPATH and "PYTHONTZPATH" not in os.environ:
    _tzpath.reset_tzpath(tuple(p for p in ("/usr/share/zoneinfo", "/usr/lib/zoneinfo", "/usr/share/lib/zoneinfo", "/etc/zoneinfo") if os.path.isdir(p)))
from ._zoneinfo import ZoneInfo
