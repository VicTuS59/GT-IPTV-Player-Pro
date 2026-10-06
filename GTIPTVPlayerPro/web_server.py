# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Temporary LAN-only HTTP bridge for the GT IPTV Player Pro web UI."""

from http.cookies import SimpleCookie
try:
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
except ImportError:  # An image may provide OpenWebif without python3-netserver.
    BaseHTTPRequestHandler = object
    ThreadingHTTPServer = None
import fcntl
import json
import os
import re
import socket
import struct
import threading
import time
from urllib.parse import parse_qs, unquote, urlsplit
from urllib.request import Request, build_opener, HTTPSHandler, ProxyHandler

from . import PLUGIN_VERSION
from .paths import plugin_path
from .web_api import GTWebService, WebServiceError
from .web_auth import PairingManager, WebAuthError, local_client
from .web_i18n import web_catalog
from .web_remote import WEB_REMOTE
from .web_subtitles import WEB_SUBTITLES, SubtitleError
from .web_audio import WEB_AUDIO, AudioError
from .web_media import WEB_MEDIA
from .web_tmdb import WEB_TMDB
from .web_youtube import WEB_YOUTUBE


WEB_PORT = 9999
MAX_REQUEST_BODY = 64 * 1024
SESSION_COOKIE = "gt_session"
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/assets/app.css": ("app.css", "text/css; charset=utf-8"),
    "/assets/app.js": ("app.js", "application/javascript; charset=utf-8"),
    "/assets/logo.svg": ("../plugin.svg", "image/svg+xml"),
    "/favicon.svg": ("../plugin.svg", "image/svg+xml"),
}


def _installed_plugin_version():
    """Read the installed version while the API reports the running version.

    An IPK update can replace the web files before Enigma2 reloads Python.
    Reading the package metadata from disk keeps that real mismatch visible
    without maintaining a separate, easily forgotten web revision number.
    """
    try:
        with open(plugin_path("__init__.py"), "r", encoding="utf-8") as handle:
            source = handle.read(4097)
        if len(source) <= 4096:
            match = re.search(
                r"(?m)^[ \t]*PLUGIN_VERSION[ \t]*=[ \t]*['\"]"
                r"([A-Za-z0-9.+_-]{1,64})['\"][ \t]*(?:#.*)?$",
                source,
            )
            if match:
                return match.group(1)
    except (OSError, UnicodeError):
        pass
    return PLUGIN_VERSION


class WebResponse(object):
    def __init__(self, status=200, body=b"", content_type="application/octet-stream", headers=None):
        self.status = int(status)
        self.body = body if isinstance(body, bytes) else str(body).encode("utf-8")
        self.content_type = str(content_type)
        self.headers = list(headers or ())


def _json_response(payload, status=200, headers=None):
    body = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return WebResponse(status, body, "application/json; charset=utf-8", headers)


def _header(headers, name, default=""):
    name = str(name).lower()
    for key, value in (headers or {}).items():
        key = key.decode("latin-1", "ignore") if isinstance(key, bytes) else str(key)
        if key.lower() == name:
            return value.decode("latin-1", "ignore") if isinstance(value, bytes) else str(value)
    return default


def _cookie_token(headers):
    value = _header(headers, "cookie")
    if not value:
        return ""
    try:
        cookie = SimpleCookie()
        cookie.load(value)
        item = cookie.get(SESSION_COOKIE)
        return item.value if item is not None else ""
    except Exception:
        return ""


def _session_cookie(token, secure=False, clear=False, path="/"):
    cookie_path = str(path or "/")
    if not cookie_path.startswith("/") or ";" in cookie_path:
        cookie_path = "/"
    if cookie_path != "/":
        cookie_path = cookie_path.rstrip("/") + "/"
    value = "{}={}; Path={}; HttpOnly; SameSite=Strict".format(
        SESSION_COOKIE,
        "" if clear else str(token or ""),
        cookie_path,
    )
    if clear:
        value += "; Max-Age=0"
    if secure:
        value += "; Secure"
    return value


def _host_allowed(value):
    value = str(value or "").strip().lower()
    if not value:
        return False
    if value.startswith("["):
        host = value[1:].split("]", 1)[0]
    else:
        host = value.rsplit(":", 1)[0] if value.count(":") == 1 else value
    host = host.rstrip(".")
    if host in ("localhost", socket.gethostname().lower().rstrip(".")):
        return True
    if host.endswith(".local"):
        return True
    try:
        return local_client(host)
    except Exception:
        return False


def _parse_json(body):
    if not body:
        return {}
    if len(body) > MAX_REQUEST_BODY:
        raise WebServiceError("request_too_large", "Request is too large", 413)
    try:
        value = json.loads(body.decode("utf-8"))
    except (UnicodeError, ValueError):
        raise WebServiceError("invalid_json", "Invalid JSON", 400)
    if not isinstance(value, dict):
        raise WebServiceError("invalid_json", "Invalid JSON", 400)
    return value


class GTWebApplication(object):
    def __init__(self, pairing=None, service=None):
        self.pairing = pairing or PairingManager()
        self.service = service or GTWebService()

    def _security_headers(self, response):
        headers = [
            ("Content-Type", response.content_type),
            ("Content-Length", str(len(response.body))),
            ("X-Content-Type-Options", "nosniff"),
            ("X-Frame-Options", "DENY"),
            ("Referrer-Policy", "no-referrer"),
            ("Permissions-Policy", "camera=(), microphone=(), geolocation=()"),
            (
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self'; style-src 'self'; "
                "img-src 'self' data: https://i.ytimg.com; connect-src 'self'; frame-ancestors 'none'; "
                "base-uri 'none'; form-action 'self'",
            ),
        ]
        headers.extend(response.headers)
        response.headers = headers
        return response

    @staticmethod
    def _error(error):
        if isinstance(error, (WebAuthError, WebServiceError, SubtitleError, AudioError)):
            return _json_response(
                {"ok": False, "error": error.code, "message": getattr(error, "message", error.code)},
                error.status,
            )
        return _json_response(
            {"ok": False, "error": "request_failed", "message": "Request failed"},
            500,
        )

    def _static(self, path):
        entry = STATIC_FILES.get(path)
        if entry is None:
            return None
        relative, content_type = entry
        web_root = os.path.realpath(plugin_path("web"))
        candidate = os.path.realpath(os.path.join(web_root, relative))
        plugin_root = os.path.realpath(plugin_path())
        if not (candidate == plugin_root or candidate.startswith(plugin_root + os.sep)):
            return _json_response({"ok": False, "error": "not_found"}, 404)
        try:
            status = os.lstat(candidate)
            if not os.path.isfile(candidate) or os.path.islink(candidate) or status.st_size > 2 * 1024 * 1024:
                raise OSError("unsafe static file")
            with open(candidate, "rb") as handle:
                body = handle.read(2 * 1024 * 1024 + 1)
        except OSError:
            return _json_response({"ok": False, "error": "not_found"}, 404)
        if len(body) > 2 * 1024 * 1024:
            return _json_response({"ok": False, "error": "not_found"}, 404)
        if relative == "index.html":
            body = body.replace(
                b"__GT_PLUGIN_VERSION__", _installed_plugin_version().encode("ascii")
            )
        # The web bundle is installed in place while an older Enigma2 Python
        # process can still be serving requests.  Never let a browser combine
        # a newly installed index with a cached JavaScript or stylesheet from
        # the previous revision.
        return WebResponse(
            200,
            body,
            content_type,
            [
                ("Cache-Control", "no-store, max-age=0"),
                ("Pragma", "no-cache"),
                ("Expires", "0"),
            ],
        )

    def _session(self, headers, client_ip, user_agent, touch=True):
        token = _cookie_token(headers)
        return token, self.pairing.authenticate(token, client_ip, user_agent, touch=touch)

    def handle(self, method, target, headers, body, client_ip, secure=False, prefix=""):
        method = str(method or "GET").upper()
        client_ip = str(client_ip or "")
        user_agent = _header(headers, "user-agent")[:1024]
        try:
            if not local_client(client_ip):
                raise WebAuthError("local_network_required", 403)
            if not _host_allowed(_header(headers, "host")):
                raise WebAuthError("invalid_host", 403)

            parsed = urlsplit(str(target or "/"))
            path = unquote(parsed.path or "/")
            query = parse_qs(parsed.query, keep_blank_values=True)
            if not self.pairing.has_live_access():
                raise WebAuthError("web_interface_disabled", 404)

            static_response = self._static(path) if method == "GET" else None
            if static_response is not None:
                return self._security_headers(static_response)

            if path == "/api/v1/i18n" and method == "GET":
                requested = (query.get("lang") or [""])[0]
                if not requested:
                    requested = _header(headers, "accept-language").split(",", 1)[0]
                return self._security_headers(_json_response({"ok": True, "data": web_catalog(requested)}))

            if path == "/api/v1/bootstrap" and method == "GET":
                token = _cookie_token(headers)
                authenticated = False
                expires_in = 0
                csrf = ""
                if token:
                    try:
                        session = self.pairing.authenticate(token, client_ip, user_agent)
                        authenticated = True
                        expires_in = session["expires_in"]
                        csrf = session["csrf"]
                    except WebAuthError:
                        pass
                return self._security_headers(_json_response({
                    "ok": True,
                    "data": {
                        "active": True,
                        "authenticated": authenticated,
                        "expires_in": expires_in,
                        "csrf": csrf,
                    },
                }))

            if path == "/api/v1/pair" and method == "POST":
                payload = _parse_json(body)
                request_id = self.pairing.submit_code(
                    payload.get("code"), client_ip, user_agent
                )
                return self._security_headers(_json_response({
                    "ok": True,
                    "data": {"status": "pending", "request_id": request_id},
                }, 202))

            if path == "/api/v1/pair/status" and method == "GET":
                request_id = (query.get("id") or [""])[0]
                result = self.pairing.poll(request_id, client_ip, user_agent)
                response_headers = []
                if result.get("status") == "approved":
                    token = result.pop("token")
                    response_headers.append((
                        "Set-Cookie",
                        _session_cookie(token, secure=secure, path=prefix or "/"),
                    ))
                return self._security_headers(_json_response(
                    {"ok": True, "data": result},
                    headers=response_headers,
                ))

            token, session = self._session(headers, client_ip, user_agent)
            if method not in ("GET", "HEAD"):
                self.pairing.validate_csrf(session, _header(headers, "x-gt-csrf"))

            if path == "/api/v1/session" and method == "GET":
                return self._security_headers(_json_response({"ok": True, "data": session}))
            if path == "/api/v1/logout" and method == "POST":
                self.pairing.logout(token)
                return self._security_headers(_json_response(
                    {"ok": True, "data": {"logged_out": True}},
                    headers=[(
                        "Set-Cookie",
                        _session_cookie(
                            "", secure=secure, clear=True, path=prefix or "/"
                        ),
                    )],
                ))
            if path == "/api/v1/dashboard" and method == "GET":
                data = self.service.dashboard()
            elif path == "/api/v1/remote/status" and method == "GET":
                data = WEB_REMOTE.keyboard_status()
            elif path == "/api/v1/remote/key" and method == "POST":
                data = WEB_REMOTE.press(_parse_json(body).get("key"))
            elif path == "/api/v1/remote/paste" and method == "POST":
                data = WEB_REMOTE.paste(_parse_json(body).get("text"))
            elif path == "/api/v1/automatic-test" and method == "GET":
                data = self.service.automatic_test()
            elif path == "/api/v1/automatic-test/start" and method == "POST":
                payload = _parse_json(body)
                data = self.service.start_automatic_test(payload.get("type"))
            elif path == "/api/v1/automatic-test/cancel" and method == "POST":
                data = self.service.cancel_automatic_test()
            elif path == "/api/v1/sources" and method == "GET":
                data = self.service.sources()
            elif path == "/api/v1/sources" and method == "POST":
                data = self.service.add_source(_parse_json(body))
            elif path.startswith("/api/v1/sources/"):
                parts = path.strip("/").split("/")
                if len(parts) < 4:
                    raise WebServiceError("not_found", "Not found", 404)
                source_id = parts[3]
                action = parts[4] if len(parts) > 4 else ""
                if method in ("PUT", "PATCH") and not action:
                    data = self.service.update_source(source_id, _parse_json(body))
                elif method == "DELETE" and not action:
                    data = self.service.delete_source(source_id)
                elif method == "POST" and action == "test":
                    data = self.service.test_source(source_id)
                else:
                    raise WebServiceError("not_found", "Not found", 404)
            elif path == "/api/v1/downloads" and method == "GET":
                data = self.service.downloads()
            elif path.startswith("/api/v1/downloads/") and method == "POST":
                parts = path.strip("/").split("/")
                if len(parts) != 5:
                    raise WebServiceError("not_found", "Not found", 404)
                data = self.service.download_action(parts[3], parts[4])
            elif path == "/api/v1/epg" and method == "GET":
                data = self.service.epg()
            elif path.startswith("/api/v1/epg/") and path.endswith("/refresh") and method == "POST":
                parts = path.strip("/").split("/")
                data = self.service.refresh_epg(parts[3] if len(parts) == 5 else "")
            elif path == "/api/v1/favorites" and method == "GET":
                data = self.service.favorites()
            elif path.startswith("/api/v1/favorites/") and method == "DELETE":
                parts = path.strip("/").split("/")
                data = self.service.delete_favorite(parts[3] if len(parts) == 4 else "")
            elif path == "/api/v1/settings" and method == "GET":
                data = self.service.settings()
            elif path == "/api/v1/settings/tmdb" and method == "GET":
                data = WEB_TMDB.status()
            elif path == "/api/v1/settings/tmdb" and method == "PUT":
                data = WEB_TMDB.save(_parse_json(body))
            elif path == "/api/v1/settings/tmdb/test" and method == "POST":
                data = WEB_TMDB.test()
            elif path == "/api/v1/settings/youtube" and method == "GET":
                data = WEB_YOUTUBE.settings()
            elif path == "/api/v1/settings/youtube" and method == "PUT":
                data = WEB_YOUTUBE.save(_parse_json(body))
            elif path == "/api/v1/youtube/history" and method == "GET":
                data = WEB_YOUTUBE.history()
            elif path == "/api/v1/youtube/history" and method == "DELETE":
                data = WEB_YOUTUBE.delete_history(_parse_json(body))
            elif path == "/api/v1/youtube/search" and method == "GET":
                search = WEB_YOUTUBE.search_async if (query.get("progress") or [""])[0] == "1" else WEB_YOUTUBE.search
                data = search((query.get("q") or [""])[0],
                                          (query.get("cursor") or [""])[0],
                                          (query.get("lang") or [""])[0]
                                          or _header(headers, "accept-language").split(",", 1)[0])
            elif path == "/api/v1/youtube/search-job" and method == "GET":
                data = WEB_YOUTUBE.search_status((query.get("token") or [""])[0])
            elif path == "/api/v1/youtube/search-job" and method == "DELETE":
                data = WEB_YOUTUBE.cancel_search(_parse_json(body).get("token"))
            elif path == "/api/v1/youtube/play" and method == "POST":
                payload = _parse_json(body)
                data = WEB_YOUTUBE.play(payload.get("id"), payload.get("title"), payload.get("context", ""))
            elif path == "/api/v1/youtube/status" and method == "GET":
                data = WEB_YOUTUBE.status((query.get("token") or [""])[0])
            elif path == "/api/v1/media/search" and method == "GET":
                data = WEB_MEDIA.search((query.get("source") or [""])[0],
                                        (query.get("q") or [""])[0],
                                        (query.get("kind") or ["movie"])[0])
            elif path == "/api/v1/media/episodes" and method == "GET":
                data = WEB_MEDIA.episodes((query.get("token") or [""])[0])
            elif path == "/api/v1/media/play" and method == "POST":
                data = WEB_MEDIA.play(_parse_json(body).get("token"))
            elif path == "/api/v1/media/download" and method == "POST":
                data = WEB_MEDIA.download(_parse_json(body).get("token"))
            elif path == "/api/v1/subtitles/providers" and method == "GET":
                data = WEB_SUBTITLES.providers()
            elif path.startswith("/api/v1/subtitles/providers/") and method in ("PUT", "POST"):
                parts = path.strip("/").split("/")
                if len(parts) == 5 and method == "PUT":
                    data = WEB_SUBTITLES.update_provider(parts[4], _parse_json(body))
                elif len(parts) == 6 and parts[5] == "test" and method == "POST":
                    data = WEB_SUBTITLES.test_provider(parts[4])
                else:
                    raise WebServiceError("not_found", "Not found", 404)
            elif path == "/api/v1/subtitles/current" and method == "GET":
                data = WEB_SUBTITLES.current()
            elif path == "/api/v1/subtitles/cues" and method == "GET":
                data = WEB_SUBTITLES.cues(
                    (query.get("q") or [""])[0],
                    (query.get("revision") or [""])[0],
                )
            elif path == "/api/v1/subtitles/sync" and method == "POST":
                data = WEB_SUBTITLES.sync(_parse_json(body))
            elif path == "/api/v1/subtitles/search" and method == "POST":
                data = WEB_SUBTITLES.search(_parse_json(body))
            elif path == "/api/v1/subtitles/apply" and method == "POST":
                data = WEB_SUBTITLES.apply(_parse_json(body).get("id"))
            elif path == "/api/v1/audio/current" and method == "GET":
                data = WEB_AUDIO.current()
            elif path == "/api/v1/audio/select" and method == "POST":
                data = WEB_AUDIO.select(_parse_json(body))
            else:
                raise WebServiceError("not_found", "Not found", 404)
            return self._security_headers(_json_response({"ok": True, "data": data}))
        except Exception as error:
            return self._security_headers(self._error(error))


def _lan_ipv4_addresses():
    addresses = []
    try:
        interfaces = socket.if_nameindex()
    except (AttributeError, OSError):
        interfaces = []
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    except (IOError, OSError, socket.error):
        probe = None
    if probe is not None:
        try:
            for unused_index, name in interfaces:
                del unused_index
                try:
                    request = struct.pack("256s", name.encode("utf-8")[:15])
                    packed = fcntl.ioctl(probe.fileno(), 0x8915, request)
                    address = socket.inet_ntoa(packed[20:24])
                except (IOError, OSError, struct.error):
                    continue
                if address != "127.0.0.1" and local_client(address) and address not in addresses:
                    addresses.append(address)
        finally:
            probe.close()
    # Resolving the receiver hostname may wait for DNS even after an interface
    # has supplied a usable address.  This routine runs off the GUI thread and
    # uses only numeric addresses, so a broken resolver cannot freeze Enigma2.
    if not addresses:
        try:
            route_probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        except (IOError, OSError, socket.error):
            route_probe = None
        if route_probe is None:
            return addresses
        try:
            # UDP connect selects a route without transmitting application
            # data.  The documentation-only destination is never contacted.
            route_probe.connect(("192.0.2.1", 9))
            address = route_probe.getsockname()[0]
            if address != "127.0.0.1" and local_client(address):
                addresses.append(address)
        except (IOError, OSError, socket.error):
            pass
        finally:
            route_probe.close()
    return addresses


class _StandaloneHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "GTWeb"
    sys_version = ""

    def log_message(self, unused_format, *unused_args):
        del unused_format, unused_args

    def _dispatch(self):
        length = _safe_content_length(self.headers.get("Content-Length", "0"))
        if length > MAX_REQUEST_BODY:
            response = _json_response({"ok": False, "error": "request_too_large"}, 413)
            response = self.server.runtime.application._security_headers(response)
        else:
            body = self.rfile.read(length) if length else b""
            response = self.server.runtime.application.handle(
                self.command,
                self.path,
                dict(self.headers.items()),
                body,
                self.client_address[0],
                secure=False,
            )
        self.send_response(response.status)
        for key, value in response.headers:
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(response.body)

    do_GET = _dispatch
    do_POST = _dispatch
    do_PUT = _dispatch
    do_PATCH = _dispatch
    do_DELETE = _dispatch
    do_HEAD = _dispatch


def _safe_content_length(value):
    try:
        length = int(value)
    except (TypeError, ValueError, OverflowError):
        return 0
    return max(0, length)


class _GTHTTPServer(ThreadingHTTPServer if ThreadingHTTPServer is not None else object):
    daemon_threads = True
    allow_reuse_address = True


def _probe_openwebif(scheme, port):
    """Confirm that OpenWebif serves our resource, not just that it imports."""
    handlers = [ProxyHandler({})]
    if scheme == "https":
        try:
            import ssl
            # Only the local availability probe ignores the receiver's
            # self-signed certificate.  Browser connections remain untouched.
            handlers.append(HTTPSHandler(context=ssl._create_unverified_context()))
        except (ImportError, AttributeError):
            return False
    url = "{}://127.0.0.1:{}/gtiptv/".format(scheme, port)
    try:
        opener = build_opener(*handlers)
        with opener.open(Request(url, headers={"Cache-Control": "no-store"}),
                         timeout=1.25) as response:
            return (
                response.getcode() == 200
                and b"<title>GT IPTV Player Pro</title>" in response.read(1024)
            )
    except Exception:
        # Disabled, not yet mounted, protected by OpenWebif authentication,
        # or no listener: the independent local server can still be used.
        return False


class GTWebRuntime(object):
    def __init__(self):
        self.pairing = PairingManager()
        self.application = GTWebApplication(self.pairing)
        self._lock = threading.RLock()
        self._server = None
        self._server_thread = None
        self._watchdog_thread = None
        self._watchdog_stop = threading.Event()
        self._openwebif = False
        self._openwebif_candidates = ()
        self._openwebif_scheme = "http"
        self._openwebif_port = 80
        self._mode = "idle"
        self._last_error = ""
        self._addresses = ()
        self._next_address_refresh = 0.0
        self._address_loading = False
        self._starting = False
        self._generation = 0

    def configure_openwebif(self, scheme="http", port=80, http_port=None):
        with self._lock:
            self._openwebif = True
            self._openwebif_scheme = "https" if str(scheme).lower() == "https" else "http"
            self._openwebif_port = int(port or (443 if self._openwebif_scheme == "https" else 80))
            self._openwebif_candidates = ((self._openwebif_scheme, self._openwebif_port),)
            if self._openwebif_scheme == "https" and http_port:
                self._openwebif_candidates += (("http", int(http_port)),)

    def _start_watchdog_locked(self):
        if not self._watchdog_stop.is_set() and self._watchdog_thread is not None and self._watchdog_thread.is_alive():
            return
        # A stopped watchdog may still be finishing its last iteration.
        # Give each session its own event instead of reusing a signalled one.
        self._watchdog_stop = threading.Event()
        self._watchdog_thread = threading.Thread(
            target=self._watchdog,
            args=(self._watchdog_stop,),
            name="GTWebWatchdog",
            daemon=True,
        )
        self._watchdog_thread.start()

    def _watchdog(self, stop_event):
        while not stop_event.wait(1.0):
            if self.pairing.has_live_access():
                continue
            with self._lock:
                if self._starting or stop_event is not self._watchdog_stop:
                    continue
            self._shutdown_standalone()
            with self._lock:
                if not self._starting:
                    self._mode = "idle"
            return

    def _refresh_addresses(self, generation):
        try:
            addresses = tuple(_lan_ipv4_addresses())
        except Exception:
            addresses = ()
        with self._lock:
            if generation == self._generation:
                self._addresses = addresses
                self._next_address_refresh = time.monotonic() + 15.0
                self._address_loading = False

    def _schedule_address_refresh_locked(self):
        if (self._mode == "idle" or self._starting or self._address_loading
                or time.monotonic() < self._next_address_refresh):
            return
        self._address_loading = True
        try:
            threading.Thread(target=self._refresh_addresses, args=(self._generation,),
                             name="GTWebAddresses", daemon=True).start()
        except RuntimeError:
            self._address_loading = False
            self._next_address_refresh = time.monotonic() + 15.0

    def _start_standalone_locked(self):
        if self._server is not None:
            return
        if ThreadingHTTPServer is None:
            raise RuntimeError("python3-netserver is unavailable")
        server = _GTHTTPServer(("0.0.0.0", WEB_PORT), _StandaloneHandler)
        server.runtime = self
        thread = threading.Thread(
            target=server.serve_forever,
            kwargs={"poll_interval": 0.25},
            name="GTWebServer",
            daemon=True,
        )
        self._server = server
        self._server_thread = thread
        try:
            thread.start()
        except Exception:
            self._server = None
            self._server_thread = None
            server.server_close()
            raise

    def _shutdown_standalone(self):
        with self._lock:
            server = self._server
            thread = self._server_thread
            self._server = None
            self._server_thread = None
        if server is not None:
            try:
                if thread is not None and thread.is_alive():
                    server.shutdown()
            except Exception:
                pass
            try:
                server.server_close()
            except Exception:
                pass

    def _begin_start(self):
        with self._lock:
            if self._starting:
                return None
            self._generation += 1
            self._starting = True
            self._last_error = ""
            self._address_loading = False
            return self._generation

    def _start_worker(self, generation):
        try:
            addresses = tuple(_lan_ipv4_addresses())
            if not addresses:
                raise RuntimeError("Network address could not be resolved.")
            with self._lock:
                if generation != self._generation:
                    self._starting = False
                    return None
                candidates = self._openwebif_candidates
            # Secure randomness, listener creation and the bounded loopback
            # probe all run off Enigma2's GUI/Twisted reactor thread.
            self.pairing.activate()
            chosen = None
            for scheme, port in candidates:
                if _probe_openwebif(scheme, port):
                    chosen = ("openwebif", scheme, port)
                    break
            if chosen is None:
                with self._lock:
                    if generation == self._generation:
                        self._start_standalone_locked()
                chosen = ("standalone", "http", WEB_PORT)
            with self._lock:
                canceled = generation != self._generation
                if not canceled:
                    self._mode, self._openwebif_scheme, self._openwebif_port = chosen
                    self._addresses = addresses
                    self._next_address_refresh = time.monotonic() + 15.0
                    self._starting = False
                    self._start_watchdog_locked()
            if canceled:
                self.pairing.deactivate()
                self._shutdown_standalone()
                with self._lock:
                    self._mode = "idle"
                    self._starting = False
            elif chosen[0] == "openwebif":
                self._shutdown_standalone()
            return None
        except Exception as error:
            self.pairing.deactivate()
            self._shutdown_standalone()
            with self._lock:
                self._last_error = str(error)[:160] if generation == self._generation else ""
                self._mode = "idle"
                self._starting = False
            return error

    def start(self):
        generation = self._begin_start()
        if generation is not None:
            error = self._start_worker(generation)
            if error is not None:
                raise error
        return self.status()

    def request_start(self):
        generation = self._begin_start()
        if generation is not None:
            try:
                threading.Thread(target=self._start_worker, args=(generation,),
                                 name="GTWebStart", daemon=True).start()
            except RuntimeError as error:
                with self._lock:
                    self._last_error = str(error)[:160]
                    self._starting = False

    def stop(self):
        with self._lock:
            if self._starting:
                self._generation += 1
                return
            self._generation += 1
            self._last_error = ""
        self.pairing.deactivate()
        self._watchdog_stop.set()
        try:
            self.application.service.shutdown()
        except Exception:
            pass
        self._shutdown_standalone()
        with self._lock:
            self._mode = "idle"

    def renew_code(self):
        return self.start()

    def approve_pending(self):
        return self.pairing.approve()

    def deny_pending(self):
        return self.pairing.deny()

    def urls(self):
        with self._lock:
            self._schedule_address_refresh_locked()
            if self._mode == "openwebif":
                scheme = self._openwebif_scheme
                port = self._openwebif_port
                suffix = "/gtiptv/"
            elif self._mode == "standalone":
                scheme = "http"
                port = WEB_PORT
                suffix = "/"
            else:
                return []
            addresses = self._addresses
        default = 443 if scheme == "https" else 80
        port_text = "" if port == default else ":{}".format(port)
        return [
            "{}://{}{}{}".format(scheme, address, port_text, suffix)
            for address in addresses
        ]

    def status(self):
        with self._lock:
            starting = self._starting
        # PairingManager can be waiting for the receiver's random generator.
        # Never acquire its lock from the UI while a startup is in progress.
        state = ({"active": False, "code": "", "pending": None,
                  "session_count": 0} if starting else self.pairing.public_state())
        with self._lock:
            state.update({
                "mode": self._mode,
                "urls": [] if self._starting else self.urls(),
                "error": self._last_error,
                "starting": self._starting,
                "encrypted": self._mode == "openwebif" and self._openwebif_scheme == "https",
            })
        return state


_RUNTIME = GTWebRuntime()
_OPENWEBIF_RESOURCE = None


def get_runtime():
    return _RUNTIME


def _config_value(element, fallback):
    try:
        return element.value
    except Exception:
        return fallback


def register_openwebif():
    """Register before OpenWebif builds its root tree; fail closed if absent."""
    global _OPENWEBIF_RESOURCE
    try:
        from twisted.internet import threads
        from twisted.web.resource import Resource
        from twisted.web.server import NOT_DONE_YET
        from Plugins.Extensions.WebInterface.WebChilds.Toplevel import loaded_plugins
    except Exception:
        return False

    scheme, port, http_port = "http", 80, 80
    try:
        from Components.config import config

        section = config.OpenWebif
        if not bool(_config_value(getattr(section, "enabled", None), True)):
            return False
        http_port = int(_config_value(getattr(section, "port", None), 80))
        if bool(_config_value(getattr(section, "https_enabled", None), False)):
            scheme = "https"
            port = int(_config_value(getattr(section, "https_port", None), 443))
        else:
            port = http_port
    except Exception:
        pass

    if _OPENWEBIF_RESOURCE is None:
        class GTWebOpenWebifResource(Resource):
            isLeaf = True

            @staticmethod
            def _finish(request, response, method):
                try:
                    request.setResponseCode(response.status)
                    for key, value in response.headers:
                        request.setHeader(
                            key.encode("ascii"), value.encode("utf-8")
                        )
                    if method != "HEAD" and response.body:
                        request.write(response.body)
                    request.finish()
                except Exception:
                    # The phone may disconnect while a bounded provider test
                    # is still running in the worker thread.
                    pass
                return response

            @staticmethod
            def _finish_failure(request, unused_failure, method):
                del unused_failure
                response = _RUNTIME.application._security_headers(
                    _json_response(
                        {
                            "ok": False,
                            "error": "request_failed",
                            "message": "Request failed",
                        },
                        500,
                    )
                )
                return GTWebOpenWebifResource._finish(
                    request, response, method
                )

            def render(self, request):
                method = request.method.decode("ascii", "ignore") if isinstance(request.method, bytes) else str(request.method)
                uri = request.uri.decode("utf-8", "replace") if isinstance(request.uri, bytes) else str(request.uri)
                if uri == "/gtiptv" or uri.startswith("/gtiptv?"):
                    request.setResponseCode(308)
                    request.setHeader(b"Location", b"/gtiptv/")
                    request.setHeader(b"Cache-Control", b"no-store")
                    return b""
                target = uri
                if target.startswith("/gtiptv"):
                    target = target[len("/gtiptv"):] or "/"
                headers = {}
                try:
                    for key, values in request.requestHeaders.getAllRawHeaders():
                        name = key.decode("latin-1", "ignore") if isinstance(key, bytes) else str(key)
                        headers[name] = ", ".join(
                            value.decode("latin-1", "ignore") if isinstance(value, bytes) else str(value)
                            for value in values
                        )
                except Exception:
                    pass
                try:
                    body = request.content.read(MAX_REQUEST_BODY + 1)
                except Exception:
                    body = b""
                try:
                    client_ip = request.getClientIP()
                except Exception:
                    client_ip = ""
                # Health tests and safe account-file updates are blocking
                # services.  Run every request through Twisted's worker pool so
                # those bounded operations never stall OpenWebif's reactor.
                deferred = threads.deferToThread(
                    _RUNTIME.application.handle,
                    method,
                    target,
                    headers,
                    body,
                    client_ip,
                    bool(request.isSecure()),
                    "/gtiptv",
                )
                deferred.addCallbacks(
                    lambda response: self._finish(request, response, method),
                    lambda failure: self._finish_failure(
                        request, failure, method
                    ),
                )
                return NOT_DONE_YET

        _OPENWEBIF_RESOURCE = GTWebOpenWebifResource()

    if not any(str(item[0]).strip("b'") == "gtiptv" for item in loaded_plugins):
        loaded_plugins.append(("gtiptv", _OPENWEBIF_RESOURCE, "GT IPTV Player Pro"))

    _RUNTIME.configure_openwebif(scheme, port, http_port=http_port)
    return True
