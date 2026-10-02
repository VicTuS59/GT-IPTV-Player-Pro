# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Boot-scoped download queue shared by Enigma2 and a detached worker.

Only lifecycle changes reach flash. Progress, stream commands and account
credentials stay in a private /tmp directory and never enter the history file.
"""
import fcntl
import hashlib
import json
import os
import re
import stat
import sys
import threading
import time
import uuid
from contextlib import contextmanager

from .i18n import N_
from .playback import resume_account_scope

RUNTIME = "/tmp/gtiptv-downloads"
HISTORY = "/etc/enigma2/gtiptvplayer/downloads.json"
SPEEDS = (100*1024, 250*1024, 500*1024, 750*1024,
          1024*1024, 2*1024*1024, 3*1024*1024, 5*1024*1024, 10*1024*1024,
          20*1024*1024, 30*1024*1024, 40*1024*1024, 50*1024*1024,
          60*1024*1024, 70*1024*1024, 80*1024*1024, 90*1024*1024, 100*1024*1024)
# Adding higher options must not raise the default or overwrite a saved limit.
DEFAULT_SPEED = 10*1024*1024
VIDEO_EXTENSIONS = frozenset((".mp4", ".mkv", ".avi", ".ts", ".m2ts",
    ".mts", ".mov", ".m4v", ".mpg", ".mpeg", ".vob", ".webm", ".wmv", ".flv"))
ACTIVE = ("resolving", "downloading", "stopping")
TERMINAL = ("completed", "cancelled")
STATUS = {
    "queued": N_("Queued"), "resolving": N_("Downloading"),
    "downloading": N_("Downloading"), "stopping": N_("Please wait"),
    "paused": N_("Paused"), "completed": N_("Completed"),
    "deleting": N_("Please wait"),
    "cancelled": N_("Cancelled"), "error": N_("Error"),
    "local": N_("Local file"),
}
MESSAGES = {
    "busy": N_("This account has a download in progress. Pause or cancel it before watching."),
    "playing": N_("This account is playing. Stop playback before downloading."),
    "folder": N_("Choose a writable recording folder first."),
    "space": N_("Not enough free space."),
    "failed": N_("Download failed."),
    "unsupported": N_("This stream is not a downloadable video file."),
    "resume": N_("The server cannot resume this file. Download again from the beginning?"),
    "reboot": N_("Download cancelled after device restart."),
    "media": N_("The video file is incomplete or its format could not be verified."),
    "delete_active": N_("Cancel the active download before deleting it."),
    "delete_failed": N_("The file could not be deleted. Check the storage device and permissions."),
}
_THREAD_LOCK = threading.RLock()


class DownloadError(Exception):
    def __init__(self, code):
        self.code = code
        Exception.__init__(self, MESSAGES.get(code, MESSAGES["failed"]))


def boot_id():
    with open("/proc/sys/kernel/random/boot_id", "r") as handle:
        return handle.read(80).strip()


def process_stamp(pid=None):
    try:
        with open("/proc/{}/stat".format(int(pid or os.getpid())), "r") as handle:
            return handle.read().rsplit(")", 1)[1].split()[19]
    except (OSError, ValueError, IndexError):
        return ""


def alive(record):
    return bool(record and record.get("stamp") and
                process_stamp(record.get("pid", -1)) == record.get("stamp"))


def _private_directory(path):
    os.makedirs(path, mode=0o700, exist_ok=True)
    info = os.lstat(path)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid():
        raise OSError("Unsafe download state directory")
    os.chmod(path, 0o700)


def _read(path, default=None):
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "r", encoding="utf-8") as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else (default or {})
    except FileNotFoundError:
        return default or {}


def _write(path, value):
    temporary = path + "." + uuid.uuid4().hex + ".tmp"
    try:
        fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, separators=(",", ":"))
            handle.flush()
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except OSError:
            pass


def _history(state):
    _private_directory(os.path.dirname(HISTORY))
    value = {key: state.get(key) for key in ("boot", "jobs", "folder", "limit", "hold")}
    _write(HISTORY, value)


def _initial_state():
    current_boot = boot_id()
    state = _read(os.path.join(RUNTIME, "state.json"))
    if state.get("boot") == current_boot:
        for job in state.get("jobs", []):
            if job.get("status") == "deleting" and not alive(job.get("delete_owner")):
                previous = job.pop("delete_previous", "error")
                job["status"] = previous if previous in TERMINAL else "error"
                job.pop("delete_owner", None)
                job["error"] = "delete_failed"
                state["hold"] = True
        if any(j.get("status") in ACTIVE for j in state.get("jobs", [])) and not alive(state.get("worker")):
            for job in state["jobs"]:
                if job.get("status") in ACTIVE:
                    job.update(status="error", error="failed", speed=0, eta=0)
                    job.pop("command", None)
            state["hold"] = True
        if (state.get("worker_start", 0) and time.time() > state["worker_start"]
                and not alive(state.get("worker"))):
            for job in state["jobs"]:
                if job.get("status") == "queued":
                    job.update(status="error", error="failed")
            state["hold"] = True
            state["worker_start"] = 0
        return state
    state = _read(HISTORY)
    state.setdefault("jobs", [])
    for job in state["jobs"]:
        if job.get("status") == "deleting":
            job["status"] = job.pop("delete_previous", "error")
            job.pop("delete_owner", None)
        if job.get("status") not in TERMINAL:
            job.update(status="cancelled", error="reboot", speed=0, eta=0)
    state.update(boot=current_boot, private={}, players={}, reservations={},
                 urls={}, worker={}, hold=True, shutdown=False)
    state.setdefault("limit", DEFAULT_SPEED)
    state.setdefault("folder", "")
    _history(state)
    return state


@contextmanager
def transaction(persist=False):
    with _THREAD_LOCK:
        _private_directory(RUNTIME)
        fd = os.open(os.path.join(RUNTIME, "state.lock"),
                     os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            state = _initial_state()
            yield state
            _write(os.path.join(RUNTIME, "state.json"), state)
            if persist:
                _history(state)
        finally:
            os.close(fd)


def snapshot():
    with transaction() as state:
        return {key: json.loads(json.dumps(state.get(key)))
                for key in ("jobs", "folder", "limit", "hold", "worker")}


def active_for(state, scope):
    return any(job.get("scope") == scope and job.get("status") in ACTIVE
               for job in state["jobs"])


def playing_in(state, scope):
    now = time.time()
    for kind in ("players", "reservations"):
        records = state.setdefault(kind, {})
        for key, record in list(records.items()):
            if not alive(record) or (kind == "reservations" and record.get("until", 0) < now):
                records.pop(key, None)
        if any(record.get("scope") == scope for record in records.values()):
            return True
    return False


def folder_ready(path):
    if not path or not os.path.isdir(path) or not os.access(path, os.W_OK | os.X_OK):
        raise DownloadError("folder")
    # A removed /media disk must never silently redirect a recording to flash.
    real = os.path.realpath(path)
    if real.startswith("/media/") or real.startswith("/mnt/"):
        parent = real
        while parent != "/" and not os.path.ismount(parent):
            parent = os.path.dirname(parent)
        if parent == "/":
            raise DownloadError("folder")
    return real


def free_bytes(folder):
    info = os.statvfs(folder)
    return info.f_bavail * info.f_frsize


def set_folder(folder):
    folder = folder_ready(folder)
    with transaction(persist=True) as state:
        state["folder"] = folder


def set_limit(value):
    value = int(value)
    if value not in SPEEDS:
        raise ValueError("Invalid download limit")
    with transaction(persist=True) as state:
        state["limit"] = value


def _payload(client, item):
    account = client.account
    source = getattr(account, "source_type", "xtream")
    if source == "stalker":
        account_data = {"portal_url": account.portal_url, "mac": account.mac}
    elif source == "xtream":
        account_data = {key: getattr(account, key, "") for key in
            ("name", "scheme", "netloc", "base_path", "username", "password", "output_format")}
    else:
        raise DownloadError("unsupported")
    fields = {key: value for key, value in vars(item).items()
              if not key.startswith("_") and isinstance(value, (str, int, float, bool, type(None)))}
    return {"source": source, "account": account_data, "item": fields}


def enqueue(client, item, poster=""):
    if getattr(item, "content_type", "") not in ("movie", "series"):
        raise DownloadError("unsupported")
    scope = resume_account_scope(client)
    payload = _payload(client, item)
    with transaction(persist=True) as state:
        if playing_in(state, scope):
            raise DownloadError("playing")
        folder = folder_ready(state.get("folder"))
        if free_bytes(folder) < 64 * 1024 * 1024:
            raise DownloadError("space")
        for job in state["jobs"]:
            if (job.get("scope") == scope and job.get("stream_id") == item.stream_id
                    and job.get("kind") == item.content_type and job.get("status") not in TERMINAL):
                return job["id"]
        identity = uuid.uuid4().hex
        title = str(getattr(item, "name", "") or "Video")[:180]
        parent = getattr(item, "favorite_parent", None)
        if parent is not None and getattr(parent, "name", "") not in title:
            title = "{} - {}".format(parent.name, title)[:180]
        filename = re.sub(r"[\x00-\x1f/\\:*?\"<>|]", "_", title).strip(" .")[:100] or "Video"
        filename = filename.encode("utf-8")[:180].decode("utf-8", "ignore")
        extension = "." + str(getattr(item, "extension", "mp4")).lower().lstrip(".")
        if extension not in VIDEO_EXTENSIONS:
            extension = ".mp4"
        path = os.path.join(folder, "{}-{}{}".format(filename, identity[:10], extension))
        job = dict(id=identity, scope=scope, stream_id=item.stream_id, kind=item.content_type,
                   name=title, source=payload["source"], status="queued", error="", path=path,
                   device=os.stat(folder).st_dev, downloaded=0, total=0, speed=0, eta=0,
                   poster=poster if os.path.isfile(poster) else "", created=time.time())
        if state.get("hold"):
            state["jobs"].insert(0, job)
        else:
            state["jobs"].append(job)
        state["private"][identity] = payload
        # An explicit new request may start work again. Paused jobs stay
        # paused, and the requested new job precedes the held waiting queue.
        state["hold"] = False
        state["shutdown"] = False
    ensure_worker()
    return identity


def command(identity, action, restart=False):
    cleanup = None
    with transaction(persist=True) as state:
        job = next((j for j in state["jobs"] if j["id"] == identity), None)
        if not job or job["status"] in TERMINAL + ("deleting",):
            return
        if action in ("pause", "cancel"):
            state["hold"] = True
            if job["status"] in ACTIVE:
                job.update(status="stopping", command=action)
            else:
                job.update(status="paused" if action == "pause" else "cancelled", speed=0, eta=0)
                if action == "cancel":
                    state["private"].pop(identity, None)
                    cleanup = dict(job)
        elif action == "resume":
            if playing_in(state, job["scope"]):
                raise DownloadError("playing")
            if identity not in state["private"]:
                job.update(status="cancelled", error="reboot")
            elif job["status"] not in ACTIVE:
                job.update(status="queued", error="", restart=bool(restart))
                state["jobs"].remove(job)
                state["jobs"].insert(0, job)
                state["hold"] = False
                state["shutdown"] = False
    if cleanup:
        try:
            if os.stat(os.path.dirname(cleanup["path"])).st_dev == cleanup["device"]:
                os.unlink(cleanup["path"] + ".part")
        except OSError:
            pass
    ensure_worker()


def delete_entry(entry):
    """Delete only the confirmed entry, reserving it against queue pickup.

    Disk I/O runs outside the state lock and is called from a GUI worker.
    An active transfer must first be cancelled through the red key.
    """
    identity, path = str(entry.get("id", "")), str(entry.get("path", ""))
    local = identity.startswith("local:")
    if not identity or not os.path.isabs(path):
        raise DownloadError("delete_failed")
    with transaction(persist=True) as state:
        job = next((row for row in state["jobs"] if row["id"] == identity), None)
        if not local and (job is None or job.get("path") != path):
            raise DownloadError("delete_failed")
        if any(row.get("path") == path and row.get("status") in ACTIVE + ("deleting",)
               for row in state["jobs"]):
            raise DownloadError("delete_active")
        if local and os.path.realpath(os.path.dirname(path)) != os.path.realpath(state.get("folder") or "/"):
            raise DownloadError("delete_failed")
        record = dict(job or entry)
        if job is not None:
            job.update(delete_previous=job["status"], status="deleting",
                       delete_owner={"pid": os.getpid(), "stamp": process_stamp()})
    try:
        folder = folder_ready(os.path.dirname(path))
        device = record.get("device")
        if device is not None and os.stat(folder).st_dev != device:
            raise DownloadError("delete_failed")
        # External symlinks are never followed to remove their targets.
        paths = (path,) if local else (path, path + ".part")
        for candidate in paths:
            try:
                info = os.lstat(candidate)
            except FileNotFoundError:
                continue
            if not (stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)):
                raise DownloadError("delete_failed")
            if local and not stat.S_ISLNK(info.st_mode):
                token = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
                if tuple(record.get("file_token") or ()) != token:
                    raise DownloadError("delete_failed")
            os.unlink(candidate)
    except Exception:
        with transaction(persist=True) as state:
            job = next((row for row in state["jobs"] if row["id"] == identity), None)
            if job is not None and job.get("status") == "deleting":
                job["status"] = job.pop("delete_previous", "error")
                job.pop("delete_owner", None)
                job["error"] = "delete_failed"
        raise DownloadError("delete_failed")
    with transaction(persist=True) as state:
        state["jobs"] = [row for row in state["jobs"] if row["id"] != identity]
        state["private"].pop(identity, None)
    # Only artwork owned by this job is eligible for cleanup.
    poster = record.get("poster") or ""
    if not local and os.path.dirname(poster) == os.path.join(folder, ".gtiptv-covers"):
        if os.path.basename(poster) in (identity + ".jpg", identity + ".png"):
            try:
                os.unlink(poster)
            except OSError:
                pass


def ensure_worker():
    """Exec a fresh interpreter, detached from the Enigma2 GUI lifetime."""
    with transaction() as state:
        if alive(state.get("worker")):
            return
        if state.get("worker_start", 0) > time.time():
            return
        state["worker_start"] = time.time() + 20
    # In embedded Enigma2, sys.executable can point to enigma2 itself.
    # Never launch that binary as if it were the standalone interpreter.
    interpreter = next((path for path in ("/usr/bin/python3", "/usr/local/bin/python3")
                        if os.path.isfile(path) and os.access(path, os.X_OK)), "")
    if not interpreter and os.path.basename(sys.executable).startswith("python"):
        interpreter = sys.executable
    if not interpreter:
        raise DownloadError("failed")
    worker = os.path.join(os.path.dirname(__file__), "download_worker.py")
    child = os.fork()
    if child:
        try:
            os.waitpid(child, 0)
        except ChildProcessError:
            pass
        return
    try:
        os.setsid()
        if os.fork():
            os._exit(0)
        os.umask(0o077)
        os.chdir("/")
        os.closerange(0, 65536)
        null = os.open(os.devnull, os.O_RDWR)
        os.dup2(null, 0)
        os.dup2(null, 1)
        os.dup2(null, 2)
        os.execl(interpreter, interpreter, worker, "run")
    finally:
        os._exit(1)


def local_files(folder):
    if not folder or not os.path.isdir(folder):
        return []
    result = []
    with os.scandir(folder) as entries:
        for entry in entries:
            if os.path.splitext(entry.name)[1].lower() not in VIDEO_EXTENSIONS:
                continue
            if not entry.is_file():
                continue
            info = entry.stat()
            if info.st_size <= 0:
                continue
            result.append(dict(id="local:" + hashlib.sha256(entry.path.encode("utf-8")).hexdigest(),
                name=os.path.splitext(entry.name)[0], path=entry.path, status="local", source="local",
                downloaded=info.st_size, total=info.st_size, speed=0, eta=0, poster="", created=info.st_mtime,
                device=info.st_dev, file_token=(info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)))
    return sorted(result, key=lambda row: row["name"].casefold())

