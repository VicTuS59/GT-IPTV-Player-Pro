# GT IPTV Player Pro 1.7.0-r110

Fixed update progress feedback and automatic interface restart confirmation.

Measured download/backup progress, bounded package-manager stage feedback, and an automatic restart question after the native screen becomes ready. Restart still requires user consent.

Offline checks: `python3 -B tests/test-updater.py`, `python3 -B tests/test-update-backup.py`, `python3 -B tests/test-update-feedback.py`. Publishing: [UPDATING.md](UPDATING.md).
