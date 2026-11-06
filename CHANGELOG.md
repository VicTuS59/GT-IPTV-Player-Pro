# Change log

## 1.9.1-r0

* Fixed compatibility-related crashes.

## 1.9.0-r0

- Redesigned the Movies and Series pages for faster, more stable browsing.
- Removed the Live TV recording feature to address buffering during live TV playback.
- Made extensive performance improvements throughout the plugin.
- Improved YouTube stability.

## 1.8.6-r0

- Remove the mandatory pre-update backup while retaining manual backup/restore.
- Preserve typed update failures through the UI, with translated explanations
  in all 43 catalogues and an error dialog after native screen handoff.
- Distinguish network, DNS, TLS, HTTP, storage, active recording/download and
  package-manager failures; report required/free space and missing dependencies.
- Fix CH+/CH− release-note paging across OpenPLi and OpenATV action contexts.
- Budget one unpacked package plus working space, tolerate nonessential cache
  cleanup failures, and distinguish installed packages from status-save failures.

## 1.4.0 — 1.4.0-r0

This release promotes the reviewed 1.3.0-r56 runtime to 1.4.0.

- Add selectable backup and restore dashboards for accounts, settings,
  favourites, history, subtitles and local source documents. Show section sizes,
  the selected total, storage path and available space.
- Verify backup contents and create a safety backup before restoring selected
  sections. Preserve unselected data and omit videos, plugin files and
  rebuildable caches from new backups.
- Improve restores across receivers with different disk layouts. Recover local
  source documents to private persistent storage when their original disk is
  unavailable, and do not mistake a small `/media` mount container for a disk.
- Add a multi-day EPG guide with programme details and continuing live preview,
  subject to the information provided by the source.
- Recall the previous Live TV channel with a short press of **0** across
  Xtream, M3U and Stalker sources. Keep channel history across live categories
  and return recalled playback to its channel list. Hold **0** to restart.
- Redesign the compact information bar. Improve source and technical
  information, playback times, remaining time, progress, weather and shortcuts.
  Correct native text blending while preserving the lightly transparent panel.
- Show the receiver's incoming network rate in Mbps, sampled once per second
  while the information bar is visible. This includes other receiver traffic.
- Prepare enabled weather data when the plugin opens and share it with player
  screens, including YouTube.
- Improve page responsiveness with RAM-aware UHD artwork caching, cached and
  background-loaded favourites, fewer redundant artwork updates, and immediate
  provider details while TMDb metadata is being completed.
- Correct duplicate navigation in movie discovery and single-press VOD seeking
  on OpenATV 8.0.1. Address the identified OpenPLi startup and native stream-info
  crash paths.
- Continue the download queue after an individual connection/server failure,
  while respecting explicit pause/cancel operations and storage failures.
- Align automatic and manual account-test results, distinguish transient
  failures from inactive accounts, and improve inactive-account review.
- Accept additional portal/MAC/expiry text-file formats and preserve imported
  expiry dates during account-file rewrites.
- Improve movie/series metadata language preference and original/default
  YouTube audio selection, including native AAC bitrate ranking.
- Extend all new interface messages to the existing 43 device languages and
  improve Live TV text-size handling across UHD, HD and SD layouts.

Restart the Enigma2 GUI after installation.
