# muse-router

FastAPI gateway turning muse.ai (Meta Personal AI Agent, codename Hatch, model Muse Spark)
into an OpenAI-compatible API (`/v1/chat/completions`, `/v1/completions`, `/v1/models`),
with a multi-account pool, first-chunk failover, emulated tool calling and an admin API.

## Quick start (Mock driver, no real account needed)

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
MUSE_DRIVER=mock uvicorn muse_router.main:app --host 0.0.0.0 --port 18610
```

With the mock driver, test accounts use sample cookie values only
(`valid_token`, `expired_token`, `ratelimit_token`) — never a real cookie.

## Quick start (Docker)

```bash
docker compose up --build
```

## Configuration

See `.env.example`. All settings use the `MUSE_` prefix (see `src/muse_router/config.py`):
port `18610`, driver default `browser`, `max_retries=3`,
quota cooldown `1800`s, transient cooldown `45`s, request timeout `120`s,
first-chunk timeout `25`s, keepalive interval `900`s (`0` = off).

## Dashboard

Open http://localhost:18610/ (or `/dashboard`) in a browser. One
self-contained page (inline CSS/JS, no CDN, no external assets) with:

- **Tổng quan:** total requests, ready / cooling / expired accounts,
  active leases, success rate, total IN/OUT tokens, and a short account table.
- **Endpoint & Key:** the OpenAI-compatible endpoint
  (`<origin>/v1`) with a Copy button, plus separate inputs for the admin
  key and the API key, and a connection check (`/healthz`,
  `/admin/accounts`). There is also a Server URL field: leave it empty
  when the dashboard is served by the router itself, or enter the
  router's URL (e.g. `http://localhost:18610`) when opening the
  dashboard file from somewhere else.
- **Tài khoản:** full account table (masked `hatch_sess`), Reset/Delete
  (with confirmation), and an add-account form that sends
  `cookie_header` straight to the backend (or Cookies JSON if no header
  is given).
- **Thử chat:** model list from `GET /v1/models`, prompt input,
  stream toggle, SSE output parsing, and usage display (including
  `tool_calls` when returned).
- **Nhật ký:** console-style activity log (time, model, account, status,
  duration, TTFT, IN/OUT tokens, error), auto-refresh every 3s while
  open, with Pause and Clear.
- **Cài đặt:** non-sensitive config from `GET /admin/config`. Config is
  changed via environment variables / `.env` and a server restart —
  the dashboard never edits keys through the UI.

**Security notes:** keys are stored only in the browser's `localStorage`
and are only sent as an `Authorization` header to the same origin; the
dashboard page itself never embeds a key, and `/admin/config` never
returns key values. Run the dashboard only on a trusted network — do
not expose it publicly without authentication in front of it.

## API

- `GET /v1/models` — Bearer `MUSE_API_KEY`
- `POST /v1/chat/completions` — `stream: false` (JSON) or `stream: true` (SSE, ends `data: [DONE]`)
- `POST /v1/completions` — legacy completions, same streaming contract
- `GET /admin/accounts`, `POST /admin/accounts`, `PATCH /admin/accounts/{id}`,
  `POST /admin/accounts/{id}/reset`, `DELETE /admin/accounts/{id}` — Bearer `MUSE_ADMIN_KEY`
- `GET /healthz`, `GET /readyz`

Admin responses mask `hatch_sess` (first 6 + last 4 characters only).
Account state is persisted atomically to `data/accounts.json`
(write to a `.tmp` file, then `os.replace`); `active_leases` is never persisted
and always resets to `0` on startup.

## Tool calling

When `tools` are supplied, the context builder injects an instruction asking the
model to emit only `<tool_call>{"name": ..., "arguments": {...}}</tool_call>`.
The response parser converts that block into OpenAI `tool_calls` with
`finish_reason="tool_calls"`.

## Tokenizer

Token counts use `tiktoken` (`cl100k_base`) when installed. If `tiktoken` is
not available, `utils/tokenizer.py` falls back to a simple whitespace-based
estimate so the API and tests keep working; counts in that mode are approximate
and this is noted here explicitly.

## Debugging the live browser driver

When the browser driver cannot find the message composer, it now
leaves evidence instead of just closing the page:

- a screenshot and the page HTML are saved under
  `debug/browser/` (configurable via `MUSE_BROWSER_DEBUG_DIR`,
  gitignored, never committed);
- the server log prints the page URL, title, and up to 20
  candidate input elements (`textarea`, `[contenteditable]`,
  `input[type="text"]`, `[role="textbox"]`) with their
  placeholder / aria-label and visibility.

If you also set `MUSE_BROWSER_DEBUG_KEEP_PAGE=true` together
with `MUSE_HEADLESS=false`, the failed page is kept open for
manual inspection instead of being closed; close the browser
window or restart the server to release it. Note that a failed
attempt still puts the account into transient cooldown
(default 45s): wait it out, or Reset the account in the
dashboard, before retrying.

## Drivers — live-verification status (important)

- `MockDriver`: fully exercised by `pytest`.
- `BrowserCDPDriver` (Playwright): implemented per SPEC §3.5 (cached
  `BrowserContext` per account, login-redirect check -> expired, DOM-polling
  streamer, cookie refresh after completion) but **NOT verified live against
  muse.ai** — no real muse.ai account/cookie was provided for this project.
- `DirectHTTPDriver`: implemented per SPEC §3.5 status mapping
  (401/403 -> expired, 429 -> quota, 5xx/timeout -> transient), but its exact
  upstream conversation endpoint is a best-effort placeholder because the SPEC
  does not name the real muse.ai endpoint, and it is likewise **not verified
  live**. Do not use either live driver in production without verifying the
  current muse.ai web flow / endpoint first.

## Tests

```bash
MUSE_DRIVER=mock pytest -v
```

Acceptance scenarios (SPEC §6): pool rotation/concurrency, first-chunk
failover streaming, OpenAI endpoints compatibility + tool calling + admin API,
plus prompt/context-builder and tool-parser tests.
