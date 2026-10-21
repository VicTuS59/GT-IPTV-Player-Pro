# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later

import http.client
import errno
import ipaddress
import socket
import ssl
import threading
import time
from collections import OrderedDict
from urllib.parse import urljoin, urlsplit, urlunsplit

from .i18n import N_, _


MAX_PUBLIC_URL_LENGTH = 2048
MAX_REDIRECTS = 3
REDIRECT_STATUSES = (301, 302, 303, 307, 308)
PROVIDER_IMAGE_HEADER_NAMES = (
    "authorization",
    "cookie",
    "referer",
    "x-user-agent",
)
DNS_CACHE_TTL_SECONDS = 30
DNS_CACHE_MAX_ENTRIES = 64
DNS_LOOKUP_WORKERS = 4

_DNS_CACHE = OrderedDict()
_DNS_CACHE_LOCK = threading.Lock()
_DNS_LOOKUP_SLOTS = threading.BoundedSemaphore(DNS_LOOKUP_WORKERS)
_SSL_CONTEXT = [None]
_SSL_CONTEXT_LOCK = threading.Lock()


class NetworkSecurityError(RuntimeError):
    pass


def _check_cancel(cancel_event):
    if cancel_event is not None and cancel_event.is_set():
        raise InterruptedError("Artwork request cancelled")


class _TransferCancellation:
    """One process watcher interrupts blocked header/body/TLS socket reads."""
    def __init__(self):
        self.condition = threading.Condition()
        self.transfers = {}
        self.thread = None

    def add(self, connection, cancel_event, deadline):
        ticket = object()
        with self.condition:
            self.transfers[ticket] = [connection, None, cancel_event, deadline]
            if self.thread is None:
                self.thread = threading.Thread(target=self._run, name="GT-ImageCancel", daemon=True)
                try:
                    self.thread.start()
                except Exception:
                    self.thread = None
                    self.transfers.pop(ticket, None)
                    raise
            self.condition.notify_all()
        return ticket

    def response(self, ticket, response):
        with self.condition:
            if ticket in self.transfers:
                self.transfers[ticket][1] = response

    def remove(self, ticket):
        with self.condition:
            self.transfers.pop(ticket, None)

    def _run(self):
        while True:
            with self.condition:
                while not self.transfers:
                    self.condition.wait()
                # Hold the memory lock while shutting down so a completed
                # transfer cannot hand a closed/reused descriptor to us.
                for connection, response, event, deadline in self.transfers.values():
                    if not event.is_set() and time.monotonic() < deadline:
                        continue
                    sock = getattr(connection, "sock", None)
                    if sock is None and response is not None:
                        raw = getattr(getattr(response, "fp", None), "raw", None)
                        sock = getattr(raw, "_sock", None)
                    if sock is not None:
                        try:
                            sock.shutdown(socket.SHUT_RDWR)
                        except (OSError, ValueError):
                            pass
                self.condition.wait(0.02)


_TRANSFER_CANCELLATION = _TransferCancellation()


def _cancellable_connect(address, port, timeout, cancel_event):
    # Address has already passed public-IP validation. Never resolve it again.
    family = socket.AF_INET6 if ":" in address else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_STREAM)
    deadline = time.monotonic() + timeout
    try:
        sock.setblocking(False)
        result = sock.connect_ex((address, port))
        if result not in (0, errno.EINPROGRESS, errno.EWOULDBLOCK, errno.EALREADY):
            raise OSError(result, "Image connection failed")
        while result:
            _check_cancel(cancel_event)
            remaining = _remaining_timeout(deadline, timeout)
            error = sock.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
            if error:
                raise OSError(error, "Image connection failed")
            try:
                sock.getpeername()
                break
            except OSError as pending:
                if pending.errno not in (errno.ENOTCONN, errno.EINVAL):
                    raise
            # Polling the pinned nonblocking socket avoids adding a receiver
            # package dependency solely for select/selectors.
            cancel_event.wait(min(0.02, remaining))
        _check_cancel(cancel_event)
        sock.settimeout(_remaining_timeout(deadline, timeout))
        return sock
    except Exception:
        sock.close()
        raise


def _provider_image_headers(value):
    if not value:
        return {}
    if not isinstance(value, dict):
        raise NetworkSecurityError(N_("Image request headers are invalid."))
    allowed = set(PROVIDER_IMAGE_HEADER_NAMES)
    result = {}
    for name, item in value.items():
        name = str(name or "").strip()
        lowered = name.lower()
        item = str(item or "").strip()
        if lowered not in allowed or not item or len(item) > 4096:
            raise NetworkSecurityError(N_("Image request headers are invalid."))
        if any(ord(character) < 32 or ord(character) == 127 for character in item):
            raise NetworkSecurityError(N_("Image request headers are invalid."))
        result[name] = item
    return result


def _public_ip(value):
    try:
        address = ipaddress.ip_address(str(value).split("%", 1)[0])
    except ValueError as exc:
        raise NetworkSecurityError(N_("Network address could not be resolved.")) from exc
    if not address.is_global:
        raise NetworkSecurityError(
            N_("Image requests to local, private or reserved network addresses are prohibited.")
        )
    return str(address)


def validate_public_url_syntax(value):
    text = str(value or "").strip()
    if (
        not text
        or len(text) > MAX_PUBLIC_URL_LENGTH
        or any(ord(character) < 32 for character in text)
    ):
        raise NetworkSecurityError(N_("Image URL is invalid."))
    try:
        parsed = urlsplit(text)
        port = parsed.port
    except (TypeError, ValueError) as exc:
        raise NetworkSecurityError(N_("Image URL is invalid.")) from exc
    scheme = str(parsed.scheme or "").lower()
    hostname = parsed.hostname
    if (
        scheme not in ("http", "https")
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise NetworkSecurityError(N_("Image URL is not safe."))
    selected_port = port or (443 if scheme == "https" else 80)
    if selected_port <= 0 or selected_port > 65535:
        raise NetworkSecurityError(N_("Image URL port is invalid."))
    try:
        ascii_hostname = hostname.encode("idna").decode("ascii").lower()
    except (UnicodeError, ValueError) as exc:
        raise NetworkSecurityError(N_("Image host name is invalid.")) from exc
    if not ascii_hostname or len(ascii_hostname) > 253:
        raise NetworkSecurityError(N_("Image host name is invalid."))
    try:
        ipaddress.ip_address(ascii_hostname.split("%", 1)[0])
    except ValueError:
        pass
    else:
        _public_ip(ascii_hostname)
    return {
        "url": urlunsplit(
            (
                scheme,
                parsed.netloc,
                parsed.path or "/",
                parsed.query,
                "",
            )
        ),
        "scheme": scheme,
        "hostname": ascii_hostname,
        "port": selected_port,
        "path": urlunsplit(("", "", parsed.path or "/", parsed.query, "")),
    }


def _remaining_timeout(deadline, fallback):
    if deadline is None:
        return float(fallback)
    remaining = float(deadline) - time.monotonic()
    if remaining <= 0:
        error = NetworkSecurityError(N_("Image download timed out."))
        error.retryable = True
        raise error
    return max(0.05, min(float(fallback), remaining))


def resolve_public_addresses(hostname, port, resolver=None, deadline=None, cancel_event=None):
    _check_cancel(cancel_event)
    use_cache = resolver is None
    cache_key = (str(hostname).lower(), int(port))
    now = time.monotonic()
    if use_cache:
        with _DNS_CACHE_LOCK:
            cached = _DNS_CACHE.get(cache_key)
            if cached is not None and now < cached[0]:
                _DNS_CACHE.move_to_end(cache_key)
                return tuple(cached[1])
            _DNS_CACHE.pop(cache_key, None)
    resolver = resolver or socket.getaddrinfo

    def lookup():
        return resolver(
            hostname,
            int(port),
            socket.AF_UNSPEC,
            socket.SOCK_STREAM,
        )

    try:
        if use_cache and deadline is not None:
            if not _DNS_LOOKUP_SLOTS.acquire(blocking=False):
                error = NetworkSecurityError(N_("Image host name resolution timed out."))
                error.retryable = True
                raise error
            completed = threading.Event()
            result = {"answers": None, "error": None}

            def run_lookup():
                try:
                    result["answers"] = lookup()
                except Exception as error:  # propagated on the caller thread
                    result["error"] = error
                finally:
                    completed.set()
                    _DNS_LOOKUP_SLOTS.release()

            worker = threading.Thread(target=run_lookup)
            worker.daemon = True
            try:
                worker.start()
            except Exception:
                _DNS_LOOKUP_SLOTS.release()
                raise
            while not completed.wait(min(0.02, _remaining_timeout(deadline, 60.0))):
                _check_cancel(cancel_event)
            if result["error"] is not None:
                raise result["error"]
            answers = result["answers"]
        else:
            answers = lookup()
    except NetworkSecurityError:
        raise
    except (OSError, socket.gaierror) as exc:
        raise NetworkSecurityError(N_("Image host name could not be resolved.")) from exc
    addresses = []
    seen = set()
    for answer in answers:
        sockaddr = answer[4]
        if not sockaddr:
            continue
        address = _public_ip(sockaddr[0])
        if address not in seen:
            seen.add(address)
            addresses.append(address)
    if not addresses:
        raise NetworkSecurityError(N_("Image host did not resolve to a public IP address."))
    addresses = tuple(addresses)
    if use_cache:
        with _DNS_CACHE_LOCK:
            _DNS_CACHE[cache_key] = (
                time.monotonic() + DNS_CACHE_TTL_SECONDS,
                addresses,
            )
            _DNS_CACHE.move_to_end(cache_key)
            while len(_DNS_CACHE) > DNS_CACHE_MAX_ENTRIES:
                _DNS_CACHE.popitem(last=False)
    return addresses


def _shared_ssl_context():
    context = _SSL_CONTEXT[0]
    if context is not None:
        return context
    with _SSL_CONTEXT_LOCK:
        context = _SSL_CONTEXT[0]
        if context is None:
            context = ssl.create_default_context()
            _SSL_CONTEXT[0] = context
    return context


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, hostname, address, port, timeout):
        context = _shared_ssl_context()
        http.client.HTTPSConnection.__init__(
            self,
            hostname,
            port=port,
            timeout=timeout,
            context=context,
        )
        self._pinned_address = address
        self._verified_hostname = hostname

    def connect(self):
        cancel_event = getattr(self, "_cancel_event", None)
        if cancel_event is not None:
            self.sock = _cancellable_connect(self._pinned_address, self.port, self.timeout, cancel_event)
            try:
                self.sock = self._context.wrap_socket(self.sock,
                    server_hostname=self._verified_hostname, do_handshake_on_connect=False)
                self.sock.do_handshake()
                _check_cancel(cancel_event)
            except Exception:
                self.sock.close()
                raise
            return
        sock = socket.create_connection(
            (self._pinned_address, self.port),
            self.timeout,
            self.source_address,
        )
        try:
            self.sock = self._context.wrap_socket(
                sock,
                server_hostname=self._verified_hostname,
            )
        except Exception:
            sock.close()
            raise


def _host_header(hostname, port, scheme):
    host = hostname
    try:
        if ipaddress.ip_address(hostname).version == 6:
            host = "[{}]".format(hostname)
    except ValueError:
        pass
    default = 443 if scheme == "https" else 80
    return host if int(port) == default else "{}:{}".format(host, port)


def _response_headers(response):
    headers = {}
    lengths = []
    for key, value in response.getheaders():
        lowered = str(key).lower()
        cleaned = str(value).strip()
        if lowered == "content-length":
            lengths.append(cleaned)
        headers[lowered] = cleaned
    if len(set(lengths)) > 1:
        raise NetworkSecurityError(N_("Image response has conflicting size information."))
    return headers


def _request_once(
    url_info,
    address,
    maximum,
    timeout,
    accept,
    user_agent,
    deadline=None,
    request_headers=None,
    cancel_event=None,
):
    connection = None
    response = None
    cancellation_ticket = None
    try:
        _check_cancel(cancel_event)
        if url_info["scheme"] == "https":
            connection = _PinnedHTTPSConnection(
                url_info["hostname"],
                address,
                url_info["port"],
                _remaining_timeout(deadline, timeout),
            )
        else:
            connection = http.client.HTTPConnection(
                address,
                port=url_info["port"],
                timeout=_remaining_timeout(deadline, timeout),
            )
        if cancel_event is not None:
            connection._cancel_event = cancel_event
            if url_info["scheme"] == "http":
                connection._create_connection = lambda address, timeout, source_address=None: _cancellable_connect(
                    address[0], address[1], timeout, cancel_event)
            cancellation_ticket = _TRANSFER_CANCELLATION.add(connection, cancel_event,
                deadline or time.monotonic() + timeout)
        headers = {
            "Accept": accept,
            "Accept-Encoding": "identity",
            "Connection": "close",
            "Host": _host_header(
                url_info["hostname"],
                url_info["port"],
                url_info["scheme"],
            ),
            "User-Agent": user_agent,
        }
        headers.update(request_headers or {})
        connection.request(
            "GET",
            url_info["path"],
            headers=headers,
        )
        response = connection.getresponse()
        if cancellation_ticket is not None:
            _TRANSFER_CANCELLATION.response(cancellation_ticket, response)
        _check_cancel(cancel_event)
        status = int(response.status)
        headers = _response_headers(response)
        if status in REDIRECT_STATUSES:
            return status, headers, b""
        if status != 200:
            error = NetworkSecurityError(
                _(N_("Image server returned HTTP {}.")).format(status)
            )
            error.status = status
            raise error
        content_encoding = headers.get("content-encoding", "").lower()
        if content_encoding not in ("", "identity"):
            raise NetworkSecurityError(
                N_("Compressed image HTTP responses are not accepted.")
            )
        content_length = headers.get("content-length")
        if content_length:
            try:
                declared = int(content_length)
            except ValueError as exc:
                raise NetworkSecurityError(
                    N_("Image response size is invalid.")
                ) from exc
            if declared < 0 or declared > maximum:
                raise NetworkSecurityError(
                    N_("Image response exceeds the safe size limit.")
                )
        chunks = []
        total = 0
        while True:
            _check_cancel(cancel_event)
            remaining = _remaining_timeout(deadline, timeout)
            sock = getattr(connection, "sock", None)
            if sock is not None:
                try:
                    sock.settimeout(remaining)
                except Exception:
                    pass
            chunk = response.read(min(65536, maximum + 1 - total))
            _check_cancel(cancel_event)
            if not chunk:
                break
            total += len(chunk)
            if total > maximum:
                raise NetworkSecurityError(
                    N_("Image response exceeds the safe size limit.")
                )
            chunks.append(chunk)
        return status, headers, b"".join(chunks)
    finally:
        if cancellation_ticket is not None:
            _TRANSFER_CANCELLATION.remove(cancellation_ticket)
        if response is not None:
            try:
                response.close()
            except Exception:
                pass
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass


def fetch_public_url(
    url,
    maximum,
    timeout,
    accept,
    user_agent,
    resolver=None,
    max_redirects=MAX_REDIRECTS,
    request_headers=None,
    cancel_event=None,
):
    try:
        maximum = int(maximum)
        timeout = float(timeout)
        max_redirects = int(max_redirects)
    except (TypeError, ValueError, OverflowError) as exc:
        raise NetworkSecurityError(N_("Image download limits are invalid.")) from exc
    if maximum <= 0 or timeout <= 0 or max_redirects < 0 or max_redirects > 5:
        raise NetworkSecurityError(N_("Image download limits are invalid."))
    initial = validate_public_url_syntax(url)
    current = initial["url"]
    provider_headers = _provider_image_headers(request_headers)
    provider_origin = (
        initial["scheme"], initial["hostname"], initial["port"]
    )
    provider_headers_allowed = bool(provider_headers)
    deadline = time.monotonic() + timeout
    previous_scheme = ""
    for redirect_count in range(max_redirects + 1):
        _check_cancel(cancel_event)
        info = validate_public_url_syntax(current)
        if previous_scheme == "https" and info["scheme"] != "https":
            raise NetworkSecurityError(
                N_("An HTTPS image request cannot be redirected to HTTP.")
            )
        current_origin = (info["scheme"], info["hostname"], info["port"])
        selected_headers = (
            provider_headers
            if provider_headers_allowed and current_origin == provider_origin
            else {}
        )
        resolve_options = {"resolver": resolver, "deadline": deadline}
        if cancel_event is not None:
            resolve_options["cancel_event"] = cancel_event
        addresses = resolve_public_addresses(
            info["hostname"],
            info["port"],
            **resolve_options,
        )
        last_error = None
        result = None
        for address in addresses:
            _check_cancel(cancel_event)
            try:
                request_options = {"deadline": deadline, "request_headers": selected_headers}
                if cancel_event is not None:
                    request_options["cancel_event"] = cancel_event
                result = _request_once(
                    info,
                    address,
                    maximum,
                    timeout,
                    str(accept),
                    str(user_agent),
                    **request_options,
                )
                break
            except (OSError, socket.error, http.client.HTTPException) as exc:
                _check_cancel(cancel_event)
                last_error = exc
        if result is None:
            raise NetworkSecurityError(
                N_("Could not establish a secure connection to the image server.")
            ) from last_error
        status, headers, body = result
        if status == 200:
            return body, headers, info["url"]
        location = headers.get("location", "")
        if not location:
            raise NetworkSecurityError(N_("Image redirect URL is missing."))
        if redirect_count >= max_redirects:
            raise NetworkSecurityError(N_("Image request was redirected too many times."))
        previous_scheme = info["scheme"]
        current = urljoin(info["url"], location)
        redirected = validate_public_url_syntax(current)
        redirected_origin = (
            redirected["scheme"],
            redirected["hostname"],
            redirected["port"],
        )
        if redirected_origin != provider_origin:
            # Provider credentials are valid only for the original server.
            # Once a redirect leaves that origin they never reappear, even if
            # a later redirect returns to it.
            provider_headers_allowed = False
    raise NetworkSecurityError(N_("Image redirection could not be completed."))

