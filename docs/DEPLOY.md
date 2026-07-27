# Deploying Prompt Party on your Ubuntu home server

Reproducible from a clean machine. Two supported paths — **Docker
(recommended)** or **bare metal with systemd**. Both end with the same
result: Prompt Party at `http://127.0.0.1:8710` behind Caddy, running as
its own service with its own data directory.

> **One process, always.** Prompt Party runs as a single worker: SQLite
> and the in-process SSE hub are per-process. Never set `--workers > 1`.
> A single worker comfortably handles a live show with hundreds of
> audience connections.

## Path A — Docker (recommended)

```bash
# 1. Prerequisites (fresh Ubuntu)
sudo apt update && sudo apt install -y git docker.io docker-compose-v2
sudo usermod -aG docker $USER   # log out/in afterwards

# 2. Get the code
git clone https://github.com/fatguylilcoat98/Prompt-party.git /opt/prompt-party
cd /opt/prompt-party

# 3. Configure
cp .env.production.example .env
python3 -c "import secrets; print(secrets.token_urlsafe(32))"   # producer token
nano .env    # paste the token into PROMPT_PARTY_PRODUCER_TOKEN

# 4. Edit the reverse proxy
nano deploy/Caddyfile   # set your domain, or use the LAN-only block

# 5. Launch
docker compose up -d --build

# 6. Verify
curl -s http://127.0.0.1:8710/api/health
```

Persistent storage lives in `./data` (database), `./media` (generated
images/audio), and `./exports` — all bind mounts that survive rebuilds.
Logs rotate automatically (json-file driver, 20 MB × 5).

## Path B — bare metal + systemd

```bash
# 1. Prerequisites
sudo apt update && sudo apt install -y git python3.11-venv caddy

# 2. Code + dedicated user
sudo useradd --system --create-home promptparty
sudo git clone https://github.com/fatguylilcoat98/Prompt-party.git /opt/prompt-party
cd /opt/prompt-party
sudo python3 -m venv .venv
sudo .venv/bin/pip install .
sudo mkdir -p data media exports
sudo chown -R promptparty:promptparty /opt/prompt-party

# 3. Configure
sudo cp .env.production.example .env && sudo nano .env   # set the token

# 4. Service
sudo cp deploy/prompt-party.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now prompt-party
systemctl status prompt-party

# 5. Reverse proxy
sudo cp deploy/Caddyfile /etc/caddy/Caddyfile   # edit domain first
sudo systemctl reload caddy
```

Logs go to journald (`journalctl -u prompt-party -f`). If you switch the
unit to file logging, install `deploy/logrotate.conf` to
`/etc/logrotate.d/prompt-party`.

## Exposure model

| Surface | Who | How |
| --- | --- | --- |
| `/audience/` | The public (your viewers) | Through Caddy |
| `/broadcast/` | OBS on your streaming PC | LAN or Caddy |
| `/producer/`, `/admin/` | You | Caddy + basic auth + producer token |
| Port 8710 | Nobody directly | Bound to 127.0.0.1 |

If you'd rather not open router ports at all, run a Cloudflare Tunnel to
`127.0.0.1:8710` instead of Caddy — everything else stays the same.

## Real AI providers

Rehearse on mocks (default — zero external calls). For live shows, point
the `.env` provider block at any OpenAI-compatible endpoint:

```
PROMPT_PARTY_TEXT_PROVIDER=openai_compatible
PROMPT_PARTY_TEXT_PROVIDER_BASE_URL=http://localhost:11434/v1   # Ollama
PROMPT_PARTY_TEXT_PROVIDER_MODEL=llama3.1
```

Then reference `openai_text` / `openai_image` as the provider in cast
entries (the producer console's cast editor). Restart the service after
`.env` changes.

## First show (no terminal needed after install)

1. Open `https://your-domain/producer/`, paste the producer token, Connect.
2. Create show → Start → pick a game → Create round.
3. Share `https://your-domain/audience/?show=<show_id>` with viewers
   (the console shows the link).
4. Add `https://your-domain/broadcast/?show=<show_id>` as an OBS browser
   source at 1920×1080.
5. Run the round from the console; watch `docs/OPERATIONS.md` for the
   show-night checklist.

## Backups

```bash
sudo crontab -u promptparty -e
# 30 2 * * * /opt/prompt-party/scripts/backup.sh /opt/prompt-party/backups
```

See `docs/RECOVERY.md` for restores.

## Updating

```bash
cd /opt/prompt-party && git pull
docker compose up -d --build          # Docker path
# or:
sudo -u promptparty .venv/bin/pip install . && sudo systemctl restart prompt-party
```
