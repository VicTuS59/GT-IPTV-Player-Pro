# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-2.0-or-later
"""Worker-verified file facts carried by existing bounded artwork entries.

There is no second cache or image body here. Native loaders can use these
facts without repeating filesystem/header reads on the GUI. Workers still
verify regular files, ownership, dimensions and identity before publication.
"""


class ArtworkPath(str):
    def __new__(cls, path, identity, header, live_valid):
        value = str.__new__(cls, path)
        value.file_identity = tuple(identity)
        value.image_header = tuple(header)
        value.live_valid = bool(live_valid)
        return value

    def __getnewargs__(self):
        return (str(self), self.file_identity, self.image_header, self.live_valid)


def artwork_path(value):
    """Preserve verified facts through preview/fullscreen snapshot handoff."""
    return value if isinstance(value, ArtworkPath) else str(value or "").strip()


def decode_identity(path):
    identity = getattr(path, "file_identity", None)
    return (identity[2], identity[3]) if identity is not None else None
