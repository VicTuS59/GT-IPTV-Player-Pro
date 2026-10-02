# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Receiver-local MPEG-TS remux for YouTube's separate H.264/AAC streams.

The original signed URLs stay in memory.  Only a loopback listener with a
random per-video path is handed to the Enigma2 HTTP player.  FFmpeg copies
both encoded tracks into one transport stream without transcoding them.

Each explicitly created loopback path also owns a bounded start position.
This lets the fullscreen player seek by opening a fresh private path whose
FFmpeg inputs begin at the requested second.  Ordinary playback still uses
the exact zero-offset R61 input path.
"""

import os
import secrets
import shutil
import socket
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit


MAX_START_SECONDS = 31 * 24 * 60 * 60
MAX_SEEK_PATHS = 32


def _valid_stream_url(value):
    if not isinstance(value, str) or not (0 < len(value) <= 8192):
        return False
    if any(char in value for char in ('\r', '\n', '\x00')):
        return False
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or '').lower()
    except ValueError:
        return False
    return parsed.scheme == 'https' and (host == 'googlevideo.com' or host.endswith('.googlevideo.com'))


class _LoopbackHandler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.0'

    def log_message(self, unused_format, *unused_args):
        # The signed media URL must never enter a receiver log.
        pass

    def _headers(self):
        owner = self.server.remux
        start_seconds = owner.start_for_path(self.path)
        if start_seconds is None:
            self.send_error(404)
            return None
        self.send_response(200)
        self.send_header('Content-Type', 'video/mp2t')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Connection', 'close')
        self.end_headers()
        return start_seconds

    def do_HEAD(self):
        self._headers()

    def do_GET(self):
        start_seconds = self._headers()
        if start_seconds is None:
            return
        self.server.remux.stream_to(self.wfile, start_seconds)


class YouTubeRemux(object):
    def __init__(self, video_url, audio_url, start_seconds=0):
        if not _valid_stream_url(video_url) or not _valid_stream_url(audio_url):
            raise ValueError('invalid YouTube media URL')
        ffmpeg = shutil.which('ffmpeg')
        if not ffmpeg or not os.access(ffmpeg, os.X_OK):
            raise OSError('ffmpeg is unavailable')
        self._ffmpeg = ffmpeg
        self._video_url = video_url
        self._audio_url = audio_url
        self._lock = threading.RLock()
        self._closed = False
        self._process = None
        self._base_path = '/' + secrets.token_urlsafe(24)
        self._paths = {}
        self._server = ThreadingHTTPServer(('127.0.0.1', 0), _LoopbackHandler)
        self._server.remux = self
        self.url = self.url_for(start_seconds)
        self.path = urlsplit(self.url).path
        thread = threading.Thread(target=self._server.serve_forever,
                                  kwargs={'poll_interval': 0.1}, name='GT-YouTube-Remux')
        thread.daemon = True
        thread.start()

    @property
    def closed(self):
        with self._lock:
            return self._closed

    @staticmethod
    def _bounded_start(value):
        try:
            value = int(value)
        except (TypeError, ValueError, OverflowError):
            raise ValueError('invalid YouTube seek position')
        if value < 0 or value > MAX_START_SECONDS:
            raise ValueError('invalid YouTube seek position')
        return value

    def url_for(self, start_seconds):
        """Return a private path which starts both inputs at an absolute second."""
        start_seconds = self._bounded_start(start_seconds)
        with self._lock:
            if self._closed:
                raise ValueError('YouTube remux is closed')
            path = '{}/{}/stream.ts'.format(
                self._base_path,
                secrets.token_urlsafe(18),
            )
            self._paths[path] = start_seconds
            while len(self._paths) > MAX_SEEK_PATHS:
                self._paths.pop(next(iter(self._paths)))
            port = self._server.server_port
        return 'http://127.0.0.1:{}{}'.format(port, path)

    def start_for_path(self, path):
        with self._lock:
            if self._closed:
                return None
            return self._paths.get(str(path or ''))

    def _ffmpeg_command(self, start_seconds):
        command = [
            self._ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin',
            '-fflags', '+genpts',
        ]
        for source in (self._video_url, self._audio_url):
            if start_seconds > 0:
                command.extend(('-ss', str(int(start_seconds))))
            command.extend(('-rw_timeout', '12000000', '-i', source))
        command.extend((
            '-map', '0:v:0', '-map', '1:a:0', '-c', 'copy',
            '-avoid_negative_ts', 'make_zero',
            '-f', 'mpegts', '-muxdelay', '0', 'pipe:1',
        ))
        return command

    def stream_to(self, output, start_seconds=0):
        start_seconds = self._bounded_start(start_seconds)
        with self._lock:
            if self._closed:
                return
            if self._process is not None:
                self._process.terminate()
            command = self._ffmpeg_command(start_seconds)
            try:
                process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                           stdout=subprocess.PIPE,
                                           stderr=subprocess.DEVNULL, close_fds=True)
            except (OSError, ValueError):
                return
            self._process = process
        try:
            while not self.closed:
                chunk = os.read(process.stdout.fileno(), 64 * 1024)
                if not chunk:
                    break
                output.write(chunk)
                output.flush()
        except (OSError, IOError, ValueError):
            pass
        finally:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            process.stdout.close()
            with self._lock:
                if self._process is process:
                    self._process = None

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            process = self._process
            self._video_url = ''
            self._audio_url = ''
            self._paths.clear()
        if process is not None and process.poll() is None:
            process.terminate()
        self._server.shutdown()
        self._server.server_close()

