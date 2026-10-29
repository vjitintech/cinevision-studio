# Privacy and data handling

## Where processing happens
* **Local mode (default):** the app, database, encrypted files and all analysis run on the owner's machine.
  In Docker Compose the API has no route to the internet (internal network) and the media worker has no network
  at all. Caddy listens on `127.0.0.1` only.
* **Private-server mode:** the same components on a server you control. Treat it as reachable by whoever can
  reach that server; put it behind your own VPN or firewall. It is not "offline".

## Subprocessors
None by default. Optional adapters, each off until configured **and** consented to per provider:

| Adapter | Receives | Never receives |
|---|---|---|
| Ollama (local LLM) | refinement command text, concept text | footage, frames, filenames, account data |
| External OpenAI-compatible LLM | the same text | the same exclusions; review the provider's retention/training terms before consenting |
| ClamAV (local) | the uploaded file, inside your deployment | — |

There is no telemetry, analytics or crash reporting. Footage is never used to train any model — the code has no
training path.

## Retention
| Data | Default | Configurable |
|---|---|---|
| Uploaded clip, proxy, frames, analysis, concepts, plans, shot lists | until you delete the project | auto-delete after N days untouched (Security & privacy) |
| Plaintext scratch files during processing | deleted when each job ends; crash leftovers after 24 h | 1–720 h |
| Sessions | 2 h idle / 12 h absolute | env |
| Audit log | 365 days | code constant |
| Backups (`scripts/backup.py`) | 30 days, minimum 3 kept | `--keep-days`, `--keep-min` |

## Deletion
Deleting a project cancels its jobs, deletes every row (clip, metadata, frames, analysis, concepts and revisions,
camera and lighting plans, storyboards, animatics, 3D scenes, shot lists) and destroys each file's wrapped data
key before removing the file, so remnants on disk are unreadable. Existing backups still contain the project until
they age out; delete specific backups manually if you need immediate removal everywhere.

## Keys
`deploy/secrets/master_key` encrypts data at rest; `deploy/secrets/backup_key` encrypts backups. Keep both outside
the data volume and keep offline copies. Losing the master key makes stored footage unrecoverable by design.
