# Deploying the parser (Railway)

The parser runs on Railway: project `tohund-services`, service `bankparser`,
region Singapore (`sin`, since 11 Oct 2026), one replica, built from the
`Dockerfile`. Domains: `parser.tohundguide.com` (direct) and
`bankparser-production.up.railway.app`. The app is mounted at
`SCRIPT_NAME=/tools/bankstatementparser`, so every URL carries that prefix:
`https://parser.tohundguide.com/tools/bankstatementparser/status`.

`DEPLOY_VPS.md` describes the old Hostinger setup; it is history.

## A deploy is `railway up`, not a push

GitHub holds the code and runs the tests (`.github/workflows/tests.yml`), but
a push deploys nothing. From a checkout of `main`, with the project token:

```bash
railway up --service bankparser --detach
```

Two gates stand between a build and the people using it:

1. **The tests run inside the Docker build** (`RUN ... pytest tests` in the
   Dockerfile). A red test fails the image, so the running parser is never
   replaced by a broken one, even from a laptop.
2. **The healthcheck** (`railway.json`: `/tools/bankstatementparser/status`).
   A container that does not answer is never promoted.

Rolling back is a click in the Railway dashboard: the previous deployment,
Redeploy.

## What lives where

| | |
|---|---|
| `uploads/`, `output/` | on the container only; a file is deleted when its answer is sent (API) or after one download / one hour. Never kept across a deploy, on purpose. |
| `learned/` (AI cache, learned bank profiles, the daily AI call count), `feedback/` | under `DATA_DIR` (the mounted volume `/data`), so they survive a deploy. Without the volume they start empty after every deploy. |

## Variables (names only; the values are in Railway)

`API_KEYS` (`key:Label,key:Label`; one line per API caller, Karr among them),
`GEMINI_API_KEY`, `LLM_DAILY_LIMIT`, `API_RATE_LIMIT`, `SCRIPT_NAME`, `PORT`,
`DATA_DIR=/data`.

## The public API and its contract

`contract/v1/response.schema.json` and `contract/v1/sample-v1-scan.json` are
the contract of `POST /api/v1/parse`. `tests/test_contract_v1.py` asserts the
sample is exactly what the code answers; Karr keeps a copy of the same sample
and its own test reads it. `/api/v1` only grows; a breaking change is `/api/v2`.
To change the sample on purpose: `python tests/test_contract_v1.py --write`,
then update Karr's copy.

`allow_ai=false` is honoured: a layout no parser knows is refused
(`PARSE_FAILED`) rather than read by the parser's own AI. Nothing an API caller
sends is cached or learned from, whichever way the flag is set.
