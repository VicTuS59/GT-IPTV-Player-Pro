# GT IPTV Player Pro 1.7.0-r108

Fixed update installation on receivers without a mounted backup drive.

The updater honors the configured backup folder and falls back to private internal storage when the drive is unavailable or full. A safety backup is still required before installation. Existing installations with the old updater may need a one-time manual installation on receivers without a backup drive.

Signed stable updates: Settings > Software update. Publishing: [UPDATING.md](UPDATING.md). Offline checks: `python3 -B tests/test-updater.py` and `python3 -B tests/test-update-backup.py`.
