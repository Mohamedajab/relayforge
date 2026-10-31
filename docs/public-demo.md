# Public recovery demo

**[Open RelayForge](https://relayforge-demo.vercel.app)** — no signup.

The public page runs the existing Python service, delivery worker and signed example receiver.
It does not expose the production API or accept destinations, payloads or credentials.

## What a run checks

1. Worker A claims an event and abandons it.
2. Worker B cannot take a live lease, then takes the expired lease.
3. The receiver returns 503 twice and 202 on the third attempt.
4. Exactly one receiver record and one delivery remain after producer replay.

Every request uses a temporary SQLite database and the receiver's in-process HTTP transport.
There is no external webhook traffic and no persistent public queue. The full application's
PostgreSQL concurrency and external HTTPS behaviour are covered separately by its tests.

## Run or redeploy

```sh
python -m pip install -e '.[dev]'
uvicorn app:app --port 8766
# Open http://localhost:8766
python scripts/recovery_demo.py --recording /tmp/recovery.cast
pytest
ruff check .
ruff format --check .
mypy src
```

Vercel uses the FastAPI preset, root directory `.`, and Python 3.12 from `.python-version`.
The initial production deployment used commit `891022a` from `codex/cv-readiness`:

```sh
npx vercel login
npx vercel link --project relayforge-demo --scope mohamedajab
npx vercel deploy --prod --scope mohamedajab
```

The project is connected to this repository; Vercel's normal production branch is `main`.
The recovery demo and delivery improvements are merged into `main`; deploy that branch.
No environment secrets or database service are needed. `app.py` exposes only the sample runner and static page;
`vercel.json` sets a 30-second function maximum and browser security headers.

The runner has a 15-second deadline and permits one active run per Python process, with
a ten-second interval between starts. These limits are process-local: multiple hosting
instances have separate limits. They are not an account-wide rate or spending cap.
The demo uses existing free hosting allowances; availability depends on those allowances.

## Verification

Frontend revision, 30 October 2026: replaced the landing-page treatment with a scenario
and delivery-log workspace; see [design choices](design.md). IBM Plex Sans is served locally
under the included OFL licence. The recovery runner and API are unchanged. Ruff checks passed;
the revised local browser controls completed the real recovery sequence.

30 October 2026, Windows, Python 3.12.10: `pytest` passed 52 tests, with one PostgreSQL
integration test skipped because no local database engine was available; coverage 90.56%.
The actual recovery command completed with 503, 503, 202 and one receiver record.
Ruff and mypy passed. CI runs the database test against PostgreSQL 17 on Python 3.11/3.12.

[CI run 41139723690](https://github.com/Mohamedajab/relayforge/actions/runs/41139723690)
passed all 53 tests with no skips on both Python versions; Python 3.12 coverage was 90.67%.
It also passed migrations, Ruff, mypy and the CLI recovery recording.

Public deployment `dpl_5bejyk2hSq87GodFzEpqRLEmquov` returned HTTP 200 without authentication
for `/`, `/healthz`, `/demo.js` and `/demo.css`. An unauthenticated POST to `/api/run` and
the live browser button both completed the 503/503/202 sequence and verified one receiver
record and one delivery. Security headers were checked on the public responses.
