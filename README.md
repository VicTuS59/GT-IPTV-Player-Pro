# GT IPTV Player Pro

GT IPTV Player Pro is an open-source, account-oriented IPTV browser and player
for Enigma2 receivers.

Current source version: **v1.4.0** — package **1.4.0-r0**.

[Download the latest release](https://github.com/VicTuS59/GT-IPTV-Player-Pro/releases/latest)
or read the [1.4.0 change log](CHANGELOG.md).

## Features

- Xtream Codes, M3U and Stalker/MAC Portal account support
- Dedicated Live TV, Movies and Series browsers
- EPG, picons and a fullscreen information bar
- Movie/series details and Continue Watching
- Device-language user interface with safe English fallback
- Open-Meteo weather and optional TMDb metadata integration
- Selectable backup and restore with section sizes, archive verification and a
  safety backup before restoring selected data
- Multi-day EPG guide with continuing live preview, using the information
  available from the selected provider
- Short **0** recalls the previous Live TV channel; hold **0** to restart the
  current live stream
- Redesigned information bar with weather, playback progress and the receiver's
  incoming network rate in Mbps
- Improved UHD image caching, favourites loading and image compatibility

Backups contain plugin user data and selected local source documents. Downloaded
videos, plugin code and rebuildable caches are excluded. The network indicator
shows incoming traffic on the selected receiver interface, including other
downloads; it is not a per-stream bitrate or an internet speed test.

## Requirements

- Enigma2
- Python 3.9 or newer

## Installation and removal

Release IPKs include `CONTROL/postrm`, which removes the complete plugin
runtime directory after `opkg remove`. Account and playlist data under
`/etc/enigma2/gtiptvplayer` is intentionally preserved for later reinstalls.
Restart the Enigma2 GUI after removal so its in-memory plugin menu is refreshed.

## Project links

- Source code: https://github.com/VicTuS59/GT-IPTV-Player-Pro
- Issue tracker: https://github.com/VicTuS59/GT-IPTV-Player-Pro/issues

The plugin does not provide channels, streams, accounts or playlists. Users are
responsible for using only services and content for which they have the
necessary access and usage rights.

## Privacy and external services

Open-Meteo receives the selected city/location only when weather is enabled.
TMDb receives a movie or series title and the user-supplied API key/token only
when TMDb metadata is enabled. IPTV account credentials are not sent to either
service.

## License

The source code and original project assets are distributed under
`GPL-2.0-or-later`. See `LICENSE`, `LICENSING.md` and
`ASSET_PROVENANCE.md`.
