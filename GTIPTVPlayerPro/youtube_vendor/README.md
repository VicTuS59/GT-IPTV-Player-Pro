The resolver in `video_url.py`, `compat.py` and `jsinterp.py` is adapted from
Taapat/enigma2-plugin-youtube (GPL-2.0), commit versions:
video_url.py 964cc4e78c7e670c934c73e072b0b2c0c9f23388,
compat.py a03fcfcecd376503188e1d658436863897688566,
jsinterp.py 474bacc42514059f38358ffc91d01f4ebe7a75b9.
Upstream: https://github.com/Taapat/enigma2-plugin-youtube
Adaptations: local thread-specific configuration, priority list reset,
normal HTTPS certificate verification, and direct timeout handling.
Local native playback uses original H.264 or 8-bit SDR VP9 video and AAC
audio inputs without remuxing. Separate audio and VP9 use ServiceApp's
ExtEplayer3 backend (service 5002). Its VP9 writer includes Vu+ driver framing:
https://github.com/oe-mirrors/exteplayer3/blob/master/output/writer/mipsel/vp.c
AV1 and HDR/high-bit-depth VP9 remain outside this native selection; search
availability still describes offered resolutions independently of decoding.
Quality inspection follows both VOD and live HLS variants, reads their actual
resolution/codecs, and distinguishes incomplete requests from absent quality.
YouTube may change its player format and require an extractor update.

Search availability reads video resolution metadata across codecs separately
from native AVC/VP9/AAC playback capability and signed URL extraction.
