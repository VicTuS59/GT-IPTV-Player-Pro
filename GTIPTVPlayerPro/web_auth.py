# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""In-memory pairing and browser-session security for the local web UI."""

from collections import OrderedDict, deque
import hashlib
import hmac
import ipaddress
import secrets
import threading
import time


PAIRING_TTL_SECONDS = 5 * 60
PAIRING_REQUEST_TTL_SECONDS = 2 * 60
SESSION_IDLE_TTL_SECONDS = 15 * 60
SESSION_ABSOLUTE_TTL_SECONDS = 60 * 60
ATTEMPT_WINDOW_SECONDS = 5 * 60
MAX_PAIRING_ATTEMPTS = 5
MAX_PENDING_REQUESTS = 4
MAX_SESSIONS = 4

_LAN_NETWORKS = tuple(
    ipaddress.ip_network(value)
    for value in (
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "127.0.0.0/8",
        "169.254.0.0/16",
        "::1/128",
        "fe80::/10",
        "fc00::/7",
    )
)


class WebAuthError(Exception):
    """One safe error code suitable for an HTTP JSON response."""

    def __init__(self, code, status=400):
        self.code = str(code or "request_failed")
        self.status = int(status)
        Exception.__init__(self, self.code)


def normalize_client_ip(value):
    value = str(value or "").strip()
    if value.startswith("::ffff:"):
        value = value[7:]
    if "%" in value:
        value = value.split("%", 1)[0]
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        return ""


def local_client(value):
    """Accept only explicit LAN, loopback and link-local address ranges."""
    value = normalize_client_ip(value)
    if not value:
        return False
    address = ipaddress.ip_address(value)
    return any(address in network for network in _LAN_NETWORKS)


def _agent_digest(value):
    value = str(value or "")[:1024]
    return hashlib.sha256(value.encode("utf-8", "replace")).hexdigest()


def _now(clock):
    try:
        return float(clock())
    except Exception:
        return time.monotonic()


class PairingManager(object):
    """Authorize a browser only after a one-time code and TV approval.

    Secrets live only in memory.  A receiver restart, explicit stop or session
    timeout invalidates every browser without touching account files.
    """

    def __init__(self, clock=None):
        self.clock = clock or time.monotonic
        self._lock = threading.RLock()
        self._active = False
        self._code = ""
        self._code_expires = 0.0
        self._pending = OrderedDict()
        self._sessions = OrderedDict()
        self._attempts = {}

    def _cleanup_locked(self, now=None):
        now = _now(self.clock) if now is None else float(now)
        for client_ip, attempts in list(self._attempts.items()):
            while attempts and now - attempts[0] > ATTEMPT_WINDOW_SECONDS:
                attempts.popleft()
            if not attempts:
                self._attempts.pop(client_ip, None)

        for request_id, request in list(self._pending.items()):
            if now >= request["expires"]:
                self._pending.pop(request_id, None)

        for token, session in list(self._sessions.items()):
            if (
                now >= session["absolute_expires"]
                or now - session["last_seen"] >= SESSION_IDLE_TTL_SECONDS
            ):
                self._sessions.pop(token, None)

        if self._active and now >= self._code_expires and not self._sessions:
            self._active = False
            self._code = ""
            self._pending.clear()
        return now

    def activate(self):
        with self._lock:
            now = self._cleanup_locked()
            self._active = True
            # Six digits remain easy to type with a phone while the request
            # limit and TV confirmation prevent online guessing.
            self._code = "{:06d}".format(secrets.randbelow(1000000))
            self._code_expires = now + PAIRING_TTL_SECONDS
            self._pending.clear()
            return self.public_state()

    def renew_code(self):
        return self.activate()

    def deactivate(self):
        with self._lock:
            self._active = False
            self._code = ""
            self._code_expires = 0.0
            self._pending.clear()
            self._sessions.clear()
            self._attempts.clear()

    def public_state(self):
        with self._lock:
            now = self._cleanup_locked()
            pending = self._oldest_pending_locked()
            return {
                "active": bool(self._active),
                "code": self._code if self._active else "",
                "code_expires_in": max(0, int(self._code_expires - now)),
                "pending": dict(pending) if pending else None,
                "session_count": len(self._sessions),
            }

    def _oldest_pending_locked(self):
        for request in self._pending.values():
            if request.get("status") == "pending":
                return {
                    "id": request["id"],
                    "client_ip": request["client_ip"],
                    "device": request["device"],
                    "requested_at": request["requested_at"],
                }
        return None

    def pending_request(self):
        with self._lock:
            self._cleanup_locked()
            pending = self._oldest_pending_locked()
            return dict(pending) if pending else None

    def submit_code(self, code, client_ip, user_agent=""):
        client_ip = normalize_client_ip(client_ip)
        if not local_client(client_ip):
            raise WebAuthError("local_network_required", 403)
        supplied = str(code or "").strip()
        with self._lock:
            now = self._cleanup_locked()
            if not self._active or not self._code:
                raise WebAuthError("web_interface_disabled", 403)
            attempts = self._attempts.setdefault(client_ip, deque())
            while attempts and now - attempts[0] > ATTEMPT_WINDOW_SECONDS:
                attempts.popleft()
            if len(attempts) >= MAX_PAIRING_ATTEMPTS:
                raise WebAuthError("too_many_attempts", 429)
            attempts.append(now)
            if now >= self._code_expires or not hmac.compare_digest(
                supplied.encode("utf-8", "ignore"),
                self._code.encode("ascii"),
            ):
                raise WebAuthError("invalid_pairing_code", 401)

            agent_hash = _agent_digest(user_agent)
            for request in self._pending.values():
                if (
                    request["client_ip"] == client_ip
                    and request["agent_hash"] == agent_hash
                    and request["status"] == "pending"
                ):
                    return request["id"]

            while len(self._pending) >= MAX_PENDING_REQUESTS:
                self._pending.popitem(last=False)
            request_id = secrets.token_urlsafe(18)
            device = str(user_agent or "Browser").split(" ", 1)[0][:48] or "Browser"
            self._pending[request_id] = {
                "id": request_id,
                "client_ip": client_ip,
                "agent_hash": agent_hash,
                "device": device,
                "requested_at": int(time.time()),
                "expires": now + PAIRING_REQUEST_TTL_SECONDS,
                "status": "pending",
            }
            return request_id

    def approve(self, request_id=None):
        with self._lock:
            now = self._cleanup_locked()
            if not self._active:
                raise WebAuthError("web_interface_disabled", 403)
            request = None
            if request_id:
                request = self._pending.get(str(request_id))
            else:
                pending = self._oldest_pending_locked()
                request = self._pending.get(pending["id"]) if pending else None
            if request is None or request.get("status") != "pending":
                raise WebAuthError("pairing_request_missing", 404)

            while len(self._sessions) >= MAX_SESSIONS:
                self._sessions.popitem(last=False)
            token = secrets.token_urlsafe(36)
            csrf = secrets.token_urlsafe(24)
            session = {
                "token": token,
                "csrf": csrf,
                "client_ip": request["client_ip"],
                "agent_hash": request["agent_hash"],
                "created": now,
                "last_seen": now,
                "absolute_expires": now + SESSION_ABSOLUTE_TTL_SECONDS,
            }
            self._sessions[token] = session
            request["status"] = "approved"
            request["token"] = token
            request["csrf"] = csrf
            request["expires"] = now + 30
            # A code authorizes only one approval generation.  A second phone
            # requires an explicit new code on the receiver.
            self._code = ""
            self._code_expires = now + SESSION_IDLE_TTL_SECONDS
            return {"client_ip": request["client_ip"], "device": request["device"]}

    def deny(self, request_id=None):
        with self._lock:
            self._cleanup_locked()
            if request_id:
                request = self._pending.get(str(request_id))
            else:
                pending = self._oldest_pending_locked()
                request = self._pending.get(pending["id"]) if pending else None
            if request is None:
                return False
            request["status"] = "denied"
            request["expires"] = _now(self.clock) + 20
            return True

    def poll(self, request_id, client_ip, user_agent=""):
        client_ip = normalize_client_ip(client_ip)
        with self._lock:
            now = self._cleanup_locked()
            request = self._pending.get(str(request_id or ""))
            if request is None:
                raise WebAuthError("pairing_request_missing", 404)
            if (
                request["client_ip"] != client_ip
                or request["agent_hash"] != _agent_digest(user_agent)
            ):
                raise WebAuthError("pairing_request_mismatch", 403)
            status = request.get("status")
            if status == "denied":
                self._pending.pop(request["id"], None)
                return {"status": "denied"}
            if status != "approved":
                return {
                    "status": "pending",
                    "expires_in": max(0, int(request["expires"] - now)),
                }
            token = request.pop("token")
            csrf = request.pop("csrf")
            self._pending.pop(request["id"], None)
            session = self._sessions.get(token)
            if session is None:
                raise WebAuthError("session_expired", 401)
            return {
                "status": "approved",
                "token": token,
                "csrf": csrf,
                "expires_in": self._session_expires_in_locked(session, now),
            }

    @staticmethod
    def _session_expires_in_locked(session, now):
        idle = SESSION_IDLE_TTL_SECONDS - (now - session["last_seen"])
        absolute = session["absolute_expires"] - now
        return max(0, int(min(idle, absolute)))

    def authenticate(self, token, client_ip, user_agent="", touch=True):
        token = str(token or "")
        client_ip = normalize_client_ip(client_ip)
        with self._lock:
            now = self._cleanup_locked()
            session = self._sessions.get(token)
            if session is None:
                raise WebAuthError("authentication_required", 401)
            if (
                session["client_ip"] != client_ip
                or session["agent_hash"] != _agent_digest(user_agent)
            ):
                self._sessions.pop(token, None)
                raise WebAuthError("session_mismatch", 401)
            if touch:
                session["last_seen"] = now
                self._sessions.move_to_end(token)
            return {
                "token": token,
                "csrf": session["csrf"],
                "expires_in": self._session_expires_in_locked(session, now),
            }

    def validate_csrf(self, session, supplied):
        expected = str((session or {}).get("csrf") or "")
        supplied = str(supplied or "")
        if not expected or not hmac.compare_digest(
            expected.encode("ascii", "ignore"),
            supplied.encode("ascii", "ignore"),
        ):
            raise WebAuthError("csrf_failed", 403)
        return True

    def logout(self, token):
        with self._lock:
            self._sessions.pop(str(token or ""), None)
            self._cleanup_locked()

    def has_live_access(self):
        with self._lock:
            self._cleanup_locked()
            return bool(self._active or self._sessions)

