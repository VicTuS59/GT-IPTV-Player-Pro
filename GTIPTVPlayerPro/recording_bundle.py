"""Recognize and manage only this plugin's local HLS recording bundles."""
import hashlib
import os
import re
import stat

_DIRECTORY = re.compile(r'^\d{8}_\d{6}_.+_[0-9a-f]{6}$')
_ASSET = re.compile(r'^[0-9a-f]{32}\.(?:ts|aac|m4s|mp4|vtt|key)(?:\.part)?$')
_PLAYLISTS = frozenset(('index.m3u8', 'video.m3u8', 'audio.m3u8'))


def _plain(path):
    value = os.lstat(path)
    if not stat.S_ISREG(value.st_mode) or value.st_nlink != 1:
        raise OSError('Unsafe recording bundle file')
    return value


def _manifest(path):
    _plain(path)
    with open(path, 'r', encoding='utf-8-sig') as source:
        if source.readline(16385).strip() != '#EXTM3U': raise ValueError('Invalid recording playlist')
        for line in iter(lambda: source.readline(16385), ''):
            if len(line) > 16384: raise ValueError('Invalid recording line')
            value = line.strip()
            names = re.findall(r'URI="([^"]+)"', value) if value.startswith('#') else [value] if value else []
            for name in names:
                if name not in _PLAYLISTS and (not _ASSET.fullmatch(name) or name.endswith('.part')):
                    raise ValueError('Nonlocal recording resource')


def inspect_bundle(directory):
    directory = os.path.abspath(directory)
    if not _DIRECTORY.fullmatch(os.path.basename(directory)): raise ValueError('Not a recording bundle')
    root = os.lstat(directory)
    if not stat.S_ISDIR(root.st_mode): raise OSError('Unsafe recording bundle directory')
    total = count = 0
    present = set()
    with os.scandir(directory) as entries:
        for entry in entries:
            if entry.name not in _PLAYLISTS and not _ASSET.fullmatch(entry.name):
                raise ValueError('Unknown recording bundle file')
            info = _plain(entry.path)
            total += info.st_size; count += 1
            if entry.name in _PLAYLISTS: present.add(entry.name)
    if 'index.m3u8' not in present or not present.intersection(('video.m3u8', 'audio.m3u8')):
        raise ValueError('Incomplete recording bundle')
    for name in present: _manifest(os.path.join(directory, name))
    path = os.path.join(directory, 'index.m3u8')
    info = _plain(path)
    token = (root.st_dev, root.st_ino, root.st_mtime_ns, info.st_ino, info.st_size, info.st_mtime_ns, count, total)
    title = os.path.basename(directory)
    return dict(id='local:'+hashlib.sha256(path.encode('utf-8')).hexdigest(), name=title,
                path=path, bundle=directory, bundle_token=token, status='local', source='local',
                downloaded=total, total=total, speed=0, eta=0, poster='', created=root.st_mtime,
                device=root.st_dev, file_token=(info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns),
                format='hls', size=total)


def inspect_entry(entry):
    directory = str(entry.get('bundle') or '')
    if os.path.join(directory, 'index.m3u8') != entry.get('path'): raise ValueError('Invalid recording path')
    value = inspect_bundle(directory)
    if tuple(entry.get('bundle_token') or ()) != value['bundle_token']:
        raise OSError('Recording bundle changed')
    return value


def delete_entry(entry):
    value = inspect_entry(entry)
    directory = value['bundle']
    parent, name = os.path.split(directory)
    if os.name != 'posix':
        # Host test adapter; Enigma2 uses directory descriptors below.
        with os.scandir(directory) as entries:
            for item in entries:
                _plain(item.path); os.unlink(item.path)
        os.rmdir(directory); return
    parent_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    descriptor = None
    try:
        descriptor = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
        info = os.fstat(descriptor)
        if (info.st_dev, info.st_ino) != value['bundle_token'][:2]: raise OSError('Recording directory changed')
        with os.scandir(descriptor) as entries:
            for item in entries:
                if item.name not in _PLAYLISTS and not _ASSET.fullmatch(item.name): raise OSError('Unexpected recording file')
                info = os.stat(item.name, dir_fd=descriptor, follow_symlinks=False)
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1: raise OSError('Unsafe recording file')
                os.unlink(item.name, dir_fd=descriptor)
        os.rmdir(name, dir_fd=parent_fd)
    finally:
        if descriptor is not None: os.close(descriptor)
        os.close(parent_fd)
