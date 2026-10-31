"""Small recording/media primitives; independent of the optional live patch."""
import socket
import urllib.parse
import urllib.request

PACKET=188
CHUNK=PACKET*256

def media_request(value):
    base, separator, fragment = str(value).partition('#')
    parsed = urllib.parse.urlsplit(base)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username:
        raise ValueError('Unsupported media address')
    headers = {'Accept-Encoding': 'identity', 'Connection': 'close'}
    if separator:
        for part in fragment.split('&'):
            name, equal, content = part.partition('=')
            if not equal or not name or any(c in name + content for c in '\r\n\0'):
                raise ValueError('Invalid media header')
            if name.lower() in ('host', 'content-length', 'transfer-encoding', 'range'):
                continue
            headers[name] = content
    return urllib.request.Request(base, headers=headers)


class TransportPackets:
    """Keep complete 188-byte TS packets; discard partial tails at reconnect."""
    def __init__(self):
        self.pending = b''
        self.locked = False
        self.discarded = 0

    def feed(self, data):
        value = self.pending + data
        if not self.locked:
            found = next((i for i in range(max(0, len(value) - PACKET * 2))
                          if value[i] == 0x47 and value[i + PACKET] == 0x47
                          and value[i + PACKET * 2] == 0x47), None)
            if found is None:
                retained = min(len(value), PACKET * 2)
                self.discarded += len(value) - retained
                self.pending = value[-retained:] if retained else b''
                if self.discarded > 65536:
                    raise ValueError('Input is not MPEG-TS')
                return b''
            self.discarded += found
            value = value[found:]
            self.locked = True
        length = len(value) // PACKET * PACKET
        output, self.pending = value[:length], value[length:]
        if any(output[i] != 0x47 for i in range(0, len(output), PACKET)):
            raise ValueError('MPEG-TS alignment changed')
        return output


def response_socket(response):
    pending, seen = [response], set()
    while pending and len(seen) < 24:
        value = pending.pop()
        if id(value) in seen:
            continue
        seen.add(id(value))
        if isinstance(value, socket.socket):
            return value
        for name in ('fp', 'raw', '_sock', 'sock'):
            child = getattr(value, name, None)
            if child is not None:
                pending.append(child)
    return None

