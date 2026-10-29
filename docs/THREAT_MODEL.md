# Threat model

## Assets
1. Original footage and derived media (frames, proxies) — confidential IP.
2. Project data: titles, director briefs, analyses, concepts, shot lists.
3. Credentials: passwords, TOTP secrets, session tokens, encryption keys.
4. Audit trail integrity.

## Trust boundaries
Browser ↔ reverse proxy (TLS) ↔ API ↔ {SQLite, blob store} ↔ worker ↔ {ffmpeg, OpenCV} ↔ (optional) external
AI provider. The worker treats every uploaded file as hostile input to complex parsers.

## Adversaries
* **A1 Network attacker** on the LAN/Wi-Fi.
* **A2 Another account holder** (if registration is opened) trying to reach someone else's projects.
* **A3 Malicious web page** in the owner's browser (CSRF, clickjacking, XSS via stored text).
* **A4 Malicious media file** (crafted container exploiting the decoder, zip-bomb style resource exhaustion).
* **A5 Disk/backup theft** — attacker obtains the data volume or a backup.
* **A6 Credential attacker** — password guessing/stuffing.
* **A7 Supply chain** — vulnerable dependency.
* **A8 Over-sharing by the app itself** — footage sent to an AI vendor without consent, logs leaking URLs.

## STRIDE analysis

| Threat | Vector | Mitigation (implemented unless stated) |
|---|---|---|
| Spoofing | password guessing (A6) | scrypt (N=2^15,r=8,p=1); 12-char minimum + common-password block; per-IP and per-account rate limits; temporary lockout after 10 failures; optional TOTP + one-time recovery codes; constant-time compares; uniform login error |
| Spoofing | session theft (A1/A3) | 256-bit random token, only its SHA-256 stored; HttpOnly, SameSite=Strict, Secure under TLS; idle (2 h) and absolute (12 h) expiry; logout-all; rotation on login and privilege change |
| Tampering | CSRF (A3) | per-session CSRF token header + Origin check on every unsafe method; SameSite=Strict |
| Tampering | ciphertext swap / DB edits (A5) | AES-GCM with AAD bound to table/column/row id |
| Repudiation | denial of exports/deletions | append-only audit events for auth, MFA, media token issue, export, consent changes, deletion |
| Info disclosure | IDOR (A2) | UUIDv4 ids; every query owner-scoped; foreign rows return 404; tested in `tests/test_authz.py` |
| Info disclosure | XSS (A3) | React escapes text; no `dangerouslySetInnerHTML`; strict CSP (`default-src 'self'`, no inline script, `frame-ancestors 'none'`), `X-Content-Type-Options: nosniff` |
| Info disclosure | media URL leakage (A8) | no public URLs; media requires session cookie **and** a ≤5-min HMAC token bound to user+session+blob; query strings stripped from logs; `Referrer-Policy: no-referrer`; `Cache-Control: no-store` |
| Info disclosure | disk/backup theft (A5) | all files AES-256-GCM with per-file DEKs wrapped by a KEK derived (HKDF) from a master key kept outside the data volume; sensitive DB columns encrypted; backups encrypted with a **separate** backup key |
| Info disclosure | third-party AI (A8) | external adapters disabled by default; require server config **and** per-provider consent; local mode rejects non-loopback endpoints; footage never used for training (no training code path exists) |
| Info disclosure | SSRF | the server never fetches user-supplied URLs; adapter endpoints come only from server config and are validated |
| Info disclosure | path traversal | file paths derived only from server-generated UUIDs, resolved and checked against the storage root; client filenames are only stored (encrypted) for display |
| DoS | huge / long / bomb media (A4) | streamed byte cap, duration/resolution/codec allow-list, ffmpeg/ffprobe timeouts and rlimits (CPU, address space, file size, no core dumps), frame-count cap, one clip per project |
| Elevation | decoder exploit (A4) | decoding only in the worker, which in Compose runs as non-root with `network_mode: none`, read-only root FS and dropped capabilities; `-protocol_whitelist file`; optional ClamAV |
| Supply chain (A7) | vulnerable deps | pinned versions; CI workflow runs `pip-audit` and `npm audit` (`.github/workflows/security.yml`) |

## Residual risks / out of scope
* A compromised host OS or browser defeats all of the above.
* SQLite with column-level encryption leaves non-sensitive metadata (timestamps, sizes, codec) in clear.
* Deleted files on SSDs may persist physically; crypto-shredding (destroying the wrapped DEK) makes them
  unreadable.
* The local TLS certificate is self-signed via Caddy's internal CA; the owner must trust it once.
