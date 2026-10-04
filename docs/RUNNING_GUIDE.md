# Contrail — Running, testing, and building

Every command below was actually run against this repository during the session that wrote
this guide (Windows 11, Git Bash + PowerShell, Docker Desktop, Python 3.11.15, Node
24.17.0). Expected output is what was actually observed, not guessed.

The project's own `Makefile` hardcodes a Windows-style venv layout
(`.venv/Scripts/...`) — on macOS/Linux, either adjust those paths to `.venv/bin/...`
yourself or skip `make` and run the equivalent commands directly, shown below for both.

## 1. Clone and enter the project

```bash
git clone <repo-url> Flagship-Project
cd Flagship-Project
```

## 2. Install dependencies

**Windows (PowerShell or Git Bash):**
```bash
uv venv .venv
uv pip install --python .venv -e ".[dev,ml]"
```

**macOS / Linux:**
```bash
uv venv .venv
uv pip install --python .venv -e ".[dev,ml]"
```
(Same command — `uv` handles the platform difference internally; only the Makefile's
hardcoded paths differ.)

Expected: a `.venv/` directory appears, and `uv` reports a list of installed packages
(FastAPI, SQLAlchemy, lightgbm, pytest, ruff, mypy, etc.) ending in something like
`Installed NN packages`.

Frontend dependencies:
```bash
cd frontend
npm install
cd ..
```
Expected: `node_modules/` appears under `frontend/`, and npm reports the installed package
count with no `npm error` lines.

## 3. Configure environment variables

```bash
cp .env.example .env
```
The defaults in `.env.example` work as-is for local development — no values need to be
filled in to run the full stack locally (there's no API key required for the ADS-B source
used; `ANTHROPIC_API_KEY`, `OPENSKY_CLIENT_ID/SECRET` etc. are reserved for not-yet-built
features and can stay blank).

## 4. Start the infrastructure + backend services

```bash
docker compose up -d --build
```
Expected: Docker builds three images (`contrail-api`, `contrail-ingest`,
`contrail-assembler`) and starts nine containers total (`postgres`, `redis`, `redpanda`,
`minio`, `api`, `ingest`, `assembler`, `prometheus`, `grafana`). First run takes a few
minutes (image builds, pulling base images); subsequent runs are fast.

Check everything is healthy:
```bash
docker compose ps
```
`postgres`, `redis`, `redpanda`, `minio` should show `(healthy)`; `api` should show
`(healthy)` once it's up (it depends on the others); `ingest`/`assembler`/`prometheus`/
`grafana` don't define a healthcheck so they'll just show `Up`.

## 5. Run database migrations

```bash
# Windows:
.venv/Scripts/python.exe -m alembic upgrade head
# macOS/Linux:
.venv/bin/python -m alembic upgrade head
```
Expected: two `INFO [alembic.runtime.migration]` lines and no error. Verify with:
```bash
.venv/Scripts/python.exe -m alembic current
```
Expected output: `0002 (head)`.

## 6. Seed reference data (airports, runways)

```bash
make seed
# or directly:
.venv/Scripts/python.exe -m scripts.seed_reference_data
```
Expected: structured JSON log lines ending in
`{"airports": 821, "runways": 3646, "event": "seed_complete", ...}` (this fetches real data
from a public GitHub-hosted mirror — it needs internet access, and the exact counts may
drift slightly over time as that upstream dataset is updated).

## 7. Confirm the backend is actually working

```bash
curl http://localhost:8000/health
# {"status":"ok"}
curl http://localhost:8000/health/ready
# {"status":"ok","checks":{"postgres":"ok","redis":"ok"}}
curl "http://localhost:8000/api/v1/airports" | head -c 200
# a JSON array of airport objects
```

Check real live data is flowing (give `ingest` 1-2 minutes after startup for its first
poll cycle):
```bash
docker compose logs ingest --tail 20
```
Expected: `poll_cycle_complete` log lines with nonzero `fetched`/`written` counts. Some
`tile_fetch_failed` / `429 Too Many Requests` lines are normal — adsb.lol's real rate limit
means not every tile succeeds every cycle (see `docs/adr/0003-hub-tiling.md`); this does not
indicate a problem with the application.

## 8. Run the frontend

```bash
make frontend-dev
# or directly:
cd frontend && npm run dev
```
Expected: Next.js starts on `http://localhost:3000` in under 2 seconds (`✓ Ready in
~1100ms`). Open it in a browser — you should see a brief splash animation, then the live
map with a "LIVE" badge and an aircraft count that updates every few seconds.

## 9. Run the backend services outside Docker (optional, for active development)

If you're actively editing backend code, running services directly (not in Docker) gives
faster iteration with `--reload`:

```bash
# API, with autoreload:
make api-dev
# or: .venv/Scripts/uvicorn.exe services.api.main:app --reload --port 8000

# Ingest worker:
make ingest-dev
# or: .venv/Scripts/python.exe -m services.ingest.main

# Assembler worker:
make assembler-dev
# or: .venv/Scripts/python.exe -m services.assembler.main
```
These still need `postgres`/`redis`/`redpanda`/`minio` running (via `docker compose up -d
postgres redis redpanda minio`, leaving `api`/`ingest`/`assembler` out of the compose
run so you run those three yourself).

## 10. Run tests

```bash
# Unit tests — fast, no external services needed:
make test-unit
# or: .venv/Scripts/python.exe -m pytest tests/unit -v
```
Expected: `185 passed` (as of this session; includes `tests/golden` only if you run it
explicitly — `make test-unit` itself targets only `tests/unit`, 163 of the 185).

```bash
# Integration tests — needs Docker (spins up its own ephemeral Postgres container
# via testcontainers, separate from your docker-compose stack):
make test-integration
# or: .venv/Scripts/python.exe -m pytest tests/integration -v
```
Expected: `16 passed` (as of this session). Each test runs in an isolated database
transaction that's rolled back afterward, so running this repeatedly never accumulates
stale data.

```bash
# Golden (determinism) tests:
.venv/Scripts/python.exe -m pytest tests/golden -v
```
Expected: `3 passed`.

## 11. Lint, format, typecheck

```bash
make lint        # .venv/Scripts/ruff.exe check services ml tests scripts
make fmt         # .venv/Scripts/ruff.exe format services ml tests scripts
make typecheck   # .venv/Scripts/mypy.exe services ml --ignore-missing-imports
```
Expected: `lint` reports `All checks passed!`. `typecheck` as of this session reports 2
pre-existing errors in `services/ingest/sources/adsb_lol.py` (an async-method type-override
mismatch and a union-type narrowing issue) — both pre-existing, not introduced by any work
described in this guide's companion docs.

Frontend equivalents:
```bash
cd frontend
npm run lint        # ESLint — expect no errors or warnings
npx tsc --noEmit     # TypeScript strict check — expect no output (clean exit)
npm run test         # Vitest — expect "8 tests ... passed" across 2 test files
npm run build        # Next.js production build
```

## 12. Build the production frontend

```bash
cd frontend
npm run build
```
Expected: `✓ Compiled successfully`, then a route table showing `/`, `/_not-found`, and
`/scorecard` all marked `○ (Static)` — both real pages are static-prerendered (all data
loads client-side).

```bash
npm start   # serves the production build
```

## 13. Train the ML model

```bash
make train
# runs, in order:
#   .venv/Scripts/python.exe -m ml.train.train_trajectory   (M1 — logs "not yet implemented", exits)
#   .venv/Scripts/python.exe -m ml.train.train_eta           (M2 — the real training run)
```
Expected for `train_eta`: downloads a month of BTS flight data on first run (~a few
hundred MB, cached under `data/raw/bts/` afterward), trains a LightGBM model, logs baseline
and model MAE, writes `data/models/eta-lgbm-<timestamp>.{txt,metrics.json}`, regenerates
`docs/ml-report.md`'s machine-generated section, and — if Postgres is reachable — registers
the run in `model_registry`, promoting it only if it beats the current incumbent. This
takes a few minutes, mostly for the BTS download on a cold cache.

After training, `GET http://localhost:8000/api/v1/models/scorecard` and the `/scorecard`
frontend page will show real metrics instead of the "no model has been promoted yet" empty
state.

## 14. Test the WebSocket live feed directly (useful for debugging)

```bash
.venv/Scripts/python.exe - <<'EOF'
import asyncio, json, websockets
from services.common.ws_protocol import decode_frame

async def main():
    async with websockets.connect("ws://localhost:8000/ws/live") as ws:
        await ws.send(json.dumps({"op": "subscribe", "h3_cells": ["8544c1bbfffffff"]}))
        frame = await asyncio.wait_for(ws.recv(), timeout=10)
        version, frame_type, ts_delta_ms, records = decode_frame(frame)
        print("record_count", len(records))

asyncio.run(main())
EOF
```
(Replace the H3 cell with a real one currently populated — query
`SELECT h3_r5, count(*) FROM state_vectors GROUP BY h3_r5 ORDER BY 2 DESC LIMIT 5;` against
the running Postgres container to find one.)

## 15. Shut everything down

```bash
docker compose down
```
Expected: all nine containers stop and are removed; the named volumes (`pgdata`,
`miniodata`) persist by default, so your seeded airport data and any trained model
registry entries survive a `down`/`up` cycle. Add `-v` to also remove those volumes for a
fully clean slate (you'd need to re-run migrations and seeding afterward).

---

## See also

[Learning guide](LEARNING_GUIDE.md) · [Architecture](ARCHITECTURE.md) ·
[Data flows](DATA_FLOWS.md)
