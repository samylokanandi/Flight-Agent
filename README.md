# Flight Agent

An AI flight tracker built in phases. Phase 1 (this repo as it stands) is a hand written tool calling agent over live flight data. RAG and text to SQL get added later as more tools.

## Layout

```
flight-agent/
├── app/
│   ├── config.py          settings from .env
│   ├── agent.py           the agent loop (read this first)
│   ├── main.py            FastAPI: /ask plus /debug endpoints
│   └── tools/
│       ├── registry.py    tool schemas + dispatcher
│       ├── aviationstack.py   flight status by flight number
│       ├── opensky.py     live aircraft near a location
│       └── errors.py
│   ├── tracker.py         when to check, what changed, notifications
│   ├── store.py           Postgres / SQLite storage
│   └── notify.py          phone push via ntfy
├── cli.py                 chat in your terminal
├── track.py               flight tracker commands
├── .github/workflows/     the every 15 minutes cloud job
├── tests/                 offline tests, no keys needed
└── evals/failures.md      log of things the agent got wrong
```

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env       # then fill in your keys
pytest                     # should pass with no keys at all
```

Keys you need:

* **ANTHROPIC_API_KEY** from console.anthropic.com
* **AVIATIONSTACK_API_KEY** from aviationstack.com (free tier works, but it only allows http and has a small monthly quota)
* OpenSky works without credentials. If you hit rate limits, create an API client in your OpenSky account and set OPENSKY_CLIENT_ID and OPENSKY_CLIENT_SECRET.

## Run it

Terminal chat (add `--trace` to watch every tool call and token count):

```bash
python cli.py --trace
```

Or the API:

```bash
uvicorn app.main:app --reload
curl -X POST localhost:8000/ask -H "Content-Type: application/json" \
  -d '{"question": "What planes are flying near Newark right now?"}'
```

Debug the tools directly, no LLM involved:

```
GET /debug/flight/DL123
GET /debug/nearby?lat=40.69&lon=-74.17&radius_km=20
```

## Flight tracker with phone notifications

1. Install the **ntfy** app on your phone and subscribe to a long random topic name (anyone who knows it can read your notifications).
2. Put that topic in `.env` as `NTFY_TOPIC`.
3. Test it: `python track.py --test-notify`

Commands:

```
python track.py add UA1532        start tracking; first check runs right away
python track.py list              what's being tracked and when it's next checked
python track.py stop UA1532       stop tracking
python track.py watch             local mode: keep running on your laptop
python track.py run-due           check whatever is due, then exit (the cloud runs this)
```

Add flights on the day you fly; AviationStack usually doesn't list a flight until close to its date.

Each flight is checked hourly until 3 hours before departure, then every 15 minutes, then every 30 minutes in the air, and tracking stops once it lands, is cancelled, or diverts. That keeps one flight at roughly 25 to 30 API calls, which matters on AviationStack's free tier of 100 calls a month. `MAX_CHECKS_PER_FLIGHT` is a hard cap.

Plain Python decides when to check and what changed (status, delays of 10+ minutes, gate or terminal changes). Claude only writes the notification text, and a template takes over if Claude is unavailable, so the tracker never goes silent.

Storage: with `DATABASE_URL` set, everything goes to Postgres (Neon). Without it, a local SQLite file is used. The schema is created automatically.

## Deploying (free: Neon + GitHub Actions)

The app doesn't run as one long process in the cloud. Instead, every tracked flight stores its own "next check" time in the database, and a scheduled job wakes up every 15 minutes, checks whatever is due, and exits. All the state lives in Postgres.

1. Put `DATABASE_URL` (from Neon's Connect button, pooled connection) in your local `.env`.
2. Push the repo to GitHub. Run `git status` first and make sure `.env` is NOT listed.
3. In the repo on GitHub: Settings, then Secrets and variables, then Actions. Add four repository secrets: `DATABASE_URL`, `ANTHROPIC_API_KEY`, `AVIATIONSTACK_API_KEY`, `NTFY_TOPIC`.
4. Go to the Actions tab, open "Check tracked flights", and click "Run workflow" once to confirm it goes green.
5. From your laptop: `python track.py add UA1532`. It writes to Neon, does the first check, and GitHub takes over from there.

Don't run `python track.py watch` locally while the GitHub job is active, or flights get checked twice.

Notes: GitHub can start scheduled jobs a few minutes late, and it pauses scheduled workflows in repos with no activity for 60 days. Each run wakes the Neon database briefly; at every 15 minutes this stays well within the free plan.

## Things to try and watch

1. Ask about a flight number, then a follow up like "is it delayed?" in the CLI. Does history carry over correctly?
2. Ask "what's flying over Central Park?" and check in the trace whether the coordinates it picked are reasonable.
3. Give it a fake flight number. Does it say so, or invent an answer?
4. Ask something ambiguous like "is my flight on time?" with no number. Does it ask, or guess?
5. Watch `output_chars` in the trace. If a tool result is huge, trim it more in the tool.

Anything that goes wrong goes in `evals/failures.md`.

## Roadmap

* **Phase 1 (now):** live data tools + hand written agent loop
* **Phase 2:** RAG over airline policies and DOT refund rules, exposed as a `search_policies` tool (pgvector, chunking, hybrid search, reranking)
* **Phase 3:** text to SQL over BTS on time performance data, exposed as a `query_history` tool
* **Phase 4:** eval harness built from `evals/failures.md`
