The resolver in `video_url.py`, `compat.py` and `jsinterp.py` is adapted from
Taapat/enigma2-plugin-youtube (GPL-2.0), commit versions:
video_url.py 964cc4e78c7e670c934c73e072b0b2c0c9f23388,
compat.py a03fcfcecd376503188e1d658436863897688566,
jsinterp.py 474bacc42514059f38358ffc91d01f4ebe7a75b9.
Upstream: https://github.com/Taapat/enigma2-plugin-youtube
Adaptations: local thread-specific configuration, priority list reset,
normal HTTPS certificate verification, and direct timeout handling.
YouTube may change its player format and require an extractor update.
