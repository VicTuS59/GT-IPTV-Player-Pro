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

from .config import config

from .compat import compat_parse_qs
from .compat import compat_Request
from .compat import compat_urlopen
from .compat import compat_URLError
from .compat import SUBURI
from .jsinterp import JSInterpreter


IGNORE_VIDEO_FORMAT = (
	'43', '44', '45', '46',  # webm
	'82', '83', '84', '85',  # 3D
	'100', '101', '102',  # 3D
	'167', '168', '169',  # webm
	'170', '171', '172',  # webm
	'218', '219',  # webm
	'242', '243', '244', '245', '246', '247',  # webm
	'394', '395', '396', '397', '398', '399', '400', '401', '402', '694', '695', '696', '697', '698', '699', '700', '701', '571',  # AV1
	'249', '250', '251',  # webm
	'302'  # webm
)

PRIORITY_VIDEO_FORMAT = ()
# These are video-only MP4 representations. The paired audio must be a
# separate AAC track, just as in E2iPlayer's default MP4 DASH selection.
DASH_VIDEO_FORMAT = frozenset((
	'133', '134', '135', '136', '137', '138', '160',
	'212', '229', '230', '231', '232', '248', '264',
	'271', '272', '266', '269', '270', '298', '299',
	'303', '313', '315', '308'
))
DASH_AUDIO_FORMAT = ('140', '141', '139')

# Use the response's height when present.  Older YouTube responses sometimes
# omit it, so retain the established itag groups as a bounded fallback.
VIDEO_FORMAT_GROUPS = (
	(144, ('17', '91', '13', '151', '160', '269')),
	(240, ('5', '36', '92', '132', '133', '229')),
	(360, ('18', '93', '34', '6', '134', '230')),
	(480, ('35', '59', '78', '94', '135', '212', '231')),
	(720, ('22', '95', '300', '136', '298', '232')),
	(1080, ('37', '96', '301', '137', '299', '248', '303', '271', '270')),
	(2160, ('38', '266', '264', '138', '313', '315', '272', '308')),
)
VIDEO_FORMAT_HEIGHT = {itag: height for height, group in VIDEO_FORMAT_GROUPS for itag in group}
RESOLUTION_LIMIT = {'18': 360, '35': 480, '22': 720, '37': 1080, '38': 2160}


def _format_height(fmt):
	try:
		height = int(fmt.get('height') or 0)
	except (TypeError, ValueError, OverflowError):
		height = 0
	return height if 0 < height <= 4320 else VIDEO_FORMAT_HEIGHT.get(str(fmt.get('itag', '')), 0)


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
		self.selected_quality = 0
		self.selected_video_itag = ''
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
			urlh = compat_urlopen(url, timeout=5)
		except compat_URLError as e:  # pragma: no cover
			raise RuntimeError(e.reason)

		content_type = urlh.headers.get('Content-Type', '')
		webpage_bytes = urlh.read()
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
		n_param = search(r'&n=(.+?)&', url).group(1)
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
			print('[YouTubeVideoUrl] Decrypted nsig %s => %s' % self.nsig_cache)
			return url.replace(self.nsig_cache[0], self.nsig_cache[1])
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
					audio_urls[audio_info.get('GROUP-ID')] = audio_info.get('URI')
		return audio_urls

	@staticmethod
	def _url_map_append(url_map, itag, url):
		height = VIDEO_FORMAT_HEIGHT.get(str(itag), 0)
		limit = RESOLUTION_LIMIT.get(str(config.plugins.YouTube.maxResolution.value), 720)
		if itag not in IGNORE_VIDEO_FORMAT and height and height <= limit:
			url_map.append({
				'url': url,
				'itag': itag,
				'height': height,
				'preference': PRIORITY_VIDEO_FORMAT.index(itag) if itag in PRIORITY_VIDEO_FORMAT else 100
			})

	def _extract_from_m3u8(self, manifest_url):
		url_map = []
		audio_url = ''

		manifest = self._download_webpage(manifest_url)
		audio_urls = self._get_m3u8_audio_urls(manifest)

		for line in manifest.splitlines():
			if audio_urls and line.startswith('#EXT-X-STREAM-INF:'):
				audio_id = self._parse_m3u8_attributes(line).get('AUDIO')
				if audio_id and audio_id in audio_urls:
					audio_url = SUBURI + audio_urls.get(audio_id)
			elif line.startswith('https'):
				itag = search(r'/sgovp/[^/]+itag%3D(\d+?)/', line) or search(r'/itag/(\d+?)/', line)
				if itag:
					self._url_map_append(url_map, itag.group(1), line + audio_url)
					audio_url = ''
		return sorted(url_map, key=lambda k: k['preference'])

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
			if '&n=' in url:
				url = self._unthrottle_url(url, player_id)
			return url

	@staticmethod
	def _video_pref(fmt, prefer):
		if prefer == 100:
			prefer = 20 if 'video/mp4' in (fmt.get('mimeType') or '').lower() else 200
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
		limit = RESOLUTION_LIMIT.get(str(config.plugins.YouTube.maxResolution.value), 720)
		if stream_mode == 'dash':
			formats.sort(key=lambda fmt: (str(fmt.get('itag')) not in DASH_VIDEO_FORMAT, fmt['preference']))
		for fmt in formats:
			itag = str(fmt.get('itag', ''))
			height = _format_height(fmt)
			if not height or height > limit:
				continue
			mime = (fmt.get('mimeType') or '').lower()
			# VP9, AV1, HEVC and WebM cannot be assumed to have a working
			# hardware decoder on Enigma2 boxes. Only accept H.264 MP4 here.
			if mime and (not mime.startswith('video/mp4') or ('codecs=' in mime and 'avc1' not in mime)):
				continue
			if not mime and itag not in ('18', '22', '37', '38'):
				continue
			separate = itag in DASH_VIDEO_FORMAT
			if separate and stream_mode == 'compatible':
				continue
			if not separate and mime and 'codecs=' in mime and 'mp4a' not in mime:
				continue
			audio_url, audio_fmt = ('', None)
			if separate:
				audio_url, audio_fmt = self._extract_dash_audio_format(
					streaming_formats, player_id, audio_preference)
				if not audio_url:
					# Never send a silent video-only representation to the player.
					continue
			url = self._extract_url(fmt, player_id)
			if url:
				self.selected_quality = height
				self.selected_video_itag = itag
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
			url = self._extract_url(fmt, player_id)
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

	def _real_extract(self, video_id, yt_auth):
		url = ''
		self.duration_seconds = 0
		lang = config.plugins.YouTube.searchLanguage.value
		stream_mode = config.plugins.YouTube.streamMode.value
		audio_preference = config.plugins.YouTube.audioPreference.value

		if stream_mode != 'compatible' and config.plugins.YouTube.useDashMP4.value:
			self.use_dash_mp4 = ()
		else:
			print('[YouTubeVideoUrl] skip DASH MP4 format')
			self.use_dash_mp4 = DASH_VIDEO_FORMAT

		player_response, player_id = self._extract_player_response(video_id, None, 3, lang)
		if not player_response:
			raise RuntimeError('Player response not found!')

		is_live = self.try_get(player_response, ('videoDetails', 'isLive'))

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

		# The loopback remux cannot publish an HTTP content length. Preserve the
		# authoritative YouTube duration separately so the GT player can render
		# real elapsed/total time and restart FFmpeg at an explicit seek target.
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
					url = fmt.get('url')
					self.selected_quality = fmt.get('height', 0)
					self.selected_video_itag = fmt.get('itag', '')
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
