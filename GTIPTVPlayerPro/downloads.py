# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Boot-scoped download queue shared by Enigma2 and a detached worker.

Only lifecycle changes reach flash. Progress, stream commands and account
credentials stay in a private /tmp directory and never enter the history file.
"""
import base64
import fcntl
from copy import deepcopy
import hashlib
import json
import os
import re
import stat
import sys
import threading
import time
import uuid
from contextlib import contextmanager, nullcontext

from .i18n import N_
from .persistence import RESTORE_COORDINATOR
from .playback import resume_account_scope

RUNTIME = "/tmp/gtiptv-downloads"
HISTORY = "/etc/enigma2/gtiptvplayer/downloads.json"
REMOVED = "/etc/enigma2/gtiptvplayer/downloads-removed.json"
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
    "remove_failed": N_("The entry could not be removed. Check the plugin data folder and permissions."),
}
_THREAD_LOCK = threading.RLock()
_STATE_LOCK_OWNER = threading.local()


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
    if stat.S_IMODE(info.st_mode) != 0o700:
        os.chmod(path, 0o700)


def _read(path, default=None, strict=False):
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "r", encoding="utf-8") as handle:
            value = json.load(handle)
        if strict and not isinstance(value, dict):
            raise ValueError("Invalid download state document")
        return value if isinstance(value, dict) else (default or {})
    except FileNotFoundError:
        return default or {}


def _write(path, value, sync=False):
    temporary = path + "." + uuid.uuid4().hex + ".tmp"
    try:
        fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, separators=(",", ":"))
            handle.flush()
            if sync:
                os.fsync(handle.fileno())
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


def _snapshot_document(path):
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "rb") as handle:
            return base64.b64encode(handle.read()).decode("ascii")
    except FileNotFoundError:
        return None


def _restore_document(path, value):
    # Skip unchanged documents: a failed history write must not require any
    # additional space to restore the history it never replaced.
    if _snapshot_document(path) == value:
        return
    if value is None:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        return
    data = base64.b64decode(value.encode("ascii"), validate=True)
    temporary = path + "." + uuid.uuid4().hex + ".tmp"
    try:
        fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except OSError:
            pass


def _journal_path():
    return os.path.join(RUNTIME, "commit.json")


def _recover_commit():
    journal = _journal_path()
    if not os.path.lexists(journal):
        return
    value = _read(journal, strict=True)
    if (value.get("version") != 1 or set(value) != {"version", "runtime", "history"}
            or any(value[key] is not None and not isinstance(value[key], str)
                   for key in ("runtime", "history"))):
        raise OSError("Invalid download commit journal")
    # Validate both snapshots before replacing either file. Paths are fixed;
    # the private journal cannot nominate an arbitrary recovery destination.
    for key in ("runtime", "history"):
        if value[key] is not None:
            base64.b64decode(value[key].encode("ascii"), validate=True)
    _restore_document(HISTORY, value["history"])
    _restore_document(os.path.join(RUNTIME, "state.json"), value["runtime"])
    os.unlink(journal)


def _commit_state(state):
    """Publish lifecycle changes to both documents or recover the old pair.

    The journal (including any private runtime payload) stays in private /tmp.
    A worker cannot see newly eligible runtime work until history succeeds.
    A crash or failed rollback leaves a journal that every reader recovers
    under the state lock before exposing either old or new queue state.
    """
    runtime = os.path.join(RUNTIME, "state.json")
    journal = _journal_path()
    previous = {"version": 1, "runtime": _snapshot_document(runtime),
                "history": _snapshot_document(HISTORY)}
    _write(journal, previous, sync=True)
    try:
        _history(state)
        _write(runtime, state)
        os.unlink(journal)
    except BaseException:
        # Recovery can itself fail (e.g. a full second volume). Keep the
        # journal in that case so subsequent workers/readers fail closed.
        try:
            _recover_commit()
        except Exception:
            pass
        raise


def _initial_state(read_only=False, needs_history=None):
    current_boot = boot_id()
    state = _read(os.path.join(RUNTIME, "state.json"))
    if needs_history is not None:
        needs_history.append(state.get("boot") != current_boot)
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
    return state


@contextmanager
def _state_lock(read_only=False, timeout=0.25):
    acquired = _THREAD_LOCK.acquire(timeout=timeout) if read_only else _THREAD_LOCK.acquire()
    if not acquired:
        raise TimeoutError("Download state busy")
    try:
        if getattr(_STATE_LOCK_OWNER, "exclusive", False):
            if not read_only:
                raise RuntimeError("Nested download mutation")
            # Backup's busy check can read on the same worker while its
            # restore guard owns the exclusive flock. Do not lock a second
            # file descriptor against ourselves.
            yield
            return
        _private_directory(RUNTIME)
        fd = os.open(os.path.join(RUNTIME, "state.lock"),
                     os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            if read_only:
                deadline = time.monotonic() + timeout
                while True:
                    try:
                        fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
                        break
                    except BlockingIOError:
                        if time.monotonic() >= deadline:
                            raise TimeoutError("Download state busy")
                        time.sleep(0.01)
            else:
                fcntl.flock(fd, fcntl.LOCK_EX)
                _STATE_LOCK_OWNER.exclusive = True
            if os.path.lexists(_journal_path()):
                if read_only:
                    # Upgrade only when recovery is necessary. Keep snapshot
                    # waits bounded, including when another process is busy.
                    while True:
                        try:
                            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                            break
                        except BlockingIOError:
                            if time.monotonic() >= deadline:
                                raise TimeoutError("Download state busy")
                            time.sleep(0.01)
                _recover_commit()
                if read_only:
                    fcntl.flock(fd, fcntl.LOCK_SH)
            yield
        finally:
            _STATE_LOCK_OWNER.exclusive = False
            os.close(fd)
    finally:
        _THREAD_LOCK.release()


@contextmanager
def transaction(persist=False, read_only=False, timeout=0.25):
    admission = nullcontext(True) if read_only else RESTORE_COORDINATOR.mutation()
    with admission as admitted:
        if not admitted:
            raise DownloadError("busy")
        with _state_lock(read_only=read_only, timeout=timeout):
            needs_history = []
            state = _initial_state(read_only=True, needs_history=needs_history)
            previous = deepcopy(state) if not read_only else None
            yield state
            if not read_only:
                if persist or needs_history[0]:
                    _commit_state(state)
                elif state != previous or not os.path.exists(os.path.join(RUNTIME, "state.json")):
                    _write(os.path.join(RUNTIME, "state.json"), state)


def snapshot():
    with transaction(read_only=True) as state:
        removed = _removed_paths()
        result = {key: json.loads(json.dumps(state.get(key)))
                  for key in ("jobs", "folder", "limit", "hold", "worker")}
        result["removed_paths"] = sorted(removed)
        result["jobs"] = visible_entries(result["jobs"] or [], removed)
        return result


def _path_key(path, parents=None):
    """Identify a local path across recording-folder aliases, not by title."""
    if not isinstance(path, str) or not os.path.isabs(path) or "\x00" in path:
        return ""
    parent, name = os.path.split(os.path.normpath(path))
    if parents is None:
        canonical = os.path.realpath(parent)
    else:
        if parent not in parents:
            parents[parent] = os.path.realpath(parent)
        canonical = parents[parent]
    return os.path.normcase(os.path.join(canonical, name))


def _removed_paths():
    # This independent document is never rewritten by a download worker or
    # a folder/limit change. Missing files are not pruned: removal is permanent.
    value = _read(REMOVED, {"version": 1, "paths": []}, strict=True)
    paths = value.get("paths")
    if (value.get("version") != 1 or not isinstance(paths, list) or any(not isinstance(path, str)
            or not os.path.isabs(path) or "\x00" in path for path in paths)):
        raise OSError("Invalid removed-download entries")
    return set(paths)


def visible_entries(entries, removed_paths):
    removed = set(removed_paths or ())
    if not removed:
        return list(entries)
    # Queue paths and scan folders are already canonical. Never touch a NAS
    # while filtering cached rows on the GUI thread; input aliases are saved
    # alongside canonical paths when an entry is removed.
    return [entry for entry in entries
            if os.path.normcase(os.path.normpath(str(entry.get("path") or ""))) not in removed]


def remove_entry(entry):
    """Forget an entry permanently while preserving video, partial and artwork.

    Queue ownership is checked under the worker's lock. The independent,
    atomic exclusion document also protects against stale scans, reboot and
    older workers rewriting their own download-history schema.
    """
    identity, path = str(entry.get("id", "")), str(entry.get("path", ""))
    key = _path_key(path)
    local = identity.startswith("local:")
    if not identity or not key:
        raise DownloadError("remove_failed")
    try:
        with transaction(persist=True) as state:
            job = next((row for row in state["jobs"] if row["id"] == identity), None)
            if not local and (job is None or job.get("path") != path):
                raise DownloadError("remove_failed")
            if local and os.path.realpath(os.path.dirname(path)) != os.path.realpath(state.get("folder") or "/"):
                raise DownloadError("remove_failed")
            parents = {}
            matches = [row for row in state["jobs"] if _path_key(row.get("path"), parents) == key]
            if any(row.get("status") in ACTIVE + ("deleting",) for row in matches):
                raise DownloadError("delete_active")
            removed = _removed_paths()
            removed.update((key, os.path.normcase(os.path.normpath(path))))
            _private_directory(os.path.dirname(REMOVED))
            _write(REMOVED, {"version": 1, "paths": sorted(removed)}, sync=True)
            identities = {row["id"] for row in matches}
            state["jobs"] = [row for row in state["jobs"] if row["id"] not in identities]
            for removed_id in identities:
                state["private"].pop(removed_id, None)
    except DownloadError:
        raise
    except (OSError, ValueError, TypeError):
        raise DownloadError("remove_failed")


def _restored_state(worker=None):
    restored = _read(HISTORY)
    restored.setdefault("jobs", [])
    for job in restored["jobs"]:
        job.pop("command", None)
        job.pop("delete_owner", None)
        job.pop("delete_previous", None)
        if job.get("status") not in TERMINAL:
            job.update(status="cancelled", error="reboot", speed=0, eta=0)
        try:
            parent = folder_ready(os.path.dirname(job.get("path") or ""))
            job["device"] = os.stat(parent).st_dev
        except (DownloadError, OSError):
            pass
    # An idle detached worker survives a settings restore. Retain only its
    # verified ownership, otherwise its next transfer would appear orphaned.
    owner = dict(worker) if alive(worker) else {}
    restored.update(boot=boot_id(), private={}, players={}, reservations={},
                    urls={}, worker=owner, worker_start=0, hold=True, shutdown=False)
    restored.setdefault("limit", DEFAULT_SPEED)
    restored.setdefault("folder", "")
    return restored


@contextmanager
def restore_guard():
    """Hold the real worker lock through restore and runtime invalidation.

    Yield an idempotent publication callback for the backup commit's rollback
    region. Recovery runs before the archive may replace history. If the
    caller rolls its files back after publication, refresh from that repaired
    history before releasing the worker lock.
    """
    with _state_lock():
        state = _initial_state(read_only=True)
        if any(job.get("status") in ACTIVE + ("queued", "deleting")
               for job in state.get("jobs", [])):
            raise DownloadError("busy")
        worker = dict(state.get("worker") or {})
        published = False

        def publish_restored():
            nonlocal published
            if published:
                return
            restored = _restored_state(worker)
            # A single /tmp publication invalidates stale private state while
            # the backup still retains its file rollback journal.
            _write(os.path.join(RUNTIME, "state.json"), restored)
            published = True

        try:
            yield publish_restored
        except BaseException:
            if published:
                # BackupManager has rolled its archive changes back inside
                # this context. Do not release a runtime from the failed
                # restore alongside that recovered persistent history.
                _write(os.path.join(RUNTIME, "state.json"), _restored_state(worker))
            raise
        else:
            # Standalone callers may rely on publication at normal exit.
            publish_restored()


def reload_after_restore():
    """Discard stale queue payloads while keeping a verified idle worker."""
    with transaction(persist=True) as state:
        if any(job.get("status") in ACTIVE + ("queued", "deleting")
               for job in state.get("jobs", [])):
            raise DownloadError("busy")
        restored = _restored_state(state.get("worker"))
        state.clear()
        state.update(restored)


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
    # Mount/NAS probes must never extend the account reservation critical
    # section. Recheck the chosen folder under the lock before committing.
    with transaction(read_only=True) as current:
        selected_folder = current.get("folder")
    folder = folder_ready(selected_folder)
    if free_bytes(folder) < 64 * 1024 * 1024:
        raise DownloadError("space")
    device = os.stat(folder).st_dev
    poster = poster if poster and os.path.isfile(poster) else ""
    with transaction(persist=True) as state:
        if playing_in(state, scope):
            raise DownloadError("playing")
        if state.get("folder") != selected_folder:
            raise DownloadError("folder")
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
                   device=device, downloaded=0, total=0, speed=0, eta=0,
                   poster=poster, created=time.time())
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
    removed = _removed_paths()
    canonical_folder = os.path.realpath(folder)
    result = []
    with os.scandir(folder) as entries:
        for entry in entries:
            if os.path.splitext(entry.name)[1].lower() not in VIDEO_EXTENSIONS:
                continue
            if os.path.normcase(os.path.join(canonical_folder, entry.name)) in removed:
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

