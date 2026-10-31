# Publishing signed updates

Use the existing `VicTuS59/GT-IPTV-Player-Pro` repository and the `main` branch.
Keep `GTIPTVPlayerPro/update_trust.py` and `update-public-key.pem` compatible
with installed receivers. Store the matching private PEM key only in the
`GTIPTV_UPDATE_SIGNING_KEY` repository Actions secret and a secure offline backup.
Never commit a private key or include one in release assets.

1. Prepare a higher version, for example:
   `python3 -B scripts/prepare-release.py --version 1.7.0-r111 --notes-file RELEASE-DESCRIPTION.txt`.
2. Review and commit the source/version/description changes to `main`.
3. Run **Actions > Publish signed IPTV update > Run workflow**, using `main`.
4. The workflow runs the offline updater checks, builds an IPK, signs its metadata,
   publishes a normal release tagged `vX.Y.Z-rN`, verifies all three release assets,
   and advances `updates/stable.json` on `main`.

The assets are the IPK, its `.sha256` file and `update-stable.json`. The release
description and signed update notes come from `RELEASE-NOTES.txt`. A published
version must never be replaced with different package bytes; use a higher version.
The release-pointer job also verifies normal releases published through GitHub's UI.

Receivers use the public signed `updates/stable.json` pointer. Automatic checks
are enabled by default and limited to every six hours while the plugin is open.
Users can check immediately in **Settings > Software update**, using GREEN.
Download, installation and interface restart require the user's actions/consent.

The publisher uses the repository `GITHUB_TOKEN` with `contents: write`. Repository
rules must permit its signed-pointer commit. Existing releases and Git history stay
available. Public offline test fixtures use a separate test trust root, and the
production receiver rejects those example packages.
