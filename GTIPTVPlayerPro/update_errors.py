# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-2.0-or-later
"""Typed updater failures; translate at the GUI boundary, never show raw logs."""
import errno
import functools
import os
import re
import shutil
import socket
import ssl
import stat
from urllib.error import URLError


class UpdateError(Exception):
    def __init__(self, code, reason='', **context):
        self.code = code
        self.reason = reason or code
        self.context = context
        self.stage = ''
        super().__init__(code)


ERROR_TEXTS = {
    'network': 'Cannot connect to the update server. Check the network and retry.',
    'timeout': 'Request timed out',
    'dns': 'DNS lookup failed. Check the network settings.',
    'certificate': 'Secure connection failed. Check the receiver date and certificates.',
    'http': 'Update server returned HTTP {status}. Try again later.',
    'interrupted': 'Download interrupted. Download the update again.',
    'space': 'Not enough free space.',
    'storage': 'Storage is unavailable or not writable.',
    'readonly': 'Storage is read-only.',
    'permission': 'Storage access denied. Check permissions.',
    'state': 'Update settings could not be read or saved. Restart the receiver.',
    'recording': 'Stop the active recording before updating.',
    'downloading': 'Pause active downloads before updating.',
    'activity': 'Activity status could not be checked. Restart the receiver.',
    'busy': 'Another update is running. Please wait.',
    'install': 'Package installation failed. Check the installation log.',
    'dependency': 'Required package missing: {package}.',
    'conflict': 'Package conflict. Check installed packages.',
    'opkg_lock': 'The package manager is busy. Try again later.',
    'opkg_missing': 'The package manager is unavailable on this receiver.',
    'root': 'Administrator permission is required to install updates.',
    'configure': 'Package configuration failed. Restart the receiver and retry.',
    'source': 'Update source is not ready',
    'signature': 'Update signature is invalid',
    'metadata': 'Invalid update information',
    'package': 'Update package is invalid',
    'compatibility': 'Update is not compatible with this receiver',
    'restart': 'Update installed. Restart the interface.',
    'version': 'Up to date',
    'cancelled': 'Cancelled',
    'failed': 'Unexpected update error. Restart the receiver and retry.',
}
POST_INSTALL_WARNING = 'Update installed, but its status could not be saved. Restart the interface.'
STAGE_TEXTS = {'check': 'Check for updates', 'download': 'Download and verify',
               'install': 'Install update'}
_PACKAGE_NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9+_.-]{0,95}\Z')
_OPKG_SPACE = re.compile(
    r'only have (?P<available>[0-9]{1,15})\s*ki?b available on filesystem '
    r'(?P<path>[^\r\n]{1,512}?), pkg [A-Za-z0-9+_.-]{1,96} needs '
    r'(?P<required>[0-9]{1,15})(?:\s*ki?b)?\b', re.IGNORECASE,
)


def storage_error(error, path=''):
    number = getattr(error, 'errno', None)
    if number in (errno.ENOSPC, getattr(errno, 'EDQUOT', errno.ENOSPC)):
        return UpdateError('space', path=path)
    reason = ('readonly' if number == errno.EROFS else
              'permission' if number in (errno.EACCES, errno.EPERM) else 'storage')
    return UpdateError('storage', reason, path=path)


def network_error(error):
    cause = error.reason if isinstance(error, URLError) else error
    if isinstance(cause, (ssl.SSLError, ssl.CertificateError)):
        return UpdateError('network', 'certificate')
    if isinstance(cause, (TimeoutError, socket.timeout)):
        return UpdateError('network', 'timeout')
    if isinstance(cause, socket.gaierror):
        return UpdateError('network', 'dns')
    return UpdateError('network')


def report(error, stage):
    if not error.stage:
        error.stage = stage
    try:
        from .diagnostics import log_event
        log_event('update', '{}: {} / {}'.format(error.stage, error.code, error.reason))
    except Exception:
        pass
    return error


def reported(stage):
    def decorate(function):
        @functools.wraps(function)
        def invoke(*args, **kwargs):
            try:
                return function(*args, **kwargs)
            except UpdateError as error:
                report(error, stage)
                raise
            except OSError as error:
                raise report(storage_error(error, getattr(error, 'filename', '') or ''), stage) from error
            except Exception as error:
                raise report(UpdateError('failed'), stage) from error
        return invoke
    return decorate


def require_space(path, required):
    available = shutil.disk_usage(path).free
    if available < required:
        raise UpdateError('space', path=path, required=required, available=available)


def installation_error(log, code=None):
    """Classify a bounded opkg log; arbitrary output never enters translated UI."""
    text = ''
    try:
        fd = os.open(log, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
        with os.fdopen(fd, 'rb') as handle:
            info = os.fstat(handle.fileno())
            if stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid():
                handle.seek(max(0, info.st_size - 128 * 1024))
                text = handle.read(128 * 1024).decode('utf-8', errors='replace')
    except OSError:
        pass
    lower = text.lower()
    reason = 'install'
    context = dict(log=log)
    if isinstance(code, int):
        context['exit_code'] = code
    space = _OPKG_SPACE.search(text)
    if space:
        context['available'] = int(space['available']) * 1024
        context['required'] = int(space['required']) * 1024
        if re.fullmatch(r'/[A-Za-z0-9_./+ -]{0,255}', space['path']):
            context['path'] = space['path']
        return UpdateError('space', **context)
    if 'no space left' in lower or 'not enough space' in lower:
        return UpdateError('space', **context)
    if 'read-only file system' in lower:
        return UpdateError('storage', 'readonly', **context)
    if 'permission denied' in lower:
        return UpdateError('storage', 'permission', **context)
    if any(part in lower for part in ('could not lock', 'cannot lock', 'resource temporarily unavailable')):
        reason = 'opkg_lock'
    elif any(part in lower for part in ('cannot find dependency', 'cannot satisfy', 'nothing provides',
                                       'unresolved dependenc', 'unknown package')):
        match = re.search(r'(?:cannot find dependency|nothing provides|unknown package)\s+[\x22\x27]?([A-Za-z0-9][A-Za-z0-9+_.-]{0,95})', text, re.I)
        if match and _PACKAGE_NAME.fullmatch(match.group(1)):
            reason = 'dependency'
            context['package'] = match.group(1)
        else:
            # Keep a precise category even when opkg omits the package name.
            reason = 'dependency'
            context['package'] = '—'
    elif any(part in lower for part in ('file clashes', 'file_clashes', 'conflicts with', 'conflicting requests')):
        reason = 'conflict'
    elif any(part in lower for part in ('postinst script returned', 'configuration failed')):
        reason = 'configure'
    return UpdateError('install', reason, **context)


def format_error(error, translate):
    reason = getattr(error, 'reason', '') or getattr(error, 'code', 'failed')
    source = ERROR_TEXTS.get(reason, ERROR_TEXTS['failed'])
    context = getattr(error, 'context', {})
    # Only validated placeholders cross the UI boundary, never exception text.
    package = context.get('package', '—')
    if not isinstance(package, str) or not _PACKAGE_NAME.fullmatch(package):
        package = '—'
    status = context.get('status', 0)
    status = status if type(status) is int and 100 <= status <= 599 else 0
    detail = translate(source).format(package=package, status=status)
    path = context.get('path', '')
    if isinstance(path, str) and path.startswith('/') and '\n' not in path and len(path) <= 4096:
        detail += '\n{}: {}'.format(translate('Storage'), path)
    for name, label in (('required', 'Required space'), ('available', 'Free space')):
        value = context.get(name)
        if type(value) is int and value >= 0:
            detail += '\n{}: {:.1f} MiB'.format(translate(label), value / (1024.0 * 1024.0))
    code = context.get('exit_code')
    if type(code) is int:
        detail += '\n{}: {}'.format(translate('Error'), code)
    log = context.get('log')
    if isinstance(log, str) and log.startswith('/') and '\n' not in log and len(log) <= 4096:
        detail += '\n' + log
    return detail
