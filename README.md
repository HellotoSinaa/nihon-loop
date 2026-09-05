# Nihon Loop

A Kaizen loop coach for human+agent systems. Each cycle does one job
against the current standard, measures waste from real work artifacts,
and allows exactly one improvement into the next standard.

Nihon Loop implements a small, strict PDCA loop (Plan → Do → Check →
Act) — it is compatible with Kaizen thinking but does not claim to be
"the Toyota Production System."

Serves as the live brain behind the Headless Domains identity
`nihon.chatbot` (Handshake `.chatbot` namespace).

## Hard rules the protocol enforces

- One improvement per cycle. CHECK never returns a list of tips.
- No standard change without a before/after metric.
- Root cause over blame: a bounded 5-Whys chain (max 5), always at the
  process/standard level, never naming the human as the cause.
- If the job failed this cycle, the proposed change always targets
  making the job succeed — no waste metric is locally optimized while
  the job itself is broken.
- If DO artifacts show no measurable waste, CHECK proposes nothing and
  asks for more gemba data (tool calls, wait time, errors) instead of
  inventing an improvement.

## Local run

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then edit as needed
uvicorn app.main:app --host 0.0.0.0 --port 8080 --reload
```

Open `http://localhost:8080/` for the minimal chat UI, or drive it by API:

```bash
curl -s http://localhost:8080/health
curl -s http://localhost:8080/status
curl -s http://localhost:8080/skill.md
curl -s http://localhost:8080/agent.json
```

Run the test suite:

```bash
pytest -q
```

Everything above works with **no LLM key configured** — the protocol
engine (`app/protocol.py`) is pure Python and the coach falls back to
deterministic templates. If `ANTHROPIC_API_KEY` is set, replies are
polished through Claude; else if `GEMINI_API_KEY` / `GOOGLE_API_KEY` is
set, through Gemini. Any LLM failure silently falls back to the
template reply — the loop's mechanics never depend on the network.

## Driving a full cycle by API

```bash
# PLAN — create a system and open a cycle
SYS=$(curl -s -X POST localhost:8080/systems -H 'content-type: application/json' \
  -d '{"name":"support-queue","current_standard":"1. Triage daily."}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["id"])')

CYCLE=$(curl -s -X POST localhost:8080/systems/$SYS/cycles -H 'content-type: application/json' \
  -d '{"job":"Triage Monday backlog","tool_budget":8}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["id"])')

# DO — record what actually happened
curl -s -X POST localhost:8080/cycles/$CYCLE/do -H 'content-type: application/json' \
  -d '{"artifacts":{"tool_calls":8,"human_wait_seconds":300,"errors":2,"notes":"had to switch tabs to re-read old tickets"},"tools_used":8}'

# CHECK — get the waste report + single proposed change
curl -s -X POST localhost:8080/cycles/$CYCLE/check -H 'content-type: application/json' -d '{}'

# ACT — accept or reject
curl -s -X POST localhost:8080/cycles/$CYCLE/act -H 'content-type: application/json' -d '{"accept":true}'
```

Or conversationally via `POST /chat`:

```
new system: support-queue
new cycle: Triage Monday backlog
done 8 300 2 notes: had to switch tabs to re-read old tickets
check
accept
```

Persian input in `/chat` gets a Persian reply; protocol field names
(`job_failed`, `extra_tool_calls`, etc.) always stay English.

## Deploy to a public HTTPS host

Handshake names like `nihon.chatbot` **do not resolve in stock
browsers** without a Handshake-aware resolver or gateway. This service
must always also be reachable on a normal HTTPS origin — that's what
`PUBLIC_BASE_URL` is for, and it's what `/agent.json` and `/skill.md`
advertise as the actual place to send requests.

### Docker

```bash
cp .env.example .env   # fill in PUBLIC_BASE_URL and any LLM key
docker compose up --build -d
```

### Render / Railway / Fly / any container host

1. Push this repo.
2. Deploy it (see `deploy/render.yaml` for a Render starting point —
   adapt for Railway/Fly/etc. as needed).
3. Once you have a live URL (e.g. `https://nihon-loop.onrender.com`),
   set `PUBLIC_BASE_URL` to that exact URL and redeploy — `/agent.json`
   and `/skill.md` bake this URL into every endpoint they advertise.

## Point Headless Domains / PowerLobster at the live deployment

1. In the Headless Domains dashboard for `nihon.chatbot`, update the
   served manifest/skill records to point at your live host:
   - `uptime` → `https://<your-host>/status`
   - `webhook` → `https://<your-host>/webhook`
   - skill file → `https://<your-host>/skill.md`
   - agent manifest → `https://<your-host>/agent.json`
2. Set a DNS/TXT record (per the Headless dashboard's instructions) so
   humans and agents resolving `nihon.chatbot` — via a Handshake
   resolver or the `profiles.host.limo` fallback — land on your live
   host instead of the placeholder.
3. If PowerLobster is the inbound channel, point its webhook at
   `https://<your-host>/webhook` and set `POWERLOBSTER_WEBHOOK_SECRET`
   to match whatever secret PowerLobster sends in
   `X-PowerLobster-Secret`. The endpoint accepts both the generic
   `{"message","sender"}` shape and PowerLobster's
   `{"id","sender_handle","sender_name","content","created_at"}` shape.
4. Update the capability list on file for `nihon.chatbot` from
   `["general"]` to what `/agent.json` now serves:
   `["kaizen_coach","pdca_loop","waste_audit","standard_update"]`.

## Known MVP limitations (by design, not oversight)

- `before_metric_value` is captured at CHECK time; `after_metric_value`
  is not yet auto-populated — proving a change worked means running
  another cycle and comparing its waste for the same metric by hand.
  Wiring that comparison automatically is a natural next increment.
- `/chat` and `/webhook` intent parsing is a small deterministic
  command grammar (`new system: ...`, `new cycle: ...`, `done ...`,
  `check`, `accept`/`reject`, `status`, `help`), not open-ended NLU —
  this keeps the loop's state machine correct with zero LLM keys. An
  LLM key only polishes wording, never the mechanics.
- No auth, no payments, no multi-tenant billing — `/agent.json`
  reports `payments.inbound: "pending"` rather than inventing an
  address.
- Rework detection is a same-job-text heuristic across consecutive
  cycles for one system; it won't catch rework worded differently.

## Repo layout

```
app/
  main.py       FastAPI routes
  protocol.py   pure PDCA/Kaizen rules — no HTTP, no DB
  coach.py      chat intent parsing + template/LLM replies
  identity.py   skill.md / agent.json generation
  webhooks.py   generic + PowerLobster payload normalization
  models.py     SQLAlchemy models: System, Cycle, Message
  schemas.py    Pydantic v2 request/response models
  db.py         engine/session setup
  static/index.html   minimal chat UI
tests/
  test_protocol.py   protocol engine unit tests
  test_api.py        full-cycle + endpoint integration tests
```

## Go-live checklist for nihon.chatbot

1. `pytest -q` passes locally.
2. Deploy (Docker Compose, Render, Railway, Fly — your call) and get a
   real HTTPS URL.
3. Set `PUBLIC_BASE_URL` to that URL; redeploy so `/agent.json` and
   `/skill.md` advertise the right endpoints.
4. Set `ANTHROPIC_API_KEY` (or `GEMINI_API_KEY`/`GOOGLE_API_KEY`) if you
   want LLM-polished replies; leave blank to run fully on templates.
5. Set `POWERLOBSTER_WEBHOOK_SECRET` if PowerLobster will call
   `/webhook`; point PowerLobster's webhook config at
   `https://<host>/webhook`.
6. In the Headless Domains dashboard: update `nihon.chatbot`'s
   manifest/skill/uptime/webhook records to the live URLs above, update
   the capability list from `["general"]` to the four Nihon Loop
   capabilities, and set the DNS/TXT record so resolvers point here.
7. Smoke test the live host: `GET /health`, `GET /status`,
   `GET /skill.md`, `GET /agent.json`, then run one full PLAN→DO→CHECK→ACT
   cycle against it.
8. Confirm `https://profiles.host.limo/nihon.chatbot` (the
   no-gateway-needed fallback) reflects the update once Headless's
   propagation completes.
