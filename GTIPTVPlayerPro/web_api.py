# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Credential-safe data adapter used by the local GT web interface."""

import datetime
import hashlib
import os
import platform
import re
import threading
import time
from urllib.parse import quote, urlsplit

from . import PLUGIN_NAME, PLUGIN_VERSION
from .downloads import (
    ACTIVE as DOWNLOAD_ACTIVE,
    TERMINAL as DOWNLOAD_TERMINAL,
    command as download_command,
    delete_entry as delete_download_entry,
    local_files,
    snapshot as download_snapshot,
)
from .dvb_epg_settings import load_dvb_epg_settings
from .favorites import FavoriteStore
from .m3u import M3USource, save_m3u_source
from .m3u_epg_settings import load_m3u_epg_bindings
from .playlist import (
    check_source_health,
    delete_account,
    delete_source,
    load_sources,
    save_account_credentials,
)
from .settings import load_player_settings
from .stalker import delete_portal_account, save_portal_account
from .weather import load_weather_settings


MAX_WEB_TEXT = 512
AUTOMATIC_TEST_TYPES = frozenset(("xtream", "stalker"))
AUTOMATIC_TEST_TIMEOUT_SECONDS = 4.0


class WebServiceError(Exception):
    def __init__(self, code, message="", status=400):
        self.code = str(code or "request_failed")
        self.message = str(message or self.code)[:MAX_WEB_TEXT]
        self.status = int(status)
        Exception.__init__(self, self.message)


def _clean(value, maximum=MAX_WEB_TEXT):
    value = str(value or "").replace("\r", " ").replace("\n", " ")
    return " ".join(value.split())[: int(maximum)]


def _safe_int(value, fallback=0):
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return int(fallback)


def _source_type(source):
    return str(getattr(source, "source_type", "xtream") or "xtream").lower()


def _source_key(source):
    source_type = _source_type(source)
    identity = getattr(source, "identity", ())
    if not isinstance(identity, (tuple, list)):
        identity = (str(identity or ""),)
    raw = "\x1f".join([source_type] + [str(value or "") for value in identity])
    return hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()[:32]


def _source_name(source):
    source_type = _source_type(source)
    if source_type == "stalker":
        return _clean(getattr(source, "host", "") or "Stalker", 100)
    return _clean(
        getattr(source, "display_name", "")
        or getattr(source, "name", "")
        or getattr(source, "host", "")
        or source_type.upper(),
        100,
    )


def _source_endpoint(source):
    value = _clean(getattr(source, "display_endpoint", ""), 240)
    # Existing model properties intentionally remove provider credentials.
    # Keep a final defensive guard in case a future source type changes them.
    lowered = value.lower()
    for marker in (
        "username=",
        "password=",
        "token=",
        "api_key=",
        "apikey=",
        "authorization=",
        "mac=",
    ):
        if marker in lowered:
            return ""
    try:
        parsed = urlsplit(value)
        if parsed.username is not None or parsed.password is not None:
            return ""
    except ValueError:
        return ""
    return value


def _redact_health_detail(value, source=None):
    """Return a bounded diagnostic without account credentials or tokens."""
    value = _clean(value, 180)
    value = re.sub(
        r"(?i)(username|password|token|api[_-]?key|mac)=([^&\s]+)",
        r"\1=••••",
        value,
    )
    if source is None:
        return value
    secrets = []
    for name in ("username", "password", "mac", "token", "access_token"):
        secret = str(getattr(source, name, "") or "")
        if secret and secret not in secrets:
            secrets.append(secret)
    for secret in secrets:
        for candidate in (secret, quote(secret, safe="")):
            if candidate:
                value = value.replace(candidate, "••••")
    return value


def _format_bytes(value):
    value = max(0, _safe_int(value))
    units = ("B", "KB", "MB", "GB", "TB")
    amount = float(value)
    unit = units[0]
    for unit in units:
        if amount < 1024.0 or unit == units[-1]:
            break
        amount /= 1024.0
    precision = 0 if unit == "B" else (1 if amount < 100 else 0)
    return ("{:0." + str(precision) + "f} {}").format(amount, unit)


def _timestamp(value):
    value = _safe_int(value)
    if value <= 0:
        return ""
    try:
        return datetime.datetime.fromtimestamp(value).strftime("%Y-%m-%d %H:%M")
    except (OSError, OverflowError, ValueError):
        return ""


def _receiver_name():
    try:
        from boxbranding import getMachineBrand, getMachineName

        brand = _clean(getMachineBrand(), 60) if callable(getMachineBrand) else ""
        name = _clean(getMachineName(), 60) if callable(getMachineName) else ""
        value = " ".join(item for item in (brand, name) if item).strip()
        if value:
            return value
    except Exception:
        pass
    return _clean(platform.node() or "Enigma2", 100)


class GTWebService(object):
    """Expose existing receiver services without returning credentials."""

    def __init__(self):
        self._health_lock = threading.RLock()
        self._health = {}
        self._automatic_test_lock = threading.RLock()
        self._automatic_test_generation = 0
        self._automatic_test_instance = os.urandom(8).hex()
        self._automatic_test_revision = 0
        self._automatic_test_cancel = threading.Event()
        self._automatic_test_thread = None
        self._automatic_test_source_ids = set()
        self._automatic_test_deleted = set()
        self._automatic_test = self._new_automatic_test_state()

    @staticmethod
    def _new_automatic_test_state():
        return {
            "state": "idle",
            "type": "",
            "total": 0,
            "completed": 0,
            "current": None,
            "results": [],
            "summary": {"green": 0, "yellow": 0, "red": 0, "neutral": 0},
            "started_at": 0,
            "finished_at": 0,
        }

    def _sources(self):
        result = load_sources()
        return list(result.accounts), str(result.error or "")

    def _source_map(self):
        sources, unused_error = self._sources()
        del unused_error
        return {_source_key(source): source for source in sources}

    def _find_source(self, source_id):
        source = self._source_map().get(str(source_id or ""))
        if source is None:
            raise WebServiceError("source_not_found", "Source not found", 404)
        return source

    def _health_payload(self, source_id):
        with self._health_lock:
            value = self._health.get(source_id)
            return dict(value) if value else None

    @staticmethod
    def _serialize_health(health, source=None):
        provider_status = _clean(
            getattr(health, "status", "unknown"), 24
        ).upper()
        if provider_status in ("ONLINE", "ACTIVE", "SUCCESS", "OK", "GREEN"):
            status = "green"
        elif provider_status in ("BUSY", "STALE", "WARNING", "YELLOW"):
            status = "yellow"
        elif provider_status in (
            "ERROR",
            "FAILED",
            "INVALID",
            "DISABLED",
            "OFFLINE",
            "EXPIRED",
            "BANNED",
            "BLOCKED",
            "INACTIVE",
            "RED",
        ):
            status = "red"
        else:
            status = "yellow"
        return {
            "status": status,
            "provider_status": provider_status,
            "latency_ms": max(0, _safe_int(getattr(health, "latency_ms", 0))),
            "account_status": _clean(getattr(health, "account_status", ""), 80),
            "active_connections": getattr(health, "active_connections", None),
            "max_connections": getattr(health, "max_connections", None),
            "expiry": _clean(getattr(health, "expiry", ""), 80),
            "detail": _redact_health_detail(
                getattr(health, "detail", ""), source
            ),
            "checked_at": int(time.time()),
        }

    def serialize_source(self, source):
        source_id = _source_key(source)
        source_type = _source_type(source)
        health = self._health_payload(source_id)
        payload = {
            "id": source_id,
            "type": source_type,
            "name": _source_name(source),
            "endpoint": _source_endpoint(source),
            "label": _clean(getattr(source, "source_label", ""), 80),
            "masked_secret": "••••••••",
            "health": health,
            "status": (health or {}).get("status", "neutral"),
        }
        if source_type == "m3u":
            kind = _clean(getattr(source, "kind", ""), 16)
            payload["kind"] = kind
            if kind == "file":
                # A phone needs a recognizable label, not the receiver's
                # private absolute filesystem layout.  Blank also means
                # "preserve the current path" when editing this source.
                payload["endpoint"] = _clean(
                    os.path.basename(str(getattr(source, "location", "") or "")),
                    180,
                )
                payload["location"] = ""
            else:
                payload["location"] = _source_endpoint(source)
        elif source_type == "xtream":
            payload["output_format"] = _clean(
                getattr(source, "output_format", "ts"), 12
            )
        return payload

    def sources(self):
        sources, error = self._sources()
        return {
            "items": [self.serialize_source(source) for source in sources],
            "error": "sources_unavailable" if error and not sources else "",
        }

    def test_source(self, source_id):
        source = self._find_source(source_id)
        try:
            health = check_source_health(source)
        except Exception:
            raise WebServiceError(
                "source_test_failed",
                "The source could not be checked.",
                502,
            )
        payload = self._serialize_health(health, source)
        with self._health_lock:
            self._health[str(source_id)] = payload
        return payload

    @staticmethod
    def _automatic_test_source_payload(source):
        source_id = _source_key(source)
        source_type = _source_type(source)
        attribute = "mac" if source_type == "stalker" else "username"
        secret = str(getattr(source, attribute, "") or "")
        suffix = secret[-5:] if source_type == "stalker" else secret[-2:]
        hint = "••••" + (suffix if len(secret) > len(suffix) else "")
        return {
            "id": source_id,
            "type": source_type,
            "name": _source_name(source),
            "endpoint": _source_endpoint(source),
            "account_hint": _clean(hint, 40) + " · " + source_id[:8],
        }

    def _automatic_test_snapshot_locked(self):
        value = self._automatic_test
        return {
            "instance": self._automatic_test_instance,
            "revision": self._automatic_test_revision,
            "state": value["state"],
            "type": value["type"],
            "total": value["total"],
            "completed": value["completed"],
            "current": dict(value["current"]) if value["current"] else None,
            "results": [dict(item) for item in value["results"]],
            "summary": dict(value["summary"]),
            "started_at": value["started_at"],
            "finished_at": value["finished_at"],
        }

    def automatic_test(self):
        with self._automatic_test_lock:
            return self._automatic_test_snapshot_locked()

    def start_automatic_test(self, source_type):
        source_type = str(source_type or "").strip().lower()
        if source_type not in AUTOMATIC_TEST_TYPES:
            raise WebServiceError(
                "invalid_automatic_test_type",
                "Choose Xtream Codes or Stalker / MAC.",
            )
        worker = None
        with self._automatic_test_lock:
            if self._automatic_test["state"] in ("running", "cancelling"):
                raise WebServiceError(
                    "automatic_test_running",
                    "An automatic test is already running.",
                    409,
                )
            sources, unused_error = self._sources()
            del unused_error
            selected = tuple(
                source for source in sources if _source_type(source) == source_type
            )
            self._automatic_test_source_ids = {_source_key(source) for source in selected}
            self._automatic_test_deleted = set()
            self._automatic_test_generation += 1
            generation = self._automatic_test_generation
            cancel_event = threading.Event()
            self._automatic_test_cancel = cancel_event
            now = int(time.time())
            self._automatic_test = {
                "state": "running" if selected else "empty",
                "type": source_type,
                "total": len(selected),
                "completed": 0,
                "current": None,
                "results": [],
                "summary": {
                    "green": 0,
                    "yellow": 0,
                    "red": 0,
                    "neutral": 0,
                },
                "started_at": now,
                "finished_at": 0 if selected else now,
            }
            if selected:
                worker = threading.Thread(
                    target=self._run_automatic_test,
                    args=(generation, selected, cancel_event),
                    name="GTWebAutomaticTest",
                )
                worker.daemon = True
                self._automatic_test_thread = worker
            else:
                self._automatic_test_thread = None
            self._automatic_test_revision += 1
            snapshot = self._automatic_test_snapshot_locked()
        if worker is not None:
            worker.start()
        return snapshot

    def _run_automatic_test(self, generation, sources, cancel_event):
        for source in sources:
            if cancel_event.is_set():
                break
            source_payload = self._automatic_test_source_payload(source)
            with self._automatic_test_lock:
                if generation != self._automatic_test_generation:
                    return
                if source_payload["id"] in self._automatic_test_deleted:
                    continue
                self._automatic_test["current"] = source_payload
                self._automatic_test_revision += 1
            try:
                health = check_source_health(
                    source,
                    timeout=AUTOMATIC_TEST_TIMEOUT_SECONDS,
                    cancel_event=cancel_event,
                    summary_only=True,
                )
                payload = self._serialize_health(health, source)
            except Exception:
                payload = {
                    "status": "red",
                    "provider_status": "ERROR",
                    "latency_ms": 0,
                    "account_status": "",
                    "active_connections": None,
                    "max_connections": None,
                    "expiry": "",
                    "detail": "The source could not be checked.",
                    "checked_at": int(time.time()),
                }
            if cancel_event.is_set():
                break
            result = dict(source_payload)
            result.update(payload)
            with self._automatic_test_lock:
                if generation != self._automatic_test_generation:
                    return
                if source_payload["id"] in self._automatic_test_deleted:
                    continue
                with self._health_lock:
                    self._health[source_payload["id"]] = dict(payload)
                self._automatic_test["results"].append(result)
                self._automatic_test["completed"] += 1
                self._automatic_test["summary"][payload["status"]] += 1
                self._automatic_test["current"] = None
                self._automatic_test_revision += 1
        with self._automatic_test_lock:
            if generation != self._automatic_test_generation:
                return
            self._automatic_test["state"] = (
                "cancelled" if cancel_event.is_set() else "completed"
            )
            self._automatic_test["current"] = None
            self._automatic_test["finished_at"] = int(time.time())
            self._automatic_test_thread = None
            self._automatic_test_revision += 1

    def cancel_automatic_test(self):
        with self._automatic_test_lock:
            if self._automatic_test["state"] == "running":
                self._automatic_test_cancel.set()
                self._automatic_test["state"] = "cancelling"
                self._automatic_test_revision += 1
            return self._automatic_test_snapshot_locked()

    def shutdown(self):
        with self._automatic_test_lock:
            if self._automatic_test["state"] in ("running", "cancelling"):
                self._automatic_test_cancel.set()
                self._automatic_test["state"] = "cancelling"
                self._automatic_test_revision += 1

    def add_source(self, payload):
        if not isinstance(payload, dict):
            raise WebServiceError("invalid_source", "Invalid source")
        source_type = str(payload.get("type") or "").strip().lower()
        try:
            if source_type == "xtream":
                source = save_account_credentials(
                    payload.get("server_url"),
                    payload.get("username"),
                    payload.get("password"),
                    name=payload.get("name", ""),
                    output_format=payload.get("output_format", "ts"),
                )
            elif source_type == "stalker":
                source = save_portal_account(
                    payload.get("portal_url"),
                    payload.get("mac"),
                )
            elif source_type == "m3u":
                source = save_m3u_source(
                    M3USource(
                        "",
                        payload.get("name", ""),
                        payload.get("kind", "url"),
                        payload.get("location", ""),
                    )
                )
            else:
                raise WebServiceError("invalid_source_type", "Invalid source type")
        except WebServiceError:
            raise
        except (TypeError, ValueError) as error:
            raise WebServiceError("source_save_failed", _clean(error, 180))
        except (IOError, OSError):
            raise WebServiceError(
                "source_save_failed", "The source could not be saved."
            )
        return self.serialize_source(source)

    def update_source(self, source_id, payload):
        if not isinstance(payload, dict):
            raise WebServiceError("invalid_source", "Invalid source")
        current = self._find_source(source_id)
        source_type = _source_type(current)
        try:
            if source_type == "xtream":
                server_url = payload.get("server_url") or _source_endpoint(current)
                username = payload.get("username") or current.username
                password = payload.get("password") or current.password
                updated = save_account_credentials(
                    server_url,
                    username,
                    password,
                    name=payload.get("name", current.name),
                    path=current.source_path,
                    output_format=payload.get(
                        "output_format", current.output_format
                    ),
                )
                if updated.identity != current.identity:
                    delete_account(current, paths=(current.source_path,))
            elif source_type == "stalker":
                portal_url = payload.get("portal_url") or current.portal_url
                mac = payload.get("mac") or current.mac
                if portal_url == current.portal_url and mac == current.mac:
                    updated = current
                else:
                    updated = save_portal_account(
                        portal_url,
                        mac,
                        path=current.source_path,
                    )
                    delete_portal_account(current, path=current.source_path)
            elif source_type == "m3u":
                updated = save_m3u_source(
                    M3USource(
                        current.source_id,
                        payload.get("name", current.name),
                        payload.get("kind", current.kind),
                        payload.get("location") or current.location,
                    )
                )
            else:
                raise WebServiceError("invalid_source_type", "Invalid source type")
        except WebServiceError:
            raise
        except (TypeError, ValueError) as error:
            raise WebServiceError("source_save_failed", _clean(error, 180))
        except (IOError, OSError):
            raise WebServiceError(
                "source_save_failed", "The source could not be saved."
            )
        with self._health_lock:
            self._health.pop(str(source_id), None)
        return self.serialize_source(updated)

    def delete_source(self, source_id):
        # Serialize the saved-source mutation with test selection and commit.
        # An in-flight health request must never restore a deleted account.
        source_id = str(source_id or "")
        with self._automatic_test_lock:
            source = self._find_source(source_id)
            try:
                if _source_type(source) == "xtream":
                    from .playlist_files import selected_playlist_paths
                    paths = list(selected_playlist_paths("xtream"))
                    source_path = str(getattr(source, "source_path", "") or "")
                    if source_path and source_path not in paths:
                        paths.append(source_path)
                    delete_source(source, paths=paths or None)
                else:
                    delete_source(source)
            except (IOError, OSError, TypeError, ValueError):
                raise WebServiceError(
                    "source_delete_failed", "The source could not be deleted."
                )
            if (source_id in self._automatic_test_source_ids
                    and source_id not in self._automatic_test_deleted):
                self._automatic_test_deleted.add(source_id)
                value = self._automatic_test
                value["results"] = [item for item in value["results"]
                                    if item["id"] != source_id]
                value["total"] = max(0, value["total"] - 1)
                value["completed"] = len(value["results"])
                value["summary"] = {"green": 0, "yellow": 0, "red": 0, "neutral": 0}
                for item in value["results"]:
                    status = item.get("status", "neutral")
                    if status not in value["summary"]:
                        status = "neutral"
                    value["summary"][status] += 1
                if value["current"] and value["current"]["id"] == source_id:
                    value["current"] = None
            with self._health_lock:
                self._health.pop(source_id, None)
            self._automatic_test_revision += 1
            snapshot = self._automatic_test_snapshot_locked()
        return {"deleted": True, "automatic_test": snapshot}

    @staticmethod
    def _download_entry(job):
        total = max(0, _safe_int(job.get("total")))
        downloaded = max(0, _safe_int(job.get("downloaded")))
        percent = int(min(100, (downloaded * 100.0 / total))) if total else 0
        status = _clean(job.get("status"), 24).lower()
        return {
            "id": _clean(job.get("id"), 96),
            "name": _clean(job.get("name") or "Video", 180),
            "status": status,
            "source": _clean(job.get("source"), 24),
            "downloaded": downloaded,
            "total": total,
            "downloaded_text": _format_bytes(downloaded),
            "total_text": _format_bytes(total) if total else "",
            "percent": percent,
            "speed": max(0, _safe_int(job.get("speed"))),
            "speed_text": "{}/s".format(_format_bytes(job.get("speed", 0))),
            "eta": max(0, _safe_int(job.get("eta"))),
            "created": _timestamp(job.get("created")),
            "can_pause": status in DOWNLOAD_ACTIVE,
            "can_resume": status in ("paused", "error"),
            "can_cancel": status not in DOWNLOAD_TERMINAL + ("local", "deleting"),
            "can_delete": status in DOWNLOAD_TERMINAL + ("local", "error"),
        }

    def downloads(self):
        try:
            state = download_snapshot()
        except Exception:
            state = {"jobs": [], "folder": "", "limit": 0, "hold": True}
        jobs = list(state.get("jobs") or [])
        folder = str(state.get("folder") or "")
        try:
            known_paths = {str(job.get("path") or "") for job in jobs}
            jobs.extend(
                item for item in local_files(folder)
                if str(item.get("path") or "") not in known_paths
            )
        except (IOError, OSError, TypeError, ValueError):
            pass
        return {
            "items": [self._download_entry(job) for job in jobs],
            "folder": os.path.basename(folder.rstrip("/")) if folder else "",
            "limit": max(0, _safe_int(state.get("limit"))),
            "hold": bool(state.get("hold")),
        }

    def download_action(self, identity, action):
        identity = str(identity or "")
        action = str(action or "").lower()
        if action not in ("pause", "resume", "cancel", "delete"):
            raise WebServiceError("invalid_download_action", "Invalid action")
        state = download_snapshot()
        entries = list(state.get("jobs") or [])
        if action == "delete":
            try:
                entries.extend(local_files(state.get("folder") or ""))
            except (IOError, OSError, TypeError, ValueError):
                pass
        entry = next((item for item in entries if item.get("id") == identity), None)
        if entry is None:
            raise WebServiceError("download_not_found", "Download not found", 404)
        try:
            if action == "delete":
                delete_download_entry(entry)
            else:
                download_command(identity, action)
        except Exception:
            raise WebServiceError("download_action_failed", "Download action failed")
        return {"updated": True}

    def epg(self):
        # The scheduler imports Enigma2's native timer.  Keep that dependency
        # lazy so the web adapter remains testable on build hosts.
        try:
            from .m3u_epg_scheduler import get_refresh_state
        except Exception:
            get_refresh_state = lambda unused_source_id: {}
        try:
            dvb = load_dvb_epg_settings()
            dvb_payload = {
                "enabled": bool(dvb.enabled),
                "days": _safe_int(dvb.epg_days, 3),
                "last_success": _timestamp(dvb.last_success_utc),
                "mapped": max(0, _safe_int(dvb.last_mapping_count)),
                "events": max(0, _safe_int(dvb.last_event_count)),
            }
        except Exception:
            dvb_payload = {"enabled": False, "days": 3, "last_success": "", "mapped": 0, "events": 0}
        bindings = []
        try:
            for binding in load_m3u_epg_bindings():
                refresh = get_refresh_state(binding.source_id) or {}
                bindings.append({
                    "source_id": binding.source_id,
                    "enabled": bool(binding.enabled),
                    "kind": _clean(binding.kind, 16),
                    "endpoint": _clean(
                        os.path.basename(binding.location)
                        if binding.kind == "file"
                        else binding.display_endpoint,
                        180,
                    ),
                    "last_success": _timestamp(binding.last_success_utc),
                    "channels": max(0, _safe_int(binding.last_channel_count)),
                    "events": max(0, _safe_int(binding.last_event_count)),
                    "error": _clean(binding.last_error_code, 80),
                    "refresh": dict(refresh) if isinstance(refresh, dict) else {},
                })
        except Exception:
            bindings = []
        return {"dvb": dvb_payload, "m3u": bindings}

    def refresh_epg(self, source_id):
        try:
            from .m3u_epg_scheduler import request_refresh
        except Exception:
            request_refresh = lambda unused_source_id: False
        source_id = str(source_id or "").strip()
        if not source_id or not request_refresh(source_id):
            raise WebServiceError(
                "epg_refresh_unavailable",
                "EPG refresh is unavailable.",
                409,
            )
        return {"queued": True}

    def favorites(self):
        try:
            entries = FavoriteStore().list_all_entries()
        except Exception:
            entries = []
        items = []
        for entry in entries:
            item = entry.get("item") or {}
            items.append({
                "key": _clean(entry.get("key"), 80),
                "name": _clean(item.get("name"), 180),
                "content_type": _clean(item.get("content_type"), 24),
                "rating": _clean(item.get("rating"), 24),
                "year": _clean(item.get("year"), 12),
                "updated": _timestamp(entry.get("updated_at")),
            })
        return {"items": items}

    def delete_favorite(self, key):
        try:
            removed = FavoriteStore().remove(str(key or ""))
        except Exception:
            removed = False
        if not removed:
            raise WebServiceError("favorite_not_found", "Favorite not found", 404)
        return {"deleted": True}

    def settings(self):
        try:
            player = load_player_settings()
        except Exception:
            player = None
        try:
            weather = load_weather_settings()
        except Exception:
            weather = None
        return {
            "version": PLUGIN_VERSION,
            "player": {
                "live_service_type": getattr(player, "live_service_type", 4097),
                "movie_service_type": getattr(player, "movie_service_type", 5002),
                "series_service_type": getattr(player, "series_service_type", 5002),
                "metadata_enabled": bool(getattr(player, "metadata_enabled", True)),
                "tmdb_configured": bool(getattr(player, "tmdb_api_key", "")),
                "cinematic_view": bool(getattr(player, "cinematic_view", True)),
                "text_size": _clean(getattr(player, "ui_text_size", "standard"), 24),
            },
            "weather": {
                "enabled": bool(getattr(weather, "enabled", False)),
                "city": _clean(getattr(weather, "location_display_name", ""), 120),
                "unit": _clean(getattr(weather, "unit", "C"), 4),
            },
        }

    def dashboard(self):
        source_payload = self.sources()
        download_payload = self.downloads()
        statuses = [item.get("status") for item in source_payload["items"]]
        tested = [status for status in statuses if status != "neutral"]
        if tested and all(status == "green" for status in tested):
            health = "green"
        elif any(status == "red" for status in tested):
            health = "red"
        elif tested:
            health = "yellow"
        else:
            health = "neutral"

        folder = ""
        try:
            state = download_snapshot()
            folder = str(state.get("folder") or "")
        except Exception:
            pass
        storage_path = folder if folder and os.path.isdir(folder) else "/media/hdd"
        total = free = 0
        try:
            info = os.statvfs(storage_path)
            total = info.f_blocks * info.f_frsize
            free = info.f_bavail * info.f_frsize
        except OSError:
            pass
        active_count = sum(
            1 for item in download_payload["items"]
            if item.get("status") in DOWNLOAD_ACTIVE + ("queued", "paused")
        )
        return {
            "plugin": {"name": PLUGIN_NAME, "version": PLUGIN_VERSION},
            "receiver": {"name": _receiver_name(), "online": True},
            "health": {"status": health, "tested": len(tested), "total": len(statuses)},
            "downloads": {"active": active_count},
            "storage": {
                "path": os.path.basename(storage_path.rstrip("/")) or storage_path,
                "total": total,
                "free": free,
                "free_text": _format_bytes(free),
            },
            "sources": source_payload,
            "download_items": download_payload["items"][:6],
            "server_time": int(time.time()),
        }
