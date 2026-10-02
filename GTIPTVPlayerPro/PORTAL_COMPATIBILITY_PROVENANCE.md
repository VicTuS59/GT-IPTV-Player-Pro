# MAG compatibility profile provenance

Recorded: 2026-09-23. Current build: 1.1.0-r23.
Permission confirmation recorded: 2026-09-24.
Supporting statement and forum link recorded: 2026-09-24.

The MAG compatibility profile introduced in R13 was developed after reviewing
kiddac's EStalker implementation:

https://github.com/kiddac/EStalker/tree/94f7be4286de7a0cda905bfe21ae78e4ad06d354

The reference informed the complete MAG browser/profile values, device-field
hash formulas, challenge metrics and their encoding, and the matching
token/cookie exchange. R14 retains that profile and corrects the detection
of the short plain-text authorization rejection that previously prevented
the automatic compatibility attempt.
R15 retains that working connection fix and adds the owner-supplied
permission statement and forum link to the source comments and records.
R16 replaces the statement with the exact English text supplied by the
owner, keeping the same forum source and attribution.

This is not a claim of independent development of that compatibility
profile. On 2026-09-24, project owner VicTuS59 confirmed that permission had
been obtained from kiddac. The permission record is in
`ESTALKER_PERMISSION.md`, together with the English statement and direct
forum link supplied by the owner. The statement is reproduced as supplied,
without asserting that it is the original-language forum text or adding
license conditions. This notice does not alter ownership of third-party
material.

The project owner's instruction to defer publication remains in place.
Preparing R19 does not publish a release or update a public feed.

The receiver test has previously verified a category response using this
profile. Playback and other accounts require separate receiver verification.
