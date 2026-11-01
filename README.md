# GT IPTV Player Pro 1.8.5-r0

★ **SQLite-free installation:** Removed the python3-sqlite3 package dependency and replaced the plugin's SQLite working caches with bounded JSON indexes. Existing resume positions, watched episodes and user settings are preserved.

★ **Live TV channel logos:** Added bounded recovery for failed logo loads and fixed incorrect logos after channel changes. Missing logo or EPG data can no longer inherit the first channel's data.

★ **Movie and series seeking:** Combine repeated seek requests, verify that playback advances, and attempt one bounded recovery using the same playback engine when seeking stalls. Intentional pauses and resume bookmarks are protected.

★ **Front panel displays:** Larger text and playback times, scrolling titles, and layouts that adapt to the display size.

★ **YouTube:** Fixed playback quality text overlapping search results.

★ **Update checks:** When automatic checks are enabled, the background check starts as soon as the plugin opens.

★ **Installation cleanup:** Improved legacy cache cleanup and corrected the misleading deferred count for temporary-directory aliases.

Signed stable updates: Settings > Software update. Publishing: [UPDATING.md](UPDATING.md).
