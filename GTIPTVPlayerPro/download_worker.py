# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Standalone, single-stream HTTP downloader; imports no Enigma2 widgets."""
import fcntl
import os
import re
import signal
import socket
import sys
import threading
import time
from urllib.error import HTTPError
from urllib.parse import parse_qsl, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

if not __package__:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    __package__ = "GTIPTVPlayerPro"

from . import downloads as store
from .content import ContentItem, XtreamContentClient
from .playlist import PlaylistAccount
from .diagnostics import log_event
from .local_media import MediaFileError, inspect_media, matching_extension


class MediaRedirect(HTTPRedirectHandler):
    max_redirections = 5

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        old, new = urlsplit(req.full_url), urlsplit(newurl)
        if new.scheme not in ("http", "https") or not new.hostname or new.username or new.password:
            raise store.DownloadError("unsupported")
        if old.scheme == "https" and new.scheme != "https":
            raise store.DownloadError("failed")
        result = HTTPRedirectHandler.redirect_request(self, req, fp, code, msg, headers, newurl)
        if result is not None and (old.scheme, old.netloc) != (new.scheme, new.netloc):
            for name in ("Authorization", "Cookie", "X-User-Agent", "Referer"):
                result.remove_header(name)
                result.remove_header(name.capitalize())
        return result


def _media_request(url, offset, job):
    url = str(url or "")
    separator = "#" if "#" in url else "|" if "|" in url else None
    headers = {}
    if separator:
        url, extra = url.split(separator, 1)
        allowed = {"user-agent", "referer", "cookie", "authorization", "x-user-agent", "accept"}
        for name, value in parse_qsl(extra, keep_blank_values=True):
            if name.lower() in allowed and "\r" not in value and "\n" not in value:
                headers[name] = value
    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise store.DownloadError("unsupported")
    if parsed.path.lower().endswith((".m3u8", ".m3u", ".mpd")):
        raise store.DownloadError("unsupported")
    headers.setdefault("User-Agent", "GTIPTVPlayerPro/1.1.0-r42")
    headers.update({"Accept-Encoding": "identity", "Connection": "close"})
    if offset:
        headers["Range"] = "bytes={}-".format(offset)
        validator = job.get("etag") or job.get("modified")
        if validator:
            headers["If-Range"] = validator
    return Request(url, headers=headers)


def _client_item(payload):
    if payload["source"] == "stalker":
        from .stalker import PortalAccount, StalkerContentClient
        client = StalkerContentClient(PortalAccount(**payload["account"]))
    else:
        client = XtreamContentClient(PlaylistAccount(**payload["account"]))
    fields = payload["item"]
    item = ContentItem(fields["content_type"], fields["stream_id"], fields["name"])
    for name, value in fields.items():
        if not name.startswith("_"):
            setattr(item, name, value)
    return client, item


class Worker(object):
    def __init__(self):
        self.stopping = threading.Event()
        self.cancel = threading.Event()
        self.response = None
        self.limit = store.DEFAULT_SPEED
        self.action = ""
        self.current = None

    def stop(self, *unused):
        self.stopping.set()
        self.cancel.set()
        self._close_connection()

    def _close_connection(self):
        response = self.response
        if response is None:
            return
        # shutdown unblocks a read immediately; close alone can wait on the
        # buffered-reader lock while a provider has stopped sending bytes.
        try:
            response.fp.raw._sock.shutdown(socket.SHUT_RDWR)
        except Exception:
            pass

    def _monitor(self, identity, done):
        while not done.wait(0.2):
            try:
                with store.transaction() as state:
                    job = next((j for j in state["jobs"] if j["id"] == identity), {})
                    self.limit = int(state.get("limit") or store.DEFAULT_SPEED)
                    self.action = job.get("command", "")
                    shutdown = state.get("shutdown", False)
                if self.action or shutdown or self.stopping.is_set():
                    if shutdown:
                        self.stopping.set()
                    self.cancel.set()
                    self._close_connection()
                    return
            except Exception:
                self.cancel.set()
                self._close_connection()
                return

    def _update(self, identity, persist=False, **values):
        with store.transaction(persist=persist) as state:
            job = next((j for j in state["jobs"] if j["id"] == identity), None)
            if job is not None:
                # A progress write must not erase a just-arrived pause/cancel.
                if job.get("command"):
                    values.pop("status", None)
                job.update(values)

    @staticmethod
    def _check_folder(job, remaining=0):
        folder = store.folder_ready(os.path.dirname(job["path"]))
        if os.stat(folder).st_dev != job["device"]:
            raise store.DownloadError("folder")
        if store.free_bytes(folder) < max(0, remaining) + 32 * 1024 * 1024:
            raise store.DownloadError("space")

    def _download(self, job, payload):
        identity = job["id"]
        self._check_folder(job)
        # Preserve an already-cached cover beside the recordings. This work
        # runs in the detached process, so NAS writes cannot freeze the GUI.
        poster = job.get("poster", "")
        if poster and os.path.isfile(poster) and poster.startswith("/tmp/"):
            try:
                with open(poster, "rb") as artwork:
                    data = artwork.read(4 * 1024 * 1024 + 1)
                if 0 < len(data) <= 4 * 1024 * 1024:
                    directory = os.path.join(os.path.dirname(job["path"]), ".gtiptv-covers")
                    os.makedirs(directory, mode=0o755, exist_ok=True)
                    if not os.path.islink(directory):
                        suffix = ".png" if data.startswith(b"\x89PNG") else ".jpg"
                        cover = os.path.join(directory, identity + suffix)
                        fd = os.open(cover, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
                        with os.fdopen(fd, "wb") as artwork:
                            artwork.write(data)
                        self._update(identity, persist=True, poster=cover)
            except OSError:
                pass
        partial = job["path"] + ".part"
        if job.get("restart"):
            if os.path.islink(partial):
                raise store.DownloadError("folder")
            try:
                os.unlink(partial)
            except FileNotFoundError:
                pass
            job.update(etag="", modified="", downloaded=0, total=0)
            self._update(identity, persist=True, restart=False, etag="", modified="", downloaded=0, total=0)
        if os.path.islink(partial) or os.path.exists(job["path"]):
            raise store.DownloadError("folder")
        offset = os.path.getsize(partial) if os.path.isfile(partial) else 0
        client, item = _client_item(payload)
        if payload["source"] == "stalker":
            url = client.playback_url(item, cancel_event=self.cancel, deadline=time.monotonic() + 30)
        else:
            url = client.playback_url(item)
        if self.cancel.is_set():
            return False
        try:
            response = build_opener(MediaRedirect()).open(_media_request(url, offset, job), timeout=12)
        except HTTPError as error:
            code = error.code
            error.close()
            if offset and code == 416:
                raise store.DownloadError("resume")
            raise store.DownloadError("failed")
        self.response = response
        try:
            status = response.getcode()
            headers = response.headers
            mime = (headers.get("Content-Type") or "").lower().split(";", 1)[0]
            if ("mpegurl" in mime or "dash+xml" in mime or mime.startswith("text/")
                    or mime in ("application/json", "application/xml")):
                raise store.DownloadError("unsupported")
            if headers.get("Content-Encoding", "identity").lower() not in ("", "identity"):
                raise store.DownloadError("unsupported")
            length = int(headers.get("Content-Length") or 0)
            total = offset + length if length else 0
            if status == 206:
                match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+|\*)", headers.get("Content-Range", ""))
                if (not match or int(match.group(1)) != offset or int(match.group(2)) < offset
                        or (length and int(match.group(2)) - offset + 1 != length)):
                    raise store.DownloadError("resume")
                total = int(match.group(3)) if match.group(3) != "*" else 0
                if total and int(match.group(2)) != total - 1:
                    raise store.DownloadError("resume")
            elif offset:
                raise store.DownloadError("resume")
            elif status != 200:
                raise store.DownloadError("failed")
            etag = headers.get("ETag", "")
            if etag.startswith("W/"):
                etag = ""
            modified = headers.get("Last-Modified", "")
            if offset and ((job.get("etag") and etag != job["etag"])
                           or (not job.get("etag") and job.get("modified") and modified != job["modified"])
                           or (job.get("total") and total and job["total"] != total)):
                raise store.DownloadError("resume")
            self._check_folder(job, max(0, total - offset))
            self._update(identity, persist=True, status="downloading", total=total,
                         downloaded=offset, etag=etag, modified=modified)
            flags = os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
            flags |= os.O_APPEND if offset else (os.O_TRUNC if os.path.isfile(partial) else os.O_CREAT | os.O_EXCL)
            fd = os.open(partial, flags, 0o644)
            received, started, report = offset, time.monotonic(), time.monotonic()
            sample_bytes = offset
            token_time, tokens, old_limit = started, 0.0, self.limit
            first = True
            with os.fdopen(fd, "ab" if offset else "wb") as output:
                while not self.cancel.is_set():
                    now = time.monotonic()
                    limit = max(store.SPEEDS[0], min(store.SPEEDS[-1], self.limit))
                    if limit != old_limit:
                        tokens = 0.0
                        old_limit = limit
                    tokens = min(float(65536), tokens + (now - token_time) * limit)
                    token_time = now
                    amount = min(32768, int(tokens))
                    if amount < 4096:
                        self.cancel.wait(min(0.05, (4096 - amount) / float(limit)))
                        continue
                    chunk = response.read(amount)
                    if not chunk:
                        break
                    if first and not offset:
                        prefix = chunk[:128].lstrip().lstrip(b"\xef\xbb\xbf").lstrip().lower()
                        if prefix.startswith((b"#extm3u", b"<!doctype", b"<html", b"<?xml", b'{"')):
                            raise store.DownloadError("unsupported")
                    first = False
                    if self.cancel.is_set():
                        break
                    output.write(chunk)
                    received += len(chunk)
                    tokens -= len(chunk)
                    now = time.monotonic()
                    if now - report >= 1:
                        self._check_folder(job)
                        speed = int((received - sample_bytes) / max(0.001, now - report))
                        self._update(identity, downloaded=received, speed=speed,
                            eta=int((total-received)/speed) if total > received and speed else 0)
                        report = now
                        sample_bytes = received
                output.flush()
                os.fsync(output.fileno())
            self._update(identity, downloaded=received, speed=0, eta=0)
            if self.cancel.is_set():
                return False
            if received <= 0 or (total and received != total):
                raise store.DownloadError("failed")
            try:
                media = inspect_media(partial, expected_size=received)
            except MediaFileError as error:
                log_event("download", "job={} validation={}".format(identity[:10], str(error)))
                raise store.DownloadError("media")
            if self.cancel.is_set():
                return False
            self._check_folder(job)
            suffix = matching_extension(job["path"], media["format"])
            destination = os.path.splitext(job["path"])[0] + suffix
            if os.path.lexists(destination):
                raise store.DownloadError("folder")
            os.rename(partial, destination)
            self._update(identity, path=destination, downloaded=received, total=received,
                         media_format=media["format"],
                         file_token=(media["device"], media["inode"], media["size"], media["mtime_ns"]))
            log_event("download", "job={} format={} bytes={} declared={} validation=basic-ok".format(
                identity[:10], media["format"], received, total or "unknown"))
            return True
        finally:
            try:
                response.close()
            finally:
                self.response = None

    def _run_job(self, job, payload):
        self.cancel.clear()
        self.action = ""
        done = threading.Event()
        watcher = threading.Thread(target=self._monitor, args=(job["id"], done))
        watcher.daemon = True
        watcher.start()
        success, error = False, ""
        try:
            success = self._download(job, payload)
        except store.DownloadError as failure:
            error = failure.code
        except Exception:
            # Provider URLs, tokens and HTTP exception details are never logged.
            error = "failed"
        finally:
            done.set()
            watcher.join(1)
            self.response = None
        # The connection has closed before releasing the account reservation.
        with store.transaction(persist=True) as state:
            target = next(j for j in state["jobs"] if j["id"] == job["id"])
            action = target.pop("command", "")
            target.update(speed=0, eta=0)
            if success:
                target.update(status="completed", error="")
                state["private"].pop(job["id"], None)
            elif action == "cancel" or self.stopping.is_set():
                target.update(status="cancelled", error="")
                state["private"].pop(job["id"], None)
            elif action == "pause":
                target.update(status="paused", error="")
            elif error == "resume":
                target.update(status="paused", error="resume")
            else:
                target.update(status="error", error=error or "failed")
            if not success:
                state["hold"] = True
            cancelled = target["status"] == "cancelled"
        if cancelled:
            try:
                if os.stat(os.path.dirname(job["path"])).st_dev == job["device"]:
                    os.unlink(job["path"] + ".part")
            except OSError:
                pass

    def run(self):
        # flock closes the race between two GUI actions launching workers.
        store._private_directory(store.RUNTIME)
        lock = os.open(os.path.join(store.RUNTIME, "worker.lock"),
                       os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(lock)
            return
        signal.signal(signal.SIGTERM, self.stop)
        signal.signal(signal.SIGINT, self.stop)
        try:
            with store.transaction(persist=True) as state:
                state["worker"] = {"pid": os.getpid(), "stamp": store.process_stamp()}
                state["worker_start"] = 0
                for job in state["jobs"]:
                    if job["status"] in store.ACTIVE:
                        job.update(status="error", error="failed", speed=0, eta=0)
                        state["hold"] = True
            while not self.stopping.is_set():
                job, payload = None, None
                with store.transaction() as state:
                    if state.get("shutdown"):
                        break
                    if not state.get("hold"):
                        for candidate in state["jobs"]:
                            if candidate["status"] != "queued":
                                continue
                            if store.playing_in(state, candidate["scope"]):
                                candidate["error"] = "playing"
                                continue
                            payload = state["private"].get(candidate["id"])
                            if payload is None:
                                candidate.update(status="cancelled", error="reboot")
                                continue
                            candidate.update(status="resolving", error="", speed=0)
                            job = dict(candidate)
                            self.limit = int(state.get("limit") or store.DEFAULT_SPEED)
                            break
                if job:
                    self._run_job(job, payload)
                else:
                    # Stay idle for this boot. This also avoids an enqueue
                    # racing a worker's last idle check and process exit.
                    self.stopping.wait(0.6)
        finally:
            with store.transaction() as state:
                state["worker"] = {}
            os.close(lock)


def stop_existing():
    """Package removal stops only our verified worker, never Enigma2."""
    if not os.path.isdir(store.RUNTIME):
        return
    with store.transaction(persist=True) as state:
        state.update(shutdown=True, hold=True)
        worker = dict(state.get("worker") or {})
        for job in state["jobs"]:
            if job["status"] not in store.TERMINAL and job["status"] not in store.ACTIVE:
                job.update(status="cancelled", speed=0, eta=0)
                state["private"].pop(job["id"], None)
    if store.alive(worker):
        try:
            os.kill(int(worker["pid"]), signal.SIGTERM)
        except OSError:
            pass
        deadline = time.monotonic() + 3
        while store.alive(worker) and time.monotonic() < deadline:
            time.sleep(0.1)
        if store.alive(worker):
            try:
                os.kill(int(worker["pid"]), signal.SIGKILL)
            except OSError:
                pass
    with store.transaction(persist=True) as state:
        for job in state["jobs"]:
            if job["status"] not in store.TERMINAL:
                job.update(status="cancelled", speed=0, eta=0)
        state["private"] = {}
        state["worker"] = {}


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "stop":
        stop_existing()
    else:
        Worker().run()

