# Recovery

## The app is down

```bash
systemctl status prompt-party          # or: docker compose ps
journalctl -u prompt-party -n 100      # or: docker compose logs --tail 100 prompt-party
sudo systemctl restart prompt-party    # or: docker compose restart prompt-party
curl -s http://127.0.0.1:8710/api/health
```

The service restarts automatically (`Restart=always` /
`restart: unless-stopped`); a crash loop points at configuration —
check `.env` first (a missing or malformed value fails fast at startup).

## Mid-show recovery

State lives in the database, not in memory. If the process restarts
during a show:

1. The show, round, phase, prompt, votes, and event ledger are intact.
2. Reopen `/producer/`, Connect, select the show — the console reattaches.
3. Broadcast and audience pages reconnect their event streams
   automatically (SSE `Last-Event-ID` resumes exactly; no events lost).
4. If a generation was in flight when the process died, its status shows
   failed → **Retry failed generation**.
5. If the phase is mid-step, the audited override jump gets you to the
   right phase.

## Restore from backup

```bash
sudo systemctl stop prompt-party            # or: docker compose stop prompt-party
cd /opt/prompt-party
cp backups/db-<STAMP>.sqlite data/prompt_party.db
tar -xzf backups/files-<STAMP>.tar.gz       # restores media/ and exports/
sudo chown -R promptparty:promptparty data media exports
sudo systemctl start prompt-party
```

The database snapshot is taken with SQLite's online-backup API, so it is
consistent even though it was captured while the app was live.

## Database integrity check

```bash
.venv/bin/python - <<'EOF'
import sqlite3
print(sqlite3.connect("data/prompt_party.db").execute("PRAGMA integrity_check").fetchone()[0])
EOF
```

`ok` means healthy. Anything else: stop the service and restore the most
recent snapshot (above).

## Disk full

Media is the usual culprit. Check `/admin/` → storage. Free space by
archiving old media (it is in every nightly `files-*.tar.gz`):

```bash
find media -type f -mtime +30 -delete       # after confirming backups exist
```

The database is append-mostly and small by comparison; never delete it
to free space — archive media instead.

## Lost producer token

Set a new one and restart:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
nano .env      # replace PROMPT_PARTY_PRODUCER_TOKEN
sudo systemctl restart prompt-party
```

Tokens are configuration, never stored in the database, so a rotation is
just a restart. Update the Caddy basic-auth hash too if you use it.

## Full rebuild from nothing

A clean machine + this repository + your latest two backup files
(`db-*.sqlite`, `files-*.tar.gz`) is a complete recovery:
follow `docs/DEPLOY.md`, then the restore steps above.
