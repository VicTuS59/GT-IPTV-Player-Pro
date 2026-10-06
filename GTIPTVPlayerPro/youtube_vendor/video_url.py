# -*- coding: UTF-8 -*-
# This video extraction code based on youtube-dl: https://github.com/ytdl-org/youtube-dl

from __future__ import print_function

from re import escape
from re import findall
from re import match
from re import search
from re import sub
from json import dumps
from json import loads
from json import JSONDecoder
from time import monotonic
from urllib.parse import urljoin

from .config import config

from .compat import compat_parse_qs
from .compat import compat_Request
from .compat import compat_urlopen
from .compat import compat_URLError
from .compat import SUBURI
from .jsinterp import JSInterpreter
from ..youtube_playback import youtube_stream_parts


IGNORE_VIDEO_FORMAT = (
	'43', '44', '45', '46',  # webm
	'82', '83', '84', '85',  # 3D
	'100', '101', '102',  # 3D
	'167', '168', '169',  # webm
	'170', '171', '172',  # webm
	'218', '219',  # webm
	'394', '395', '396', '397', '398', '399', '400', '401', '402', '694', '695', '696', '697', '698', '699', '700', '701', '571',  # AV1
	'249', '250', '251',  # Opus audio
)

PRIORITY_VIDEO_FORMAT = ()
# Original video-only representations accepted by the native player. VP9
# WebM video can use a separate MP4 AAC input in ExtEplayer3, without remuxing.
VP9_VIDEO_FORMAT = frozenset(('242', '243', '244', '245', '246', '247',
	'248', '271', '272', '278', '302', '303', '308', '313', '315'))
DASH_VIDEO_FORMAT = frozenset((
	'133', '134', '135', '136', '137', '138', '160',
	'212', '229', '230', '231', '232', '248', '264',
	'271', '272', '266', '269', '270', '298', '299',
	'303', '313', '315', '308'
)) | VP9_VIDEO_FORMAT
H264_VIDEO_FORMAT = DASH_VIDEO_FORMAT - VP9_VIDEO_FORMAT | frozenset(('18', '22', '37', '38'))
VP9_HDR_VIDEO_FORMAT = frozenset(str(value) for value in range(330, 338))
DASH_AUDIO_FORMAT = ('140', '141', '139')

# Use the response's height when present.  Older YouTube responses sometimes
# omit it, so retain the established itag groups as a bounded fallback.
VIDEO_FORMAT_GROUPS = (
	(144, ('17', '91', '13', '151', '160', '269', '278')),
	(240, ('5', '36', '92', '132', '133', '229', '242')),
	(360, ('18', '93', '34', '6', '134', '230', '243')),
	(480, ('35', '59', '78', '94', '135', '212', '231', '244', '245', '246')),
	(720, ('22', '95', '300', '136', '298', '232', '247', '302')),
	(1080, ('37', '96', '301', '137', '299', '248', '303', '270')),
	(1440, ('264', '271', '308')),
	(2160, ('38', '266', '138', '313', '315', '272')),
)
VIDEO_FORMAT_HEIGHT = {itag: height for height, group in VIDEO_FORMAT_GROUPS for itag in group}
RESOLUTION_LIMIT = {'18': 360, '35': 480, '22': 720, '37': 1080, '264': 1440, '38': 2160}
H264_HLS_FORMATS = frozenset(('91', '92', '93', '94', '95', '96', '300', '301',
	'269', '229', '230', '231', '232', '270', '311', '312'))


def _format_height(fmt):
	try:
		height = int(fmt.get('height') or 0)
	except (TypeError, ValueError, OverflowError):
		height = 0
	return height if 0 < height <= 4320 else VIDEO_FORMAT_HEIGHT.get(str(fmt.get('itag', '')), 0)


def _codec_tokens(mime):
	codecs = search(r'codecs\s*=\s*["\']?([^"\';]+)', (mime or '').lower())
	return tuple(value.strip() for value in codecs.group(1).split(',')) if codecs else ()


def _native_video_codec(fmt):
	"""Accept AVC or 8-bit SDR VP9; availability remains independent of this.

	ExtEplayer3 has a native VP9 writer, including the Vu+ driver framing.
	Do not select AV1 or 10/12-bit HDR VP9 just because its height matches.
	"""
	mime = (fmt.get('mimeType') or '').lower()
	itag = str(fmt.get('itag', ''))
	if not mime:
		return 'h264' if itag in ('18', '22', '37', '38') else ''
	container = mime.split(';', 1)[0].strip()
	if container not in ('video/mp4', 'video/webm'):
		return ''
	codecs = _codec_tokens(mime)
	if not codecs:
		return 'h264' if container == 'video/mp4' and itag in H264_VIDEO_FORMAT else ''
	video_codecs = [codec for codec in codecs if not codec.startswith('mp4a.')]
	if len(video_codecs) != 1:
		return ''
	codec = video_codecs[0]
	if codec.startswith('avc1.'):
		return 'h264' if container == 'video/mp4' else ''
	if codec == 'vp9':
		vp9_sdr = True
	elif codec.startswith(('vp09.', 'vp9.')):
		fields = codec.split('.')
		vp9_sdr = fields[1] in ('0', '1', '00', '01') and (
			(len(fields) >= 4 and fields[3] in ('8', '08')) or
			(codec.startswith('vp9.') and len(fields) == 2))
	else:
		return ''
	color = fmt.get('colorInfo') or {}
	transfer = sub(r'[^a-z0-9]', '', str(color.get('transferCharacteristics') or '').lower())
	if (not vp9_sdr or itag in VP9_HDR_VIDEO_FORMAT
			or 'hdr' in str(fmt.get('qualityLabel') or '').lower()
			or transfer.endswith(('smptest2084', 'smpte2084', 'aribstdb67', 'hlg', 'pq'))):
		return ''
	return 'vp9'


def _video_uses_separate_audio(fmt):
	codecs = _codec_tokens(fmt.get('mimeType'))
	return str(fmt.get('itag', '')) in DASH_VIDEO_FORMAT or (
		bool(codecs) and not any(codec.startswith('mp4a.') for codec in codecs))


def create_priority_formats():
	global PRIORITY_VIDEO_FORMAT
	PRIORITY_VIDEO_FORMAT = ()
	itag = config.plugins.YouTube.maxResolution.value
	for unused_height, video_format in VIDEO_FORMAT_GROUPS:
		PRIORITY_VIDEO_FORMAT = video_format + PRIORITY_VIDEO_FORMAT
		if video_format[0] == itag:
			break


create_priority_formats()


class YouTubeVideoUrl():
	def __init__(self):
		self.use_dash_mp4 = ()
		self.exact_quality = 0
		self.preferred_quality = 0
		self._probe_deadline = 0
		self.selected_quality = 0
		self.selected_video_itag = ''
		self.selected_video_codec = ''
		self.selected_audio_itag = ''
		self.duration_seconds = 0
		self._code_cache = {}
		self._player_cache = {}
		self.nsig_cache = (None, None)

	@staticmethod
	def try_get(src, getter):
		for x in getter:
			if isinstance(src, dict) and x in src:
				src = src[x]
			else:
				return None
		return src

	@staticmethod
	def _guess_encoding_from_content(content_type, webpage_bytes):
		m = match(r'[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+\s*;\s*charset=(.+)', content_type)
		if m:
			encoding = m.group(1)
		else:
			m = search(br'<meta[^>]+charset=[\'"]?([^\'")]+)[ /\'">]', webpage_bytes[:1024])
			if m:
				encoding = m.group(1).decode('ascii')
			elif webpage_bytes.startswith(b'\xff\xfe'):
				encoding = 'utf-16'
			else:
				encoding = 'utf-8'

		return encoding

	def _download_webpage(self, url, data=None, headers={}):
		""" Return the data of the page as a string """

		if data:
			data = dumps(data).encode('utf8')
		if data or headers:
			url = compat_Request(url, data=data, headers=headers)
			url.get_method = lambda: 'POST'

		try:
			timeout = 5
			if self._probe_deadline:
				timeout = min(4, self._probe_deadline - monotonic())
				if timeout <= 0:
					raise RuntimeError('Quality probe timed out')
			urlh = compat_urlopen(url, timeout=timeout)
		except compat_URLError as e:  # pragma: no cover
			raise RuntimeError(e.reason)

		content_type = urlh.headers.get('Content-Type', '')
		try:
			webpage_bytes = urlh.read(4 * 1024 * 1024 + 1) if self._probe_deadline else urlh.read()
		finally:
			urlh.close()
		if self._probe_deadline and len(webpage_bytes) > 4 * 1024 * 1024:
			raise RuntimeError('Quality response too large')
		encoding = self._guess_encoding_from_content(content_type, webpage_bytes)

		try:
			content = webpage_bytes.decode(encoding, 'replace')
		except Exception:  # pragma: no cover
			content = webpage_bytes.decode('utf-8', 'replace')

		return content

	@staticmethod
	def _extract_n_function_name(jscode):
		func_name, idx = search(
			r'''(?x)
				(?:
					\.get\("n"\)\)&&\(b=|
					(?:
						b=String\.fromCharCode\(110\)|
						(?P<str_idx>[a-zA-Z0-9_$.]+)&&\(b="nn"\[\+(?P=str_idx)\]
					)
					(?:
						,[a-zA-Z0-9_$]+\(a\))?,c=a\.
						(?:
							get\(b\)|
							[a-zA-Z0-9_$]+\[b\]\|\|null
						)\)&&\(c=|
					\b(?P<var>[a-zA-Z0-9_$]+)=
				)(?P<nfunc>[a-zA-Z0-9_$]+)(?:\[(?P<idx>\d+)\])?\([a-zA-Z]\)
				(?(var),[a-zA-Z0-9_$]+\.set\((?:"n+"|[a-zA-Z0-9_$]+)\,(?P=var)\))
			''', jscode
		).group('nfunc', 'idx')
		if not func_name:
			print('[YouTubeVideoUrl] Falling back to generic n function search')
			return search(
				r'''(?xs)
					;\s*(?P<name>[a-zA-Z0-9_$]+)\s*=\s*function\([a-zA-Z0-9_$]+\)
					\s*\{(?:(?!};).)+?return\s*(?P<q>["'])[\w-]+_w8_(?P=q)\s*\+\s*[a-zA-Z0-9_$]+
				''', jscode
			).group('name')
		if not idx:
			return func_name
		if int(idx) == 0:
			real_nfunc = search(
				r'var %s\s*=\s*(\[.+?\])\s*[,;]' % (escape(func_name), ),
				jscode
			)
			if real_nfunc:
				return real_nfunc.group(1)[1:-1]

	def _extract_player_info(self):
		res = self._download_webpage('https://www.youtube.com/iframe_api')
		if res:
			player_id = search(r'player\\?/([0-9a-fA-F]{8})\\?/', res)
			if player_id:
				return player_id.group(1)
		print('[YouTubeVideoUrl] Cannot get player info')

	def _load_player(self, player_id):
		if player_id and player_id not in self._player_cache:
			self._player_cache[player_id] = self._download_webpage(
				'https://www.youtube.com/s/player/%s/player_ias.vflset/en_US/base.js' % player_id
			)

	@staticmethod
	def _fixup_n_function_code(argnames, code):
		return argnames, sub(
			r';\s*if\s*\(\s*typeof\s+[a-zA-Z0-9_$]+\s*===?\s*(["\'])undefined\1\s*\)\s*return\s+%s;' % argnames[0],
			';', code)

	def _extract_function(self, player_id, s_id):
		if player_id not in self._player_cache:
			self._load_player(player_id)
		jsi = JSInterpreter(self._player_cache[player_id])
		if s_id not in self._code_cache:
			if s_id.startswith('nsig_'):
				funcname = self._extract_n_function_name(self._player_cache[player_id])
			else:
				funcname = self._parse_sig_js(self._player_cache[player_id])
			self._code_cache[s_id] = self._fixup_n_function_code(*jsi.extract_function_code(funcname))
		return lambda s: jsi.extract_function_from_code(*self._code_cache[s_id])([s])

	def _unthrottle_url(self, url, player_id):
		n_match = search(r'(?:\?|&)n=([^&]+)', url)
		if not n_match:
			return url
		n_param = n_match.group(1)
		n_id = 'nsig_%s_%s' % (player_id, '.'.join(str(len(p)) for p in n_param.split('.')))
		if self.nsig_cache[0] != n_param:
			print('[YouTubeVideoUrl] Decrypt nsig', n_id)
			self.nsig_cache = (None, None)
			try:
				ret = self._extract_function(player_id, n_id)(n_param)
			except Exception as ex:
				print('[YouTubeVideoUrl] Unable to decode nsig', ex)
			else:
				if ret.startswith('enhanced_except_') or ret.endswith(n_param):
					print('[YouTubeVideoUrl] Unhandled exception in decode', ret)
				else:
					self.nsig_cache = (n_param, ret)
		if self.nsig_cache[1]:
			print('[YouTubeVideoUrl] Decrypted nsig')
			return url[:n_match.start(1)] + self.nsig_cache[1] + url[n_match.end(1):]
		if n_id in self._code_cache:
			del self._code_cache[n_id]
		return url

	def _decrypt_signature_url(self, sc, player_id):
		"""Turn the encrypted s field into a working signature"""
		s = sc.get('s', [''])[0]
		s_id = 'sig_%s_%s' % (player_id, '.'.join(str(len(p)) for p in s.split('.')))
		print('[YouTubeVideoUrl] Decrypt signature', s_id)
		try:
			sig = self._extract_function(player_id, s_id)(s)
		except Exception as ex:
			print('[YouTubeVideoUrl] Signature extraction failed', ex)
			if s_id in self._code_cache:
				del self._code_cache[s_id]
		else:
			return '%s&%s=%s' % (sc['url'][0], sc['sp'][0] if 'sp' in sc else 'signature', sig)

	def _parse_sig_js(self, jscode):

		def _search_regex(pattern, string):
			mobj = ''
			for p in pattern:
				mobj = search(p, string, 0)
				if mobj:
					break
			return mobj

		return _search_regex(
			(r'\b(?P<var>[a-zA-Z0-9_$]+)&&\((?P=var)=(?P<sig>[a-zA-Z0-9_$]{2,})\(decodeURIComponent\((?P=var)\)\)',
				r'(?P<sig>[a-zA-Z0-9_$]+)\s*=\s*function\(\s*(?P<arg>[a-zA-Z0-9_$]+)\s*\)\s*{\s*(?P=arg)\s*=\s*(?P=arg)\.split\(\s*""\s*\)\s*;\s*[^}]+;\s*return\s+(?P=arg)\.join\(\s*""\s*\)',
				r'(?:\b|[^a-zA-Z0-9_$])(?P<sig>[a-zA-Z0-9_$]{2,})\s*=\s*function\(\s*a\s*\)\s*{\s*a\s*=\s*a\.split\(\s*""\s*\)(?:;[a-zA-Z0-9_$]{2}\.[a-zA-Z0-9_$]{2}\(a,\d+\))?',
				# Old patterns
				r'\b[cs]\s*&&\s*[adf]\.set\([^,]+\s*,\s*encodeURIComponent\s*\(\s*(?P<sig>[a-zA-Z0-9$]+)\(',
				r'\b[a-zA-Z0-9]+\s*&&\s*[a-zA-Z0-9]+\.set\([^,]+\s*,\s*encodeURIComponent\s*\(\s*(?P<sig>[a-zA-Z0-9$]+)\(',
				r'\bm=(?P<sig>[a-zA-Z0-9$]{2,})\(decodeURIComponent\(h\.s\)\)',
				# Obsolete patterns
				r'("|\')signature\1\s*,\s*(?P<sig>[a-zA-Z0-9$]+)\(',
				r'\.sig\|\|(?P<sig>[a-zA-Z0-9$]+)\(',
				r'yt\.akamaized\.net/\)\s*\|\|\s*.*?\s*[cs]\s*&&\s*[adf]\.set\([^,]+\s*,\s*(?:encodeURIComponent\s*\()?\s*(?P<sig>[a-zA-Z0-9$]+)\(',
				r'\b[cs]\s*&&\s*[adf]\.set\([^,]+\s*,\s*(?P<sig>[a-zA-Z0-9$]+)\(',
				r'\bc\s*&&\s*[a-zA-Z0-9]+\.set\([^,]+\s*,\s*\([^)]*\)\s*\(\s*(?P<sig>[a-zA-Z0-9$]+)\('),
			jscode
		).group('sig')

	@staticmethod
	def _parse_m3u8_attributes(attrib):
		return {key: val[1:-1] if val.startswith('"') else val for (key, val) in findall(r'(?P<key>[A-Z0-9-]+)=(?P<val>"[^"]+"|[^",]+)(?:,|$)', attrib)}

	def _get_m3u8_audio_urls(self, manifest):
		audio_urls = {}
		if '#EXT-X-MEDIA:' in manifest:
			for line in manifest.splitlines():
				if line.startswith('#EXT-X-MEDIA:'):
					audio_info = self._parse_m3u8_attributes(line)
					if audio_info.get('TYPE') == 'AUDIO' and audio_info.get('URI'):
						audio_urls[audio_info.get('GROUP-ID')] = audio_info['URI']
		return audio_urls

	def _extract_from_m3u8(self, manifest_url, limit=None, allow_separate=True):
		"""Read the manifest's real resolution and codecs for VOD and live video."""
		url_map = []
		if limit is None:
			limit = RESOLUTION_LIMIT.get(str(config.plugins.YouTube.maxResolution.value), 1440)
		manifest = self._download_webpage(manifest_url)
		if '#EXTM3U' not in manifest:
			raise RuntimeError('Invalid quality manifest')
		audio_urls = self._get_m3u8_audio_urls(manifest)
		attributes = None
		for line in manifest.splitlines():
			line = line.strip()
			if line.startswith('#EXT-X-STREAM-INF:'):
				attributes = self._parse_m3u8_attributes(line)
				continue
			if not attributes or not line or line.startswith('#'):
				continue
			info, attributes = attributes, None
			url = urljoin(manifest_url, line)
			if not url.startswith('https://'):
				continue
			itag_match = search(r'/sgovp/[^/]+itag%3D(\d+?)/', url) or search(r'/itag/(\d+?)/', url)
			itag = itag_match.group(1) if itag_match else ''
			resolution = match(r'\d+x(\d+)$', info.get('RESOLUTION', ''))
			height = int(resolution.group(1)) if resolution else VIDEO_FORMAT_HEIGHT.get(itag, 0)
			codecs = info.get('CODECS', '').lower()
			if codecs:
				codec = _native_video_codec({'mimeType': 'video/mp4; codecs="%s"' % codecs})
				if not codec or not any(value.startswith('mp4a.') for value in _codec_tokens('codecs="%s"' % codecs)):
					continue
			elif itag not in H264_HLS_FORMATS:
				continue
			else:
				codec = 'h264'
			if not 0 < height <= limit:
				continue
			audio_id = info.get('AUDIO')
			if audio_id:
				if not allow_separate or audio_id not in audio_urls:
					continue
				audio_url = urljoin(manifest_url, audio_urls[audio_id])
				if not audio_url.startswith('https://'):
					continue
				url += SUBURI + audio_url
			url_map.append({'url': url, 'itag': itag, 'height': height, 'codec': codec})
		return sorted(url_map, key=lambda fmt: (-fmt['height'], fmt['codec'] != 'h264'))

	def _skip_fmt(self, fmt, itag):
		return (
			fmt.get('targetDurationSec') or
			fmt.get('drmFamilies') or
			fmt.get('type') == 'FORMAT_STREAM_TYPE_OTF' or
			itag in IGNORE_VIDEO_FORMAT or
			itag in self.use_dash_mp4
		)

	def _extract_url(self, fmt, player_id):
		url = fmt.get('url')
		if not url and 'signatureCipher' in fmt:
			url = self._decrypt_signature_url(compat_parse_qs(fmt.get('signatureCipher', '')), player_id)
		if url:
			if search(r'(?:\?|&)n=[^&]+', url):
				url = self._unthrottle_url(url, player_id)
			return url

	@staticmethod
	def _video_pref(fmt, prefer):
		if prefer == 100:
			codec = _native_video_codec(fmt)
			prefer = 20 if codec == 'h264' else 30 if codec == 'vp9' else 200
		return prefer

	@staticmethod
	def _audio_pref(fmt, prefer, get_audio):
		audio_track = fmt.get('audioTrack') or {}
		name = (audio_track.get('displayName') or '').lower()
		original = audio_track.get('isOriginal') or any(value in name for value in (
			'original', 'orijinal', 'originale', 'originalton', 'الأصل'))
		if prefer == 100:
			prefer = 20 if 'audio/mp4' in (fmt.get('mimeType') or '').lower() else 200
		if get_audio == 'original' and original:
			prefer -= 400
		elif audio_track.get('audioIsDefault'):
			prefer -= 200
		return prefer

	def _sort_formats(self, priority_formats, streaming_formats, get_audio=None):
		sorted_fmt = []
		for fmt in streaming_formats:
			itag = str(fmt.get('itag', ''))
			if self._skip_fmt(fmt, itag):
				continue
			prefer = priority_formats.index(itag) if itag in priority_formats else 100
			prefer = self._video_pref(fmt, prefer) if get_audio is None else self._audio_pref(fmt, prefer, get_audio)
			if prefer < 200:
				fmt['preference'] = prefer
				sorted_fmt.append(fmt)
		return sorted(sorted_fmt, key=lambda k: k['preference'])

	def _extract_fmt_video_format(self, streaming_formats, player_id, stream_mode='auto', audio_preference='default'):
		print('[YouTubeVideoUrl] Try fmt url')
		formats = self._sort_formats(PRIORITY_VIDEO_FORMAT, streaming_formats)
		limit = 2160 if self.preferred_quality else RESOLUTION_LIMIT.get(str(config.plugins.YouTube.maxResolution.value), 720)
		formats.sort(key=lambda fmt: (bool(self.preferred_quality and _format_height(fmt) != self.preferred_quality),
			-_format_height(fmt),
			stream_mode == 'dash' and not _video_uses_separate_audio(fmt),
			_native_video_codec(fmt) != 'h264', fmt['preference']))
		for fmt in formats:
			itag = str(fmt.get('itag', ''))
			height = _format_height(fmt)
			if not height or height > limit or (self.exact_quality and height != self.exact_quality):
				continue
			mime = (fmt.get('mimeType') or '').lower()
			codec = _native_video_codec(fmt)
			if not codec:
				continue
			separate = _video_uses_separate_audio(fmt)
			if separate and stream_mode == 'compatible':
				continue
			if not separate and mime and 'codecs=' in mime and 'mp4a' not in mime:
				continue
			audio_url, audio_fmt = ('', None)
			if separate:
				try:
					audio_url, audio_fmt = self._extract_dash_audio_format(
						streaming_formats, player_id, audio_preference)
				except Exception:
					if not self.preferred_quality:
						raise
					continue
				if not audio_url:
					# Never send a silent video-only representation to the player.
					continue
			try:
				url = self._extract_url(fmt, player_id)
				if url and self.preferred_quality:
					youtube_stream_parts(url + (SUBURI + audio_url if audio_url else ''))
			except Exception:
				if not self.preferred_quality:
					raise
				continue
			if url:
				self.selected_quality = height
				self.selected_video_itag = itag
				self.selected_video_codec = codec
				self.selected_audio_itag = str(audio_fmt.get('itag', '')) if audio_fmt else ''
				print('[YouTubeVideoUrl] selected video itag=%s codec=%s audio itag=%s codec=%s mode=%s' % (
					itag, mime.split('codecs=')[-1][:56] if 'codecs=' in mime else 'unknown',
					str(audio_fmt.get('itag')) if audio_fmt else 'embedded',
					(audio_fmt.get('mimeType') or '').lower().split('codecs=')[-1][:56] if audio_fmt else 'embedded',
					stream_mode))
				return url + (SUBURI + audio_url if audio_url else ''), itag
		return '', ''

	def _extract_dash_audio_format(self, streaming_formats, player_id, audio_preference):
		""" If DASH MP4 video add also DASH MP4 audio track"""
		print('[YouTubeVideoUrl] Try fmt audio url')
		aac_formats = [fmt for fmt in streaming_formats if
			(fmt.get('mimeType') or '').lower().startswith('audio/mp4') and
			'mp4a.' in (fmt.get('mimeType') or '').lower()]
		for fmt in self._sort_formats(DASH_AUDIO_FORMAT, aac_formats, audio_preference):
			try:
				url = self._extract_url(fmt, player_id)
				if url and self.preferred_quality:
					youtube_stream_parts(url)
			except Exception:
				if not self.preferred_quality:
					raise
				continue
			if url:
				return url, fmt
		return '', None

	def _extract_signature_timestamp(self):
		sts = None
		player_id = self._extract_player_info()
		if player_id:
			if player_id not in self._player_cache:
				self._load_player(player_id)
			sts = search(
				r'(?:signatureTimestamp|sts)\s*:\s*(?P<sts>\d{5})',
				self._player_cache[player_id]
			).group('sts')
		return sts, player_id

	def _extract_visitor_id(self, webpage):
		if not webpage:
			return None
		ytcfg = search(r'ytcfg\.set\s*\(\s*({.+?})\s*\)\s*;', webpage)
		if ytcfg:
			try:
				return self.try_get(loads(ytcfg.group(1)), ('INNERTUBE_CONTEXT', 'client', 'visitorData'))
			except ValueError:  # pragma: no cover
				pass
		print('[YouTubeVideoUrl] Failed to extract visitor id')

	def _extract_web_response(self, webpage):
		player_response = search(r'ytInitialPlayerResponse\s*=\s*({[^>]*})\s*;\s*(?:var\s+meta|</script|\n)', webpage)
		if player_response:
			try:
				return loads(player_response.group(1)), self._extract_player_info()
			except ValueError:  # pragma: no cover
				pass
		print('[YouTubeVideoUrl] Failed to extract web response')
		return None, None

	def _extract_player_response(self, video_id, yt_auth, client, lang, webpage=None):
		player_id = None
		url = 'https://www.youtube.com/youtubei/v1/player?prettyPrint=false'
		data = {
			'videoId': video_id,
			'playbackContext': {
				'contentPlaybackContext': {
					'html5Preference': 'HTML5_PREF_WANTS'
				}
			},
			'context': {
				'client': {
					'hl': lang if lang else 'en'
				}
			}
		}
		headers = {
			'content-type': 'application/json',
			'Origin': 'https://www.youtube.com',
			'X-YouTube-Client-Name': client
		}
		if yt_auth:
			headers['Authorization'] = yt_auth
		if client == 3:
			VERSION = '21.08.266'
			USER_AGENT = 'com.google.android.youtube/%s (Linux; U; Android 11) gzip' % VERSION
			CLIENT_CONTEXT = {
				'clientName': 'ANDROID',
				'androidSdkVersion': 30,
				'osName': 'Android',
				'osVersion': '11'
			}
			data['params'] = '2AMB'
		elif client == 5:
			VERSION = '20.03.02'
			USER_AGENT = 'com.google.ios.youtube/%s (iPhone16,2; U; CPU iOS 18_2_1 like Mac OS X;)' % VERSION
			CLIENT_CONTEXT = {
				'clientName': 'IOS',
				'deviceMake': 'Apple',
				'deviceModel': 'iPhone16,2',
				'osName': 'iPhone',
				'osVersion': '18.2.1.22C161'
			}
		elif client == 7:
			VERSION = '7.20250129.15.00'
			USER_AGENT = 'Mozilla/5.0 (ChromiumStylePlatform) Cobalt/Version,gzip(gfe)'
			CLIENT_CONTEXT = {'clientName': 'TVHTML5'}
		elif client == 56:
			VERSION = '1.20241201.00.00'
			USER_AGENT = None
			CLIENT_CONTEXT = {'clientName': 'WEB_EMBEDDED_PLAYER'}
		else:
			VERSION = '2.0'
			USER_AGENT = None
			CLIENT_CONTEXT = {'clientName': 'TVHTML5_SIMPLY_EMBEDDED_PLAYER'}
			data['context']['thirdParty'] = {'embedUrl': 'https://www.youtube.com/'}
		data['context']['client']['clientVersion'] = VERSION
		data['context']['client'].update(CLIENT_CONTEXT)
		if USER_AGENT:
			data['context']['client']['userAgent'] = USER_AGENT
			headers['User-Agent'] = USER_AGENT
		if client in (7, 56, 85):
			sts, player_id = self._extract_signature_timestamp()
			if sts:
				data['playbackContext']['contentPlaybackContext']['signatureTimestamp'] = sts
		if client in (5, 7):
			headers['X-Goog-Visitor-Id'] = self._extract_visitor_id(webpage) or ''
		headers['X-YouTube-Client-Version'] = VERSION
		try:
			return loads(self._download_webpage(url, data, headers)), player_id
		except ValueError:  # pragma: no cover
			print('[YouTubeVideoUrl] Failed to parse JSON')
			return None, None

	def _load_player_response(self, video_id, yt_auth, lang):
		try:
			player_response, player_id = self._extract_player_response(video_id, None, 3, lang)
		except Exception:
			player_response, player_id = None, None
		is_live = self.try_get(player_response, ('videoDetails', 'isLive'))
		streaming = self.try_get(player_response, ('streamingData',)) or {}
		heights = self._native_format_heights(streaming, True)
		# A matching videoId does not guarantee a complete response. Some
		# clients return metadata without URLs or only a low combined stream.
		# The iOS client can offer the original HLS alternatives in that case.
		needs_fallback = (self.try_get(player_response, ('videoDetails', 'videoId')) != video_id
			or (not streaming.get('hlsManifestUrl') and
				(not heights or (self.exact_quality and self.exact_quality not in heights))))
		fallback_failed = False
		if needs_fallback:
			try:
				candidate, candidate_id = self._extract_player_response(video_id, None, 5, lang)
			except Exception:
				candidate, candidate_id = None, None
				fallback_failed = True
			if (self.try_get(candidate, ('videoDetails', 'videoId')) == video_id
					and self.try_get(candidate, ('streamingData',))):
				player_response, player_id = candidate, candidate_id
				is_live = self.try_get(candidate, ('videoDetails', 'isLive'))
			elif fallback_failed and self._probe_deadline:
				# Do not turn an incomplete client/network response into a
				# confirmed absence of the selected resolution.
				raise RuntimeError('Quality response could not be verified')

		if self.try_get(player_response, ('videoDetails', 'videoId')) != video_id:
			webpage = self._download_webpage('https://www.youtube.com/watch?v=%s&bpctr=9999999999&has_verified=1' % video_id)
			if not webpage:
				raise RuntimeError('Webpage not found!')

			if not is_live:
				print('[YouTubeVideoUrl] Got wrong player response, try mweb response')
				player_response, player_id = self._extract_player_response(video_id, None, 7, lang, webpage)
			else:
				if self.use_dash_mp4:
					print('[YouTubeVideoUrl] Got wrong player response, try for live web response')
					player_response, player_id = self._extract_web_response(webpage)
				else:
					print('[YouTubeVideoUrl] Got wrong player response, try for live ios client')
					player_response, player_id = self._extract_player_response(video_id, None, 5, lang, webpage)

		if self.try_get(player_response, ('playabilityStatus', 'status')) == 'LOGIN_REQUIRED':
			print('[YouTubeVideoUrl] Age gate content, try web embedded client')
			player_response, player_id = self._extract_player_response(video_id, None, 56, lang)
			if not player_response or self.try_get(player_response, ('playabilityStatus', 'status')) != 'OK':
				print('[YouTubeVideoUrl] Player response is not usable, try authorized tv embedded client')
				player_response, player_id = self._extract_player_response(video_id, yt_auth, 85, lang)
			if not player_response:
				raise RuntimeError('Age gate content player response not found!')

		if not isinstance(player_response, dict):
			raise RuntimeError('Player response not found!')
		return player_response, player_id, is_live

	def _native_format_heights(self, streaming, allow_separate):
		formats = (streaming.get('formats') or []) + (streaming.get('adaptiveFormats') or [])
		def usable(fmt):
			return bool(fmt.get('url') or fmt.get('signatureCipher')) and not self._skip_fmt(fmt, str(fmt.get('itag', '')))
		has_audio = any(usable(fmt) and
			(fmt.get('mimeType') or '').lower().startswith('audio/mp4') and
			'mp4a.' in (fmt.get('mimeType') or '').lower() for fmt in formats)
		heights = set()
		for fmt in formats:
			if not usable(fmt) or not _native_video_codec(fmt):
				continue
			if not _video_uses_separate_audio(fmt) or (allow_separate and has_audio):
				heights.add(_format_height(fmt))
		return {height for height in heights if height in (360, 480, 720, 1080, 1440, 2160)}

	def probe(self, video_id, language='en', allow_separate=True, deadline=0):
		"""Inspect the same native representations used by playback.

		Failures are unknown, not an empty list of available resolutions.
		The page budget bounds queued workers as well as individual requests.
		"""
		self._probe_deadline = min(deadline or float('inf'), monotonic() + 12)
		try:
			response, unused_player, unused_live = self._load_player_response(video_id, None, language)
			if self.try_get(response, ('videoDetails', 'videoId')) != video_id:
				raise RuntimeError('Video response does not match the selection')
			streaming = response.get('streamingData') or {}
			heights = self._native_format_heights(streaming, allow_separate)
			if streaming.get('hlsManifestUrl') and (not self.exact_quality or self.exact_quality not in heights):
				# Probe all supported heights, independent of a worker's
				# default maximum. Both ordinary videos and live streams use HLS.
				for fmt in self._extract_from_m3u8(streaming['hlsManifestUrl'], 2160, allow_separate):
					heights.add(fmt['height'])
			formats = (streaming.get('formats') or []) + (streaming.get('adaptiveFormats') or [])
			if not heights and not streaming.get('hlsManifestUrl') and not any(
					fmt.get('url') or fmt.get('signatureCipher') for fmt in formats):
				raise RuntimeError('Quality formats are incomplete')
			return sorted(heights)
		finally:
			self._probe_deadline = 0

	@staticmethod
	def _available_format_heights(streaming):
		"""Read video resolution metadata, independently of decoder/URL support."""
		heights = set()
		formats = (streaming.get('formats') or []) + (streaming.get('adaptiveFormats') or [])
		for fmt in formats:
			if not isinstance(fmt, dict) or fmt.get('drmFamilies'):
				continue
			mime = str(fmt.get('mimeType') or '').lower()
			if mime.startswith('audio/') or not (mime.startswith('video/') or fmt.get('qualityLabel') or fmt.get('width')):
				continue
			try:
				height = int(fmt['height']) if fmt.get('height') is not None else 0
			except (TypeError, ValueError, OverflowError):
				continue
			if not height and fmt.get('height') is None:
				label = match(r'^(\d+)p(?:\d+)?$', str(fmt.get('qualityLabel') or ''))
				height = int(label.group(1)) if label else 0
			if 0 < height <= 4320:
				heights.add(height)
		return heights

	def _available_hls_heights(self, manifest_url):
		manifest = self._download_webpage(manifest_url)
		if '#EXTM3U' not in manifest:
			raise RuntimeError('Invalid quality manifest')
		heights = set()
		for line in manifest.splitlines():
			if not line.strip().startswith('#EXT-X-STREAM-INF:'):
				continue
			info = self._parse_m3u8_attributes(line.strip())
			resolution = match(r'\d+x(\d+)$', info.get('RESOLUTION', ''))
			if resolution and 0 < int(resolution.group(1)) <= 4320:
				heights.add(int(resolution.group(1)))
		return heights

	def probe_available(self, video_id, language='en', deadline=0):
		"""List offered resolutions without restricting search to H.264/AAC.

		Native playback checks remain separate. Merge the observed metadata;
		an alternative client must not hide a quality seen by the first client.
		"""
		self._probe_deadline = min(deadline or float('inf'), monotonic() + 12)
		heights, verified, failed = set(), False, False
		self.probe_source = None
		manifests = {}
		def inspect(response):
			if (self.try_get(response, ('videoDetails', 'videoId')) != video_id
					or self.try_get(response, ('playabilityStatus', 'status')) not in (None, 'OK')):
				raise RuntimeError('Quality response is not usable')
			streaming = response.get('streamingData') or {}
			observed = self._available_format_heights(streaming)
			if streaming.get('hlsManifestUrl') and (not self.exact_quality or self.exact_quality not in observed):
				manifest_url = streaming['hlsManifestUrl']
				if manifest_url not in manifests:
					manifests[manifest_url] = self._available_hls_heights(manifest_url)
				observed.update(manifests[manifest_url])
			if not observed:
				raise RuntimeError('Quality metadata is incomplete')
			return observed
		def watch_response():
			webpage = self._download_webpage('https://www.youtube.com/watch?v=%s&bpctr=9999999999&has_verified=1' % video_id)
			marker = search(r'(?:var\s+)?ytInitialPlayerResponse\s*=\s*', webpage)
			if not marker:
				raise RuntimeError('Web quality metadata not found')
			response, unused_end = JSONDecoder().raw_decode(webpage[marker.end():].lstrip())
			return response
		# Full watch-page metadata often includes high-resolution representations
		# missing from mobile responses. Playback keeps its existing client order.
		order = ('web', 3, 5) if self.exact_quality >= 1440 else (3, 5, 'web')
		preferred = getattr(self, 'probe_preferred_source', None)
		if preferred in order:
			order = (preferred,) + tuple(source for source in order if source != preferred)
		try:
			for source in order:
				try:
					if source == 'web':
						response = watch_response()
					else:
						response, unused_player = self._extract_player_response(video_id, None, source, language)
					observed = inspect(response)
					verified = True
					heights.update(observed)
				except Exception:
					failed = True
				if heights and (not self.exact_quality or self.exact_quality in heights):
					self.probe_source = source
					return sorted(heights.intersection((360, 480, 720, 1080, 1440, 2160)))
			if verified and not failed:
				return sorted(heights.intersection((360, 480, 720, 1080, 1440, 2160)))
			raise RuntimeError('Selected quality could not be verified')
		finally:
			self._probe_deadline = 0

	def _preferred_web_response(self, video_id):
		webpage = self._download_webpage(
			'https://www.youtube.com/watch?v=%s&bpctr=9999999999&has_verified=1' % video_id)
		marker = search(r'(?:var\s+)?ytInitialPlayerResponse\s*=\s*|window\["ytInitialPlayerResponse"\]\s*=\s*', webpage or '')
		if not marker:
			raise RuntimeError('Web player response not found')
		response, unused_end = JSONDecoder().raw_decode(webpage[marker.end():].lstrip())
		if not isinstance(response, dict):
			raise RuntimeError('Invalid web player response')
		streaming = response.get('streamingData') or {}
		formats = (streaming.get('formats') or []) + (streaming.get('adaptiveFormats') or [])
		player_id = None
		if any(fmt.get('signatureCipher') or search(r'(?:\?|&)n=[^&]+', fmt.get('url', '')) for fmt in formats):
			try:
				player_id = self._extract_player_info()
			except Exception:
				pass  # Other direct representations can still be usable.
		return response, player_id

	@staticmethod
	def _response_duration(response):
		values = [response.get('videoDetails', {}).get('lengthSeconds')]
		for fmt in ((response.get('streamingData') or {}).get('formats', []) +
				(response.get('streamingData') or {}).get('adaptiveFormats', [])):
			try:
				values.append(int(str(fmt.get('approxDurationMs', ''))) // 1000)
			except (TypeError, ValueError, OverflowError):
				pass
		for raw in values:
			try:
				duration = int(str(raw))
			except (TypeError, ValueError, OverflowError):
				continue
			if 0 < duration <= 31 * 24 * 60 * 60:
				return duration
		return 0

	def _preferred_extract(self, video_id, yt_auth, lang, stream_mode, audio_preference):
		"""Prefer the chosen height; otherwise keep the highest native input.

		Each candidate retains its own original video/audio URLs and player
		context. No streams are joined, converted or fetched during search.
		"""
		candidates, duration, age_restricted = [], 0, False

		def rank(candidate):
			return (candidate['height'] == self.preferred_quality,
				candidate['height'], candidate['codec'] == 'h264')

		def inspect(response, player_id):
			nonlocal duration, age_restricted
			if not isinstance(response, dict) or self.try_get(response, ('videoDetails', 'videoId')) != video_id:
				return
			status = response.get('playabilityStatus') or {}
			age_restricted |= status.get('status') == 'LOGIN_REQUIRED'
			if status.get('status', 'OK') != 'OK':
				return
			duration = max(duration, self._response_duration(response))
			streaming = response.get('streamingData') or {}
			formats = (streaming.get('formats') or []) + (streaming.get('adaptiveFormats') or [])
			choices = []
			if not self.try_get(response, ('videoDetails', 'isLive')):
				url, unused_itag = self._extract_fmt_video_format(formats, player_id, stream_mode, audio_preference)
				if url:
					choices.append({'url': url, 'height': self.selected_quality,
						'codec': self.selected_video_codec, 'itag': self.selected_video_itag,
						'audio_itag': self.selected_audio_itag})
			if (streaming.get('hlsManifestUrl')
					and not any(choice['height'] == self.preferred_quality for choice in choices)):
				try:
					for fmt in self._extract_from_m3u8(streaming['hlsManifestUrl'], 2160, stream_mode != 'compatible'):
						try:
							youtube_stream_parts(fmt['url'])
						except ValueError:
							continue
						choices.append(dict(fmt, audio_itag=''))
				except Exception:
					pass  # A bad manifest must not discard a resolved direct input.
			if choices:
				candidates.append(max(choices, key=rank))

		sources = ('web', 3, 5) if self.preferred_quality >= 1440 else (3, 'web', 5)
		for source in sources:
			try:
				if source == 'web':
					response, player_id = self._preferred_web_response(video_id)
				else:
					response, player_id = self._extract_player_response(video_id, None, source, lang)
				inspect(response, player_id)
			except Exception:
				continue
			if any(candidate['height'] == self.preferred_quality for candidate in candidates):
				break
		if not candidates and age_restricted:
			try:
				response, player_id, unused_live = self._load_player_response(video_id, yt_auth, lang)
				inspect(response, player_id)
			except Exception:
				pass
		if not candidates:
			raise RuntimeError('No supported original YouTube video and audio stream found')
		chosen = max(candidates, key=rank)
		self.selected_quality = chosen['height']
		self.selected_video_itag = chosen.get('itag', '')
		self.selected_video_codec = chosen['codec']
		self.selected_audio_itag = chosen.get('audio_itag', '')
		self.duration_seconds = duration
		return chosen['url']

	def _real_extract(self, video_id, yt_auth):
		url = ''
		self.selected_quality = 0
		self.selected_video_itag = self.selected_audio_itag = self.selected_video_codec = ''
		self.duration_seconds = 0
		lang = config.plugins.YouTube.searchLanguage.value
		stream_mode = config.plugins.YouTube.streamMode.value
		audio_preference = config.plugins.YouTube.audioPreference.value

		if stream_mode != 'compatible' and config.plugins.YouTube.useDashMP4.value:
			self.use_dash_mp4 = ()
		else:
			print('[YouTubeVideoUrl] skip DASH MP4 format')
			self.use_dash_mp4 = DASH_VIDEO_FORMAT

		if self.preferred_quality:
			return self._preferred_extract(video_id, yt_auth, lang, stream_mode, audio_preference)

		player_response, player_id, is_live = self._load_player_response(video_id, yt_auth, lang)

		# Preserve authoritative metadata when a native decoder does not expose
		# the duration until its separate video/audio inputs have opened.
		raw_duration = self.try_get(player_response, ('videoDetails', 'lengthSeconds'))
		try:
			duration = int(str(raw_duration))
		except (TypeError, ValueError, OverflowError):
			duration = 0
		if not 0 < duration <= 31 * 24 * 60 * 60:
			duration = 0
		self.duration_seconds = duration

		streaming_data = player_response.get('streamingData', {})
		if not self.duration_seconds:
			for fmt in (streaming_data.get('formats', []) +
						streaming_data.get('adaptiveFormats', [])):
				try:
					candidate = int(str(fmt.get('approxDurationMs', ''))) // 1000
				except (TypeError, ValueError, OverflowError):
					candidate = 0
				if 0 < candidate <= 31 * 24 * 60 * 60:
					self.duration_seconds = max(self.duration_seconds, candidate)
		streaming_formats = streaming_data.get('formats', [])

		# If priority format changed in config, recreate priority list
		if not PRIORITY_VIDEO_FORMAT or PRIORITY_VIDEO_FORMAT[0] != config.plugins.YouTube.maxResolution.value:
			create_priority_formats()

		if not is_live:
			streaming_formats = streaming_formats + streaming_data.get('adaptiveFormats', [])
			url, our_format = self._extract_fmt_video_format(
				streaming_formats, player_id, stream_mode, audio_preference)

		if not url:
			print('[YouTubeVideoUrl] Try manifest url')
			hls_manifest_url = streaming_data.get('hlsManifestUrl')
			if hls_manifest_url:
				for fmt in self._extract_from_m3u8(hls_manifest_url):
					if self.exact_quality and fmt.get('height') != self.exact_quality:
						continue
					url = fmt.get('url')
					self.selected_quality = fmt.get('height', 0)
					self.selected_video_itag = fmt.get('itag', '')
					self.selected_video_codec = fmt.get('codec', '')
					self.selected_audio_itag = ''
					print('[YouTubeVideoUrl] Found manifest url')
					break

		if not url:
			playability_status = player_response.get('playabilityStatus', {})
			reason = playability_status.get('reason') or 'No supported AAC or combined YouTube stream found'
			if reason:
				subreason = playability_status.get('messages')
				if subreason:
					if isinstance(subreason, list):
						subreason = subreason[0]
					reason += '\n%s' % subreason
			raise RuntimeError(reason)

		return str(url)

	def extract(self, video_id, yt_auth=None):
		error_message = None
		for _ in range(3):
			try:
				return self._real_extract(video_id, yt_auth)
			except Exception as ex:
				if ex is None:
					print('No supported formats found, trying again!')
				else:
					error_message = str(ex)
					break
		if not error_message:
			error_message = 'No supported formats found in video info!'
		raise RuntimeError(error_message)
