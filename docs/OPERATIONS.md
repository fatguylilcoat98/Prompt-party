# Operating Prompt Party

## Show-night checklist

1. **Health**: open `/admin/` — everything green? Providers healthy?
2. **Rehearse the pipeline** (optional): with providers on `mock`, run one
   fast round end to end. Mocks cost nothing and exercise every phase.
3. **Producer console** (`/producer/`): create the show, start it, create
   the first round with the game's default cast.
4. **OBS**: browser source `https://your-host/broadcast/?show=<id>`,
   1920×1080. The view is 16:9 and has no controls on it.
5. **Audience**: share `/audience/?show=<id>` in chat. Viewers join with
   a nickname; everything they send lands in your moderation queue.
6. During the round, the console's phase bar is your run of show:
   advance phases left to right; every AI action is a button; every
   power-up goes through the approved-modifier panel.

## The controls that matter under pressure

| Situation | Control |
| --- | --- |
| Anything weird on stage | **Pause** (blocks all round transitions) |
| Image/verse generation failed | **Retry failed generation** — retries only failures, never duplicates a success |
| A submission is borderline | Edit+Approve in the moderation queue, or Reject |
| A judge is stuck | Request scores from the other judges; scoring reweights valid judges |
| Tie on the scoreboard | Run your sudden-death bit, then **Override / sudden death** with a reason (audited) |
| Show must stop NOW | **EMERGENCY STOP** — ends the show, audited, nothing else fires |

Producer overrides are always recorded in the event ledger with your
reason — the replay shows exactly what was overridden and why.

## Monitoring

- `/admin/` — live dashboard: uptime, memory, connected viewers, active
  shows, provider latency, storage, recent incidents.
- `journalctl -u prompt-party -f` (systemd) or
  `docker compose logs -f prompt-party` (Docker).
- `GET /api/health` — machine-readable liveness for uptime monitors.

## Routine maintenance

- **Backups** run nightly via cron (`scripts/backup.sh`); check
  `backups/` occasionally and after any big show.
- **Media growth**: generated images accumulate in `media/`. The admin
  page shows storage usage. Old rounds' media can be archived after the
  backup has captured it.
- **Updates**: see DEPLOY.md → Updating. Run `make test` (dev machine)
  before deploying an update on show day.

## Known operational limits (v1)

- One uvicorn worker only (SQLite + in-process SSE hub). Don't scale
  workers; the single process handles a live show comfortably.
- SSE reconnects resume exactly via `Last-Event-ID`; a viewer who was
  offline simply catches up from the ledger.
- Voting closes when *you* close it. The on-screen countdown is pacing
  for the audience, not an automatic close.
