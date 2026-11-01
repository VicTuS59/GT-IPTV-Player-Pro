# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-2.0-or-later
"""Remove only recognised, disposable storage from older plugin versions.

The historical filenames below are migration identifiers, not a database
backend. Authoritative JSON history and shared system packages are untouched.
"""
import os
import re
import stat
import threading

_HISTORY = {
    'etc/enigma2': ('gtiptvplayerpro-episode-history.json',),
    'etc/enigma2/gtiptvplayer': ('downloads.json', 'downloads-removed.json'),
}
_HISTORY_NAMES = tuple(name for names in _HISTORY.values() for name in names)
_SIDE = r'(?:-(?:journal|wal|shm)|\.lock|\.[0-9a-f]{32}\.tmp(?:-(?:journal|wal|shm))?)?'
def _history_pattern(names):
    return re.compile(r'((?:' + '|'.join(re.escape(n) for n in names) + r')\.history-index\.sqlite3)' + _SIDE + r'\Z')


_HISTORY_FILE = _history_pattern(_HISTORY_NAMES)
_CATALOGUE_FILE = re.compile(r'([0-9a-f]{64}\.sqlite3)' + _SIDE + r'\Z')
_CATALOGUES = (
    'etc/enigma2/gtiptvplayer/vod-catalog',
    'etc/enigma2/gtiptvplayer/vod-catalog/index',
    'etc/enigma2/gtiptvplayer/vod-catalog/index/series',
    'tmp/gtiptvplayer/vod-catalog',
    'tmp/gtiptvplayer/vod-catalog/index',
    'tmp/gtiptvplayer/vod-catalog/index/series',
    'tmp/gtiptvplayer/vod-catalog/ram-v1',
)
_START_LOCK = threading.Lock()
_STARTED = False
_LIVE_PLATFORM = os.name == 'posix'


def is_history_sidecar(name):
    return bool(_HISTORY_FILE.fullmatch(str(name)))


class _Directory:
    """Descriptor-relative unlink on receivers; confined offline Windows tests."""
    def __init__(self, root, relative):
        self.fd = None
        self.path = os.path.join(root, *relative.split('/'))
        if os.name == 'posix':
            if not getattr(os, 'O_DIRECTORY', 0) or not getattr(os, 'O_NOFOLLOW', 0):
                raise OSError('Safe directory flags unavailable')
            flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            fd = os.open(os.path.sep, flags)
            try:
                for component in os.path.abspath(self.path).split(os.path.sep):
                    if not component: continue
                    next_fd = os.open(component, flags, dir_fd=fd)
                    os.close(fd); fd = next_fd
                self.fd = fd; fd = None
            finally:
                if fd is not None: os.close(fd)
        elif os.name == 'nt' and os.path.dirname(root) != root:
            # Used only when operating on an explicit offline image/test root.
            # Never enable a path-based fallback for a receiver's live root.
            self._check_windows_path()
        else:
            raise OSError('Safe directory operations unavailable')

    def _check_windows_path(self):
        drive, rest = os.path.splitdrive(os.path.abspath(self.path))
        cursor = drive + os.path.sep
        for component in rest.strip(os.path.sep).split(os.path.sep):
            if not component: continue
            cursor = os.path.join(cursor, component)
            info = os.lstat(cursor)
            if (not stat.S_ISDIR(info.st_mode) or
                    getattr(info, 'st_file_attributes', 0) & 0x400):
                raise OSError('Unsafe offline directory')

    def names(self):
        return os.scandir(self.fd if self.fd is not None else self.path)

    def info(self, name):
        if self.fd is not None: return os.stat(name, dir_fd=self.fd, follow_symlinks=False)
        self._check_windows_path()
        return os.lstat(os.path.join(self.path, name))

    def unlink(self, name):
        if self.fd is not None:
            os.unlink(name, dir_fd=self.fd)
        else:
            self._check_windows_path()
            os.unlink(os.path.join(self.path, name))

    def lock(self, name):
        try:
            info = self.info(name)
        except FileNotFoundError:
            return None
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise OSError('Unsafe legacy lock')
        if info.st_uid != getattr(os, 'geteuid', lambda: info.st_uid)():
            raise OSError('Unexpected lock owner')
        flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
        if self.fd is None: flags = os.O_RDWR | getattr(os, 'O_BINARY', 0)
        fd = os.open(name, flags, dir_fd=self.fd) if self.fd is not None else os.open(os.path.join(self.path, name), flags)
        try:
            opened = os.fstat(fd)
            if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
                raise OSError('Legacy lock changed')
            if os.name == 'posix':
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            else:
                import msvcrt
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            return fd
        except BaseException:
            os.close(fd); raise

    def close(self):
        if self.fd is not None: os.close(self.fd)


def _clean_directory(root, relative, pattern, result):
    directory = None
    try:
        directory = _Directory(root, relative)
        # Acquire existing per-catalogue locks before deleting any sidecar.
        # A held/unsafe lock defers that entire database group until next entry.
        with directory.names() as entries:
            groups = set()
            for entry in entries:
                match = pattern.fullmatch(entry.name)
                if match:
                    groups.add(match.group(1))
        for base in sorted(groups):
            lock_fd = None
            try:
                lock_fd = directory.lock(base + '.lock')
                with directory.names() as entries:
                    for entry in entries:
                        if not (entry.name == base or entry.name.startswith(base + '-') or entry.name.startswith(base + '.')):
                            continue
                        if not pattern.fullmatch(entry.name): continue
                        if entry.name == base + '.lock': continue
                        info = directory.info(entry.name)
                        owner = getattr(os, 'geteuid', lambda: info.st_uid)()
                        if info.st_uid != owner or not (stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)):
                            result['skipped'] += 1; continue
                        directory.unlink(entry.name)
                        result['removed'] += 1
                # Keep the locked inode linked until all its data files are gone.
                if lock_fd is not None:
                    if directory.fd is None:
                        # Windows offline-image tests cannot unlink an open
                        # lock. Receivers keep the descriptor locked through unlink.
                        os.close(lock_fd); lock_fd = None
                    directory.unlink(base + '.lock'); result['removed'] += 1
            except FileNotFoundError:
                pass  # Another migration already removed this disposable file.
            except OSError:
                result['skipped'] += 1
            finally:
                if lock_fd is not None: os.close(lock_fd)
    except FileNotFoundError:
        pass
    except OSError:
        result['skipped'] += 1
    finally:
        if directory is not None: directory.close()


def clean_legacy_storage(root=None):
    root = os.path.abspath(root or os.path.sep)
    result = dict(removed=0, skipped=0)
    for relative, names in _HISTORY.items():
        _clean_directory(root, relative, _history_pattern(names), result)
    paths = list(_CATALOGUES)
    # OpenATV may expose /tmp through this system-managed mount alias. Never
    # resolve an arbitrary link or an absolute link in an offline image root.
    if root == os.path.sep and os.path.realpath('/tmp') == '/var/volatile/tmp':
        paths = [p.replace('tmp/', 'var/volatile/tmp/', 1) if p.startswith('tmp/') else p for p in paths]
    for relative in paths:
        _clean_directory(root, relative, _CATALOGUE_FILE, result)
    return result


def schedule_legacy_cleanup():
    global _STARTED
    if not _LIVE_PLATFORM: return
    with _START_LOCK:
        if _STARTED: return
        _STARTED = True
    def run():
        try:
            result = clean_legacy_storage()
            if result['removed'] or result['skipped']:
                from .diagnostics import log_event
                log_event('storage', 'legacy caches: removed={}, deferred={}'.format(result['removed'], result['skipped']))
        except Exception:
            pass  # Optional migration cannot prevent the JSON backend opening.
    try:
        threading.Thread(target=run, name='gt-legacy-storage', daemon=True).start()
    except RuntimeError:
        with _START_LOCK: _STARTED = False


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Clean only obsolete GT IPTV cache files')
    parser.add_argument('--root', default=os.path.sep)
    arguments = parser.parse_args()
    summary = clean_legacy_storage(arguments.root)
    print('GT IPTV Player Pro: old caches removed={}, deferred={}'.format(summary['removed'], summary['skipped']))
