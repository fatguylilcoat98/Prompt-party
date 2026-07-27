# Prompt Party

Standalone live audience-controlled AI party-game platform for Twitch and
browser play. Five game modules — AI Art Showdown, AI Rap Battle, AI Court,
AI Improv, AI Roast Battle — run on **one reusable game engine**.

> Build principle: reliability first, audience agency second, chaos only
> through controlled game objects.

**Separation rule:** Prompt Party is fully independent of Team Talk —
separate repository, service, port (`8710`), configuration prefix
(`PROMPT_PARTY_`), database (`./data/prompt_party.db`), media directory,
UI, and deployment. It imports nothing from Team Talk.

## Source of truth

| Document | Governs |
| --- | --- |
| `docs/00_Master_Product_and_Integration_Spec.pdf` | Shared architecture |
| `docs/01_AI_Art_Showdown.pdf` … `docs/05_AI_Roast_Battle.pdf` | Game-specific behavior |
| `docs/Prompt_Party_Complete_Build_Binder.pdf` | Combined binder |
| `references/` | Visual direction (see `references/README.md`) |

## Quickstart

```bash
make install    # create .venv and install dependencies
make test       # run the test suite
make dev        # start the development server (http://127.0.0.1:8710)
cp .env.example .env   # then edit configuration as needed
```

Health check: `GET /api/health`.

## Repository map

```
prompt-party/
├── app/
│   ├── main.py               # FastAPI application factory
│   ├── config.py             # PROMPT_PARTY_-prefixed settings, secret masking
│   ├── api/                  # HTTP surface (health; show/round APIs in M2)
│   ├── controller/           # game-state controller (Milestone 2)
│   ├── audience/             # room join, submissions, voting (M2/M3)
│   ├── broadcast/            # broadcast state + SSE event stream (M2/M3)
│   ├── producer/             # producer authority and controls (M2)
│   ├── moderation/           # submission screening queue (M2/M3)
│   ├── events/               # event bus with public/private separation
│   ├── providers/            # provider interface, registry, mock adapters
│   ├── persistence/          # SQLAlchemy models for all 11 spec ledgers
│   └── games/
│       ├── shared/           # 12-phase lifecycle, data contracts, GameModule protocol
│       ├── art_showdown/     # Milestone 3
│       ├── rap_battle/       # Milestone 5
│       ├── ai_court/         # Milestone 5
│       ├── improv/           # Milestone 5
│       └── roast_battle/     # Milestone 5
├── web/                      # broadcast / audience / producer / replay views
├── tests/                    # pytest suite
├── config/                   # cast/persona configuration examples
├── docs/                     # specification packets (source of truth)
├── references/               # visual direction
├── media/                    # generated media (gitignored, separate from Team Talk)
├── exports/                  # replay/round exports (gitignored)
├── scripts/dev.sh            # development startup command
└── .env.example              # environment-variable example file
```

## Architecture decisions (Milestone 1)

- **Language/stack:** Python 3.11+, FastAPI, Pydantic v2. The Master Spec
  defines the `GameModule` contract as a Python `Protocol` (section 6), so
  the engine is Python.
- **Database:** SQLite via SQLAlchemy 2.0 (portable types; PostgreSQL is a
  `DATABASE_URL` change). All eleven ledgers from Master Spec section 11
  exist as tables; the `events` table carries a monotonic `seq` for
  replay-grade ordering.
- **Event stream:** append-only event ledger + in-process pub/sub bus with
  server-side public/private filtering; delivered to clients over SSE
  (Master Spec section 10, "SSE preferred for v1"). SSE endpoint lands in
  Milestone 2/3 alongside broadcast state.
- **Providers:** every AI call goes through `ProviderAdapter`; adapters
  return status objects (never raise raw errors upward), and deterministic
  mocks make all five games testable with zero external API calls.
- **Authority:** only the controller or an authorized producer action
  advances state (enforced in Milestone 2's transition engine); the phase
  lifecycle, envelope, and modifier contracts here are its foundations.

## Milestone status

- [x] M1 — Repository foundation
- [x] **M2 — Shared engine** (controller, validated transitions, producer
  authority, pause/resume, event persistence, failure recording,
  command idempotency)
- [ ] M3 — AI Art Showdown vertical slice (mock providers)
- [ ] M4 — Real provider adapters
- [ ] M5 — Rap Battle, Court, Improv, Roast Battle

## Deviations from specification

1. `references/flint_show_visual_show.html` was not supplied; the Master
   Spec's surface descriptions substitute until it is provided
   (`references/README.md`).
2. The spec's `ai_requests/responses` ledger is one table (`ai_requests`)
   holding request and response outcome per row; all required minimum
   contents are present.
3. The lifecycle row "SCORING / WINNER / POST-ROUND" is a single phase
   (`SCORING`) covering calculate → announce → persist → reset, keeping the
   lifecycle at exactly twelve phases.
4. A `commands` table was added beyond the §11 minimum list to satisfy the
   build-order item 8 / §15 requirement that duplicate commands are
   rejected; §11 is a minimum, not a ceiling.
5. Show pause is show-scoped: while paused, all round transitions are
   blocked. The spec does not define per-round pause; this is the smallest
   safe interpretation of the producer pause control.
