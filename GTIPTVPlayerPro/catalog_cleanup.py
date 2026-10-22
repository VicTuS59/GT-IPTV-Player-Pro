# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Delete recognised obsolete catalogue files; never create cache directories."""
import os
import re
import stat
import threading

from .vod_catalog import DEFAULT_VOD_CATALOG_DIR


_guard = threading.Lock()
_done = False
_OLD = re.compile(r"^[0-9a-f]{64}\.(?:sqlite3(?:-wal|-shm|-journal|\.lock)?|portable\.(?:json(?:\.tmp)?|lock|probe|[0-9]+\.(?:body|members)|mi-[0-9]+-[0-9]+-[0-9]+))$")
_R3 = re.compile(r"^[0-9a-f]{64}\.(?:movie|series)\.portable\.(?:json(?:\.tmp)?|lock|probe|[0-9]+\.(?:body|members)|mi-[0-9]+-[0-9]+-[0-9]+)$")


def clean_previous_catalogues(codec):
    global _done
    if _done:
        return
    # Only this migration uses filesystem APIs; the RAM catalogue itself has
    # no filesystem writer, lock file, manifest or temporary-file backend.
    import fcntl
    with _guard:
        if _done:
            return
        original = codec.directory
        complete = True
        temporary = os.path.join(os.path.realpath("/tmp"), "gtiptvplayer/vod-catalog")
        paths = (os.path.join(DEFAULT_VOD_CATALOG_DIR, "index"),
                 os.path.join(DEFAULT_VOD_CATALOG_DIR, "index", "series"),
                 os.path.join(temporary, "index"), os.path.join(temporary, "index", "series"),
                 os.path.join(temporary, "ram-v1"))
        try:
            for path in paths:
                codec.directory = path
                directory = codec._directory_fd(create=False)
                if directory is None:
                    continue
                locks = []
                try:
                    ram = path == paths[-1]
                    names = [name for name in os.listdir(directory)
                             if (_R3 if ram else _OLD).fullmatch(name)]
                    lock_names = (["session.lock"] if ram else
                                  [name for name in names if name.endswith(".lock")])
                    for name in lock_names:
                        try:
                            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
                        except FileNotFoundError:
                            continue
                        locks.append(fd)
                        info = os.fstat(fd)
                        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                            raise OSError("unsafe catalogue lock")
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    for name in names:
                        info = os.stat(name, dir_fd=directory, follow_symlinks=False)
                        if stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                            os.unlink(name, dir_fd=directory)
                    if ram:
                        try:
                            os.unlink("session.lock", dir_fd=directory)
                        except FileNotFoundError:
                            pass
                except OSError:
                    complete = False
                finally:
                    for fd in locks:
                        os.close(fd)
                    os.close(directory)
        finally:
            codec.directory = original
        _done = complete
