# Signed remote updates — 1.8.6-r0

## Updating a receiver

Version 1.8.6-r0 removes the automatic pre-update backup step. Backup errors
no longer block updates. Manual backup and restore remain available in Settings.
If an update fails, the failed stage and reason are displayed in the receiver's
language across all 43 UI catalogues, including connection, storage, missing
package, and active recording/download conditions.

Users whose older updater is blocked by a pre-update backup error must install
1.8.6-r0 manually once, then restart the Enigma2 interface. After that, signed
remote updates remain available in **Settings > Software update**.

Receivers use the public signed `updates/stable.json` pointer. Automatic checks
are enabled by default and start in a background worker immediately when the
plugin opens. Each new launch refreshes metadata even if the preceding check
was less than six hours ago. Welcome and dashboard screens share the same
in-flight check, and subsequent checks run every six hours while the plugin
remains open. Disabling automatic checks prevents these network requests.
Users can check immediately in **Settings > Software update**, using GREEN.
Download, installation and interface restart require the user's actions/consent.

## Publishing a release

Use the existing `VicTuS59/GT-IPTV-Player-Pro` repository and the `main` branch.
Keep `GTIPTVPlayerPro/update_trust.py` and `update-public-key.pem` compatible
with installed receivers. Store the matching private PEM key only in the
`GTIPTV_UPDATE_SIGNING_KEY` repository Actions secret and a secure offline backup.
Never commit a private key or include one in release assets.

1. Prepare the source version and approved English release notes. For this release:
   `python3 -B scripts/prepare-release.py --version 1.8.6-r0 --notes-file RELEASE-NOTES.txt`.
   Future releases must use a higher version.
2. Review and commit the source/version/description changes to `main`.
3. Run **Actions > Publish signed IPTV update > Run workflow**, using `main`.
4. The workflow runs the offline updater checks, builds an IPK, signs its metadata,
   publishes a normal release tagged `vX.Y.Z-rN`, verifies all three release assets,
   and advances `updates/stable.json` on `main`.

For 1.8.6-r0, the assets are
`enigma2-plugin-extensions-gtiptvplayerpro_1.8.6-r0_all.ipk`, its `.sha256` file
and `update-stable.json`. The release description and signed update notes come
from `RELEASE-NOTES.txt` and must match the release summary in `README.md`.
Verify the signed metadata version, package URL, size and SHA-256 against the
published IPK before advancing the remote update pointer. A published
version must never be replaced with different package bytes; use a higher version.
The release-pointer job also verifies normal releases published through GitHub's UI.

The publisher uses the repository `GITHUB_TOKEN` with `contents: write`. Repository
rules must permit its signed-pointer commit. Existing releases and Git history stay
available. Public offline test fixtures use a separate test trust root, and the
production receiver rejects those example packages.
