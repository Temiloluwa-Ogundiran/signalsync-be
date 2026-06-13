# Partna AI ("Ask Partna AI") — Architecture & Implementation Spec

**Status:** Draft for team review
**Author:** Engineering
**Last updated:** 2026-06-13
**Scale target:** 50k+ users at launch

---

## 0. For the implementing session — READ FIRST

This spec will be executed in a fresh session (Sonnet 4.6). Start here.

**Before writing any backend code, read [`synctrades-be/RULES.md`](../RULES.md)
in full.** It is a hard contract (layering, transactions, PgBouncer/50k-scale DB
rules, security). Where this spec and RULES.md ever disagree, **RULES.md wins** —
tell the user instead of improvising.

### What is already done for you (don't rebuild)
- **FE navbar stub exists:** `sync-trades-fe/.../header.tsx` wires
  `useAiInsightModal().open`; `ai-insight-modal.tsx` is a **hardcoded mock** to
  replace. (Frontend is a separate repo — `sync-trades-fe`.)
- **The Next proxy already streams and injects auth:**
  `sync-trades-fe/src/app/api/proxy/[...path]/route.ts` forwards `response.body`
  (SSE works) and overwrites `Authorization` from the NextAuth cookie. You only
  add `ai/*` → `AI_SERVICE_URL` routing (§6).
- **Auth is compatible:** the prototype's `sub=user_id`, HS256 already match the
  backend. Reuse `shared/deps.get_current_user`; delete the prototype's `auth.py`.
- **The journal chat rail is already controlled** and its AI-hook props
  (`prompts`, `onPromptClick`, `onContextChange`, `chatContext`) are declared and
  currently `void`-ed — the toggle slots in there (§ surface 3).

### Where the code goes (new domain)
```
src/app/domains/ai/
  __init__.py
  router.py            # /ai/* endpoints; parse → ONE service call → schema (RULES §1)
  service/             # business logic; commit ONLY at public entry (RULES §2)
    __init__.py
    _sessions.py       # session CRUD
    _chat.py           # turn orchestration (prepare → stream → persist)
    _insights.py       # precompute + read
    _memory.py         # cross-session user memory (§11.1)
  repository.py        # ALL SQL: sessions, messages, usage, insights, memory,
                       #          + the read-only analytics queries the tools call
  schemas.py           # pydantic; request models use ConfigDict(extra="forbid")
  models.py            # ai_chat_sessions/_messages, ai_usage, ai_user_memory, ai_agents
  agent.py             # LangGraph: compiled ONCE, account_ids via configurable (§9.1)
  prompts.py           # system prompt (port from prototype)
  tools/               # the 16 analytics tools — each calls repository.py, NOT raw SQL
  cache.py             # Redis tool cache keyed by data_version (§8.1)
  quota.py             # credit metering (§10)
  checkpointer.py      # LangGraph PostgresSaver on DATABASE_URL_DIRECT (see gotcha)
  deps.py              # AI-specific deps (e.g. resolve account_ids for current user)
```
The prototype `ai/app/*` is the **reference**, not the target. Port its logic into
this layering — do not copy its files wholesale (they break RULES, see below).

### Prototype violations you MUST fix while porting
| Prototype (`ai/app`) | Breaks | Fix |
|---|---|---|
| Raw SQL in `service.py` (`SELECT id FROM trading_accounts…`) | RULES §1 (no SQL in services) | Move to `repository.py` / reuse `accounts.repository`. |
| `send_message` commits 3× | RULES §2 (one commit at entry) | One transaction; flush in repo, single `commit()` in the public service fn. |
| Own `auth.py` (python-jose) | RULES §5 (one auth path) | Delete; use `get_current_user`. |
| `ai_chat_messages` unbounded | RULES §3 (retention story) | Ship a purge job + index now. |
| Graph recompiled per request | §9.1 perf | Compile once; inject `account_ids` via `configurable`. |
| Blocking `compiled.invoke` | §9.2 (starves workers) | Async `astream_events` + SSE; request path fully async. |
| No pagination on `list_sessions` | RULES §3 | Cursor pagination, `limit=Query(50, le=100)`. |

### Critical gotchas (each one is a shipped-bug-waiting-to-happen)
1. **PgBouncer × checkpointer** — see the boxed warning in §7. Put the LangGraph
   checkpointer on `DATABASE_URL_DIRECT`. This is the #1 thing to get right.
2. **Request path is async-only.** No sync `invoke`, no blocking tool I/O on the
   event loop. Tools either use async SQLAlchemy or run via `anyio.to_thread`.
3. **Rate-limit keyed per-user** via the existing `limiter` (RULES §5); add
   `@limiter.limit(settings.RATE_LIMIT_AI)` to the stream endpoint.
4. **Money is `Decimal`** end-to-end; cast to float only at the schema boundary.
5. **Tool SQL is parameterized + tested** against the real test DB (RULES §3).

### Integration points (grep targets)
- `src/app/main.py` → `app.include_router(ai_router)` (and its sub-routers).
- `src/app/models/__init__.py` → import + `__all__` the new `ai_*` models.
- Alembic → one new migration on the current head (autogenerate after registering
  models; migrations run on `DATABASE_URL_DIRECT`).
- `src/app/tasks/journal_sync_tasks.py` / `accounts/sync_orchestrator.py` → after a
  successful sync, `redis.incr(f"acct:ver:{account_id}")` + enqueue
  `ai.recompute_insights` (§8.1). Hook at sync success, not in the request.
- `src/app/core/config.py` → add the settings in "Config keys" below.
- `pyproject.toml` → add: `langchain`, `langchain-core`, `langchain-openai`,
  `langgraph`, `langgraph-checkpoint-postgres`, `psycopg[binary,pool]`,
  `redis` (async). `openai` comes transitively via `langchain-openai`.

### Config keys to add (`core/config.py`)
```
OPENAI_API_KEY: str
AI_MODEL: str = "gpt-4.1-mini"
AI_TEMPERATURE: float = 0.0
AI_ENABLED: bool = True                      # kill-switch
AI_REDIS_URL: str = "redis://localhost:6379/3"   # own DB index (2=rate-limit,0/1=celery)
AI_SERVICE_URL: str = ""                     # FE proxy target; empty => in-process
RATE_LIMIT_AI: str = "12/minute"
AI_CREDITS_FREE: int = 50
AI_CREDITS_ESSENTIAL: int = 500
AI_CREDITS_PRO: int = 1000
# DATABASE_URL_DIRECT already exists (used by Alembic) — reuse for the checkpointer.
```

### Build order
Follow the phases in §12. **MVP = phases 1–3.** Each phase below names its
acceptance check; don't advance until it's green. When unsure about a RULES
interpretation or a product detail (plan tiers, credit costs), **stop and ask the
user** rather than guessing — this doc flags those in §13.

---

## 1. Summary

We are adding an AI trading copilot ("Partna AI") to SyncTrades, modelled on
TradeZella's "Zella AI". It appears in three places:

1. **Floating dock (navbar "Ask Partna AI")** — a right-side docked chat panel
   (TradeZella's Zella AI pattern): context-aware ("Opened from: Dashboard"),
   suggested prompts, a "Take Action" chip row, session history, and an
   **expand-to-fullscreen** control that hands off to surface #2.
2. **Full-page chat (left-sidebar "Partna AI" tab)** — a dedicated route giving
   the full-width chat experience: persistent session sidebar, larger message
   area, richer rendering. Same engine and session store as the dock — the dock's
   expand button deep-links here.
3. **In-context chat-rail AI** — the existing journal **day** and **trade** chat
   rails gain an *"Ask AI"* toggle. Off = journaling (today's behaviour, saves to
   `/journal`). On = the same engine, scoped to that specific day/trade.
4. **Proactive insight cards** — precomputed after each account sync (home-screen
   chips, "find and flag my worst pattern"), served instantly instead of a live
   LLM call per user.

Surfaces 1 and 2 are **two presentations of one component** (dock vs. full page);
the dock is the quick-access glance, the sidebar tab is the focused workspace —
exactly how TradeZella splits the slide-out Zella panel from its larger view.

The engine already exists as a near-complete prototype in the `ai/` repo
(LangGraph ReAct agent + 16 data tools + Postgres-backed conversation memory).
This spec describes how to productionize it and integrate it into
`synctrades-be` and `sync-trades-fe`.

### Decisions made

| Decision | Choice | Rationale |
|---|---|---|
| LLM provider | **OpenAI `gpt-4.1-mini`** | Zero engine changes; prototype already runs on it. Revisit provider routing post-launch. |
| Codebase | **Inside `synctrades-be` as a domain** (`src/app/domains/ai/`) | Decided by the team: the AI engine is a first-class domain, following the existing router→service→repository→models layering. NOT a separate repo. |
| Deployment (launch) | **Same image; runnable as a separate process group** | One codebase/image. The AI routes can be served by a dedicated uvicorn/gunicorn process group (same image, scaled independently) so slow LLM calls don't starve API workers. Starting in-process is acceptable **only** if the AI path is fully async (§9). |
| Database | **Same Postgres, single Alembic chain** | AI tables live in the same DB and the same migration history; models register in `app/models/__init__.py`. AI reads core tables (trades/journal/accounts) via repositories, never raw cross-domain SQL. |
| Auth | **Reuse `get_current_user`** | Drop the prototype's `python-jose` auth; use `shared/deps.get_current_user` (validates `typ==access`, user exists, not deleted, email verified). No new auth surface. |

---

## 2. Why a dedicated domain (and its own process group)

The AI engine lives **in** `synctrades-be` as `domains/ai/`, but its workload has a
fundamentally different runtime profile from the rest of the API:

| Concern | Core API (`synctrades-be`) | AI engine |
|---|---|---|
| Request duration | 10–200 ms | **3–20 s** (LLM + tool loops) |
| Bottleneck | CPU / DB | Outbound LLM latency (I/O-bound) |
| Dependencies | lean | langchain/langgraph/openai (~100s of MB) |
| Release cadence | migration-gated | prompt tweaks ~daily |
| Cost | fixed infra | metered per token |

If a 15-second LLM call runs inside the pool that serves 50k users' journal/trade
requests, those slow calls **starve normal traffic** (worker exhaustion).
Single-codebase does **not** mean single-process: we run the same image as **two
process groups** behind the load balancer — the API group and an AI group that
serves `/ai/*`. They share code, DB, and Redis but not an event loop or a worker
pool, so the AI group can be scaled (and rate-limited, and circuit-broken)
independently. The frontend reaches the AI group via the Next proxy's `ai/*`
route; `AI_SERVICE_URL` simply points at the AI group's URL (which may equal
`BACKEND_URL` in dev/in-process mode).

**Domain-boundary rules (in addition to RULES.md):**
- AI is a normal domain: `router → service → repository → models`, plus
  `agent/`, `tools/`, `cache`, `quota` helpers. It obeys every rule in
  `synctrades-be/RULES.md` — that file is the contract, this spec is the design.
- AI **reads** core tables only through their domains' repositories (e.g.
  `accounts.repository` for account ids), or — for the read-only analytics tools —
  through its own repository methods that issue **parameterized, tested** SQL
  (RULES §3). No f-string SQL, no reserved-word aliases, every raw query covered
  by a test against the real test DB.
- AI **owns** the `ai_*` tables; their migrations are part of the **single Alembic
  chain** (one head), and the models are registered in `app/models/__init__.py`.
- Core API → AI coupling is one-directional and async: the sync task bumps a Redis
  `data_version` and enqueues an insight-recompute Celery job; the request path
  never blocks on the AI engine.

---

## 3. Architecture

```
                  ┌────────────────────────────────────────────────────┐
                  │                 sync-trades-fe (Next)              │
                  │  • Sidebar "Partna AI" tab  → /ai full page        │
                  │  • Navbar "Ask Partna AI"   → floating dock panel  │
                  │  • Chat-rail AI toggle (journal/trade)             │
                  │  • TanStack Query + SSE stream reader              │
                  └───────────────┬────────────────────────────────────┘
                                  │  (browser, no token)
                  ┌───────────────▼────────────────────────────────────┐
                  │   Next proxy  /api/proxy/[...path]                  │
                  │   • injects Bearer from NextAuth cookie            │
                  │   • routes  ai/*  → AI_SERVICE_URL                 │
                  │             else  → BACKEND_URL                    │
                  │   • streams response.body (SSE passthrough)       │
                  └──────┬───────────────────────────────┬─────────────┘
                         │ ai/*                           │ everything else
            ┌────────────▼─────────────┐      ┌───────────▼──────────────┐
            │  AI process group         │      │  API process group        │
            │  serves /ai/* (uvicorn)   │      │  serves everything else   │
            │  • domains/ai/ (async)    │      │  • journal/trades/...     │
            │  • SSE /ai/.../stream     │      │  • post-sync Celery:      │
            │  • LangGraph compiled once│      │     bump data_version,    │
            │  • quota guard            │      │     enqueue insights      │
            │  • Redis tool cache       │      └───────────┬──────────────┘
            │  • own DB pool            │                  │
            │  ── same image & codebase as the API group ──│
            └───┬───────────┬───────────┘                  │
                │           │                               │
       ┌────────▼──┐  ┌─────▼───────┐            ┌──────────▼────────┐
       │  OpenAI    │  │   Redis     │◄───────────┤  Celery worker     │
       │ gpt-4.1-   │  │ • tool cache│  bump ver  │ • data_version++   │
       │ mini       │  │ • quotas    │            │ • recompute        │
       └────────────┘  │ • rate lim  │            │   insights         │
                       └─────┬───────┘            └──────────┬─────────┘
                             │                               │
                       ┌─────▼───────────────────────────────▼─────────┐
                       │        Shared Postgres (+ PgBouncer)           │
                       │  trades, journals, accounts (AI reads)         │
                       │  ai_chat_sessions / _messages (AI writes)      │
                       │  ai_insights, ai_usage                         │
                       │  langgraph checkpoints                         │
                       └────────────────────────────────────────────────┘

One codebase/image, two process groups, one shared data tier. The AI group and
the API group don't share an event loop or worker pool — only code, Postgres, and
Redis. In dev you can run a single process (`AI_SERVICE_URL == BACKEND_URL`).
```

---

## 4. Frontend component structure

New feature module `src/features/ai/`, mirroring the existing `features/journal`
convention (feature-sliced, TanStack Query, Zustand, shadcn):

```
src/features/ai/
  api/
    ai.api.ts                 # sessions CRUD + sendMessage(stream)
  hooks/
    use-ai-sessions.ts        # list/create/delete sessions
    use-ai-chat.ts            # send + SSE stream consumer, optimistic msgs
    use-ai-dock.ts            # zustand: dock open/closed, fullscreen, context
    use-ai-mode.ts            # zustand: per-rail AI toggle state
  store/
    ai-chat-store.ts          # streaming buffer, active session (shared dock+page)
  components/
    ai-chat-core.tsx          # SHARED engine UI: message list + composer + stream
    ai-dock.tsx               # floating right-side dock (navbar entry)
    ai-dock-header.tsx        # history / expand-fullscreen / options / close
    ai-chat-page.tsx          # full page (/ai route) — wraps ai-chat-core
    ai-session-sidebar.tsx    # history list (Zella's left rail; page + dock history)
    ai-message-list.tsx       # markdown bubbles + tool/thinking states
    ai-composer.tsx           # input + send; "Ask anything about your trading…"
    ai-greeting.tsx           # "Hey {firstName}" + capability blurb (empty state)
    ai-context-chip.tsx       # "Opened from: Dashboard" pill
    ai-suggested-prompts.tsx  # "What's hurting my performance?" etc.
    ai-action-chips.tsx       # "⚡ Take Action" row → capability actions
    ai-insight-card.tsx       # precomputed insight card
  lib/
    sse.ts                    # fetch-based SSE reader (ReadableStream)
    markdown.tsx              # safe markdown render
    ai-disclaimer.tsx         # "AI can make mistakes. Not financial advice."
  types.ts
  index.ts
```

`ai-chat-core.tsx` is the single source of truth for the conversation UI. Both
`ai-dock.tsx` and `ai-chat-page.tsx` render it; they differ only in chrome
(positioning, header, width). This guarantees the dock and the full page never
drift apart and that expand-to-fullscreen is seamless (same store, same session).

**Reuse the existing chat rail.** `journal-day-chat-rail.tsx` is already a fully
controlled component — the AI hook props (`prompts`, `onPromptClick`,
`onContextChange`, `chatContext`) are *already declared and currently `void`-ed*.
We add:
- An **"Ask AI" Switch** (`@radix-ui/react-switch`, already a dependency) in the
  rail header.
- When AI mode is on, the page controller (`journal-day-chat-page.tsx`) routes
  `onSend` to `use-ai-chat` instead of the journal messages API, and renders
  streamed assistant bubbles with distinct styling.

### Two entry points

**A. Navbar "Ask Partna AI" → floating dock** (the stub already exists —
`header.tsx` wires `useAiInsightModal().open`, and `ai-insight-modal.tsx` is a
**hardcoded mock** we replace). The dock is a right-side panel, **not** a centred
modal — it doesn't block the page, so the trader can reference the dashboard while
chatting. We rename the provider to `AiDockProvider` and keep the
`open(context?)` API. Dock anatomy, copied from Zella AI:

- **Header bar:** `history` (session list), `expand` (→ fullscreen / `/ai`),
  `options` (kebab: clear, new chat), `close`.
- **Greeting empty-state:** "Hey {firstName}" + one-line capability blurb
  ("I don't just analyze your trades — I can tag them, build playbooks, track your
  rules…").
- **Context chip:** "Opened from: {Dashboard|Journal|Trade|Day view|Trade
  History|Reports|Backtesting}" — the calling surface passes its context to
  `open()`, so the agent knows where the user is and scopes accordingly. (Zella is
  context-aware from Trade detail, Notes, Reports, Dashboard, Day view,
  Backtesting and Strategies — we mirror this everywhere we have an equivalent.)
- **Keyboard shortcut:** a global hotkey opens the dock from anywhere (Zella uses
  ⌘/Ctrl + Z then Enter; we'll pick a non-conflicting combo, e.g. ⌘/Ctrl + J, and
  show the hint in the composer like the existing `⌘` affordance in the mock).
- **Suggested prompts:** 2–3 context-aware starters
  ("What's hurting my performance?", "Summarize my recent trades").
- **⚡ Take Action chips:** capability shortcuts that map to real actions
  ("Tag my last 20 trades by setup", "Find and flag my worst pattern",
  "Check my last trades against my playbook", "Add a rule to block my worst
  setup"). See *Capabilities* below for how these resolve.
- **Composer:** "Ask anything about your trading…" + disclaimer (adapted from
  Zella's): *"Partna AI uses your SyncTrades data to answer questions and run the
  actions you ask for. It can make mistakes, so review anything it changes before
  relying on it — and nothing it says is financial advice."*
- **Expand** swaps the dock for the full page at `/ai`, preserving the active
  session (shared store).

**B. Sidebar "Partna AI" tab → full page.** Add a nav item to
`src/components/layout/sidebar.tsx` (with a "Beta" badge, matching the existing
PropFirm Sync treatment) routing to `src/app/(dashboard)/ai/page.tsx` →
`<AiChatPage/>`. The page is the focused workspace: persistent
`ai-session-sidebar` (history), full-width `ai-chat-core`, room for richer
rendering (tables, equity snapshots). Same engine, same sessions as the dock.

### Capabilities & the "Take Action" model

The prototype's 16 tools are **read-only** (analysis, breakdowns, pattern
detection). Zella's "Take Action" chips — *tag trades*, *check against playbook*,
*add a rule* — are **write** actions. We add these as a second, gated tool class:

| Chip | New capability | Backs onto |
|---|---|---|
| Tag my last 20 trades by setup | `tag_trades(trade_ids, tags)` | journal tags API (system already has tag categories/options) |
| Find and flag my worst pattern | read-only `detect_patterns` + flag | existing tool + a saved-insight write |
| Check my last trades against my playbook | `evaluate_against_playbook` | needs a Playbook/Rules model (new) |
| Add a rule to block my worst setup | `create_rule(rule)` | needs a Rules model (new) |

Rules for write tools:
- **Confirm before mutating.** A write tool returns a *proposed action* (a
  preview); the FE renders a confirm card ("Tag these 20 trades as *Breakout*?
  [Confirm] [Cancel]"). Only on confirm does the AI service call the core API
  (over HTTP, with the user's token) to apply it. The agent never silently writes.
- **Capability flags.** Write tools are feature-flagged; launch can ship
  read-only + tagging, and defer playbooks/rules to a later phase (they need new
  core-API models — out of scope for AI MVP; tracked in §12).
- **Audit.** Every applied action writes a row (who/what/when) for reversibility.

This keeps the MVP honest: the chips that work day-one are the read-only and
tagging ones; playbook/rule chips ship when the underlying core-API models exist.

---

## 5. Data flow

### Full chatbot (streaming)
```
1. User types → use-ai-chat.send()
2. Optimistic: push user msg + empty assistant msg into store
3. POST /api/proxy/ai/sessions/{id}/stream   (proxy injects JWT, routes to AI proc)
4. AI: quota check → load session → LangGraph astream (account_ids via config)
5. SSE back:
     data: {"type":"token","v":"Your "}
     data: {"type":"tool","name":"get_breakdown"}
     data: {"type":"done","message_id":"..."}
6. FE appends tokens to the assistant bubble live
7. AI persists user+assistant rows; LangGraph checkpoint persists agent memory
```

### In-context rail AI
Same pipeline, but the session is keyed by context:
```
context = { type: "journal_day"|"trade", ref: "<date>"|"<trade_id>", account_id }
```
The AI service upserts a context-scoped session (so re-opening the day shows the
thread). The agent's system prompt gets the context injected → tools auto-scope.

### Proactive insights (no live LLM on the read path)
```
Sync completes → core backend Celery emits → AI worker recomputes insights for
that account → writes ai_insights rows → home/journal reads them instantly.
```

---

## 6. API design (AI service — `/ai/*`)

Keeps the prototype's REST surface; **adds streaming + context**.

| Method | Path | Purpose | Notes |
|---|---|---|---|
| `POST` | `/ai/sessions` | create session | optional `context_type`, `context_ref`, `account_id` |
| `GET` | `/ai/sessions` | list (paginated) | history sidebar |
| `GET` | `/ai/sessions/{id}` | session + messages | |
| `DELETE` | `/ai/sessions/{id}` | soft delete | |
| `POST` | `/ai/sessions/{id}/stream` | **send + SSE stream** | primary path |
| `POST` | `/ai/sessions/{id}/message` | non-stream fallback | mobile / retries |
| `GET` | `/ai/sessions/by-context` | resolve/create context session | `?type=journal_day&ref=2026-06-13&account_id=` |
| `GET` | `/ai/insights` | precomputed cards | `?account_id=&kind=` |
| `GET` | `/ai/suggestions` | suggested prompts | static + data-aware |
| `GET` | `/ai/usage` | quota status | drives "limit reached" UI |

### SSE event protocol (the FE contract)
```
data: {"type":"token","v":"..."}          # incremental text
data: {"type":"tool","name":"..."}        # tool started ("Analyzing trades…")
data: {"type":"error","detail":"..."}     # graceful failure
data: {"type":"done","message_id":"..."}  # end, persisted id
```

### Proxy change (`sync-trades-fe/src/app/api/proxy/[...path]/route.ts`)
The proxy already injects the JWT server-side and streams `response.body`. We only
add prefix-based routing:

```ts
const AI_PREFIX = "ai/";
function resolveTargetBase(joinedPath: string) {
  if (joinedPath.startsWith(AI_PREFIX) && process.env.AI_SERVICE_URL) {
    return process.env.AI_SERVICE_URL.replace(/\/$/, "");
  }
  return resolveBackendUrl();
}
// in proxyRequest: const backendUrl = resolveTargetBase(joinedPath);
```
`ai/` is **not** in `PUBLIC_BACKEND_PREFIXES`, so the proxy enforces a session and
overwrites Authorization from the encrypted NextAuth cookie. No client can inject
a token. When the AI runs in-process (single process group), set
`AI_SERVICE_URL == BACKEND_URL` and the same app serves `/ai/*`; when it runs as a
dedicated process group, point `AI_SERVICE_URL` at that group. The FE contract is
identical either way.

---

## 7. Database schema

Extend the prototype's tables; add the rest. These ship as **one migration in the
single Alembic chain** (not a separate branch), and every model is added to
`app/models/__init__.py` so autogenerate sees it. AI reads core tables; it owns
the `ai_*` tables. Every new query pattern ships with its index in the same
migration (RULES §3), and `ai_chat_messages` (unbounded growth) ships with a
**retention/purge job** from day one (RULES §3).

> ⚠️ **PgBouncer × LangGraph checkpointer — read before writing the checkpointer.**
> We run behind PgBouncer in **transaction pooling** mode (RULES §3): no
> session-scoped state, no reliance on server-side prepared statements. LangGraph's
> `langgraph-checkpoint-postgres` (psycopg) will break under that pooler unless you
> either (a) point the checkpointer at **`DATABASE_URL_DIRECT`** (bypasses
> PgBouncer — the env var already exists and is what Alembic uses), or (b)
> configure psycopg with `prepare_threshold=None` and `autocommit=True`. Prefer
> (a) for the checkpointer's own small pool. The **analytics tools** use the normal
> pooled session but keep it short-lived (open → query → close, never held across
> an LLM call) and carry **no session state** — so they stay pooler-safe.

```sql
-- existing (from ai repo), extended:
ALTER TABLE ai_chat_sessions
  ADD COLUMN context_type    VARCHAR(20) DEFAULT 'general' NOT NULL, -- general|journal_day|trade
  ADD COLUMN context_ref     VARCHAR(64),                            -- date string or trade_id
  ADD COLUMN last_message_at TIMESTAMPTZ;

CREATE UNIQUE INDEX uq_ai_session_context
  ON ai_chat_sessions (user_id, context_type, context_ref, account_id)
  WHERE is_deleted = false AND context_type <> 'general';

CREATE INDEX ix_ai_sessions_user_recent
  ON ai_chat_sessions (user_id, last_message_at DESC) WHERE is_deleted = false;

ALTER TABLE ai_chat_messages
  ADD COLUMN input_tokens  INT,
  ADD COLUMN output_tokens INT,
  ADD COLUMN meta JSONB;          -- tool calls, model, latency

-- per-user usage, metered in CREDITS (Zella's model: monthly credit allowance,
-- not raw message counts) plus raw tokens for cost reconciliation
CREATE TABLE ai_usage (
  user_id        UUID NOT NULL,
  period_month   DATE NOT NULL,        -- first day of the billing month
  credits_used   INT  DEFAULT 0,       -- the metered unit shown to the user
  message_count  INT  DEFAULT 0,
  input_tokens   BIGINT DEFAULT 0,
  output_tokens  BIGINT DEFAULT 0,
  cost_cents     INT DEFAULT 0,
  PRIMARY KEY (user_id, period_month)
);

-- cross-session user memory ("What do you know about me?"). Distinct from the
-- per-thread LangGraph checkpoints: durable facts about the trader (style, goals,
-- recurring mistakes) injected into the system prompt on every turn.
CREATE TABLE ai_user_memory (
  user_id    UUID PRIMARY KEY,
  profile    JSONB NOT NULL DEFAULT '{}',  -- {style, goals[], recurring_mistakes[], prefs}
  updated_at TIMESTAMPTZ NOT NULL
);

-- background agents (Zella's "My Agents": auto-tagger, session review, sentiment
-- briefing, "Start My Day"). Config + enablement per user; executed by Celery.
CREATE TABLE ai_agents (
  id          UUID PRIMARY KEY,
  user_id     UUID NOT NULL,
  kind        VARCHAR(40) NOT NULL,   -- auto_tagger|session_review|sentiment|start_my_day
  enabled     BOOLEAN DEFAULT true,
  config      JSONB DEFAULT '{}',
  last_run_at TIMESTAMPTZ,
  created_at  TIMESTAMPTZ NOT NULL
);
CREATE INDEX ix_ai_agents_user ON ai_agents (user_id, kind);

-- precomputed insight cards (no live LLM on read path)
CREATE TABLE ai_insights (
  id           UUID PRIMARY KEY,
  user_id      UUID NOT NULL,
  account_id   UUID,
  kind         VARCHAR(40) NOT NULL,   -- worst_pattern|best_setup|daily_recap|risk_flag
  payload      JSONB NOT NULL,         -- {title, body, tags, severity, cta}
  data_version BIGINT NOT NULL,        -- account data_version it was built on
  generated_at TIMESTAMPTZ NOT NULL,
  valid_until  TIMESTAMPTZ
);
CREATE INDEX ix_ai_insights_user ON ai_insights (user_id, account_id, kind);

-- langgraph checkpoints: managed by langgraph-checkpoint-postgres
```

`data_version` is the keystone of cache correctness (see §8).

---

## 8. Caching strategy

Five layers. TTL alone is wrong for financial data — a stale P&L is worse than a
slow one — so each layer has explicit invalidation.

### 8.1 Tool-result cache (Redis — the big one)
The 16 tools run aggregations over trades. Cache
`key = sha1(tool_name : sorted(args) : sorted(account_ids) : data_version)`.
`data_version` is a per-account integer in Redis, **bumped by the core backend's
post-sync Celery task**. New trades → version bumps → stale keys are never read
again (and expire by TTL ~6h).

```python
# src/app/domains/ai/cache.py
import hashlib, json, redis.asyncio as redis
from app.core.config import settings

r = redis.from_url(settings.AI_REDIS_URL, decode_responses=True)

async def data_version(account_ids: list[str]) -> str:
    if not account_ids:
        return "0"
    vals = await r.mget([f"acct:ver:{a}" for a in account_ids])
    return ":".join(v or "0" for v in vals)

def tool_cache(ttl: int = 6 * 3600):
    def deco(fn):
        async def wrapper(*, account_ids, **kwargs):
            ver = await data_version(account_ids)
            raw = f"{fn.__name__}:{json.dumps(kwargs, sort_keys=True, default=str)}:{sorted(account_ids)}:{ver}"
            key = "tool:" + hashlib.sha1(raw.encode()).hexdigest()
            if (hit := await r.get(key)) is not None:
                return json.loads(hit)
            result = await fn(account_ids=account_ids, **kwargs)
            await r.set(key, json.dumps(result, default=str), ex=ttl)
            return result
        return wrapper
    return deco
```

Core backend bumps the version inside its existing sync task:
```python
# synctrades-be: after a successful account sync
redis_client.incr(f"acct:ver:{account_id}")
celery_app.send_task("ai.recompute_insights", args=[str(account_id)])
```

### 8.2 LLM prompt caching
The system prompt (`prompts.py`) is ~1.5k static tokens. Use OpenAI automatic
prefix caching (keep the static block first, dynamic account IDs/context last) so
we don't pay full input tokens every turn.

### 8.3 Precomputed insights (`ai_insights`)
Home chips and "worst pattern" are **not** computed live for 50k users — read from
the table, rebuilt only on sync. This is the difference between 50k live LLM calls
per dashboard load and ~0.

### 8.4 Session/message read cache
TanStack Query on the FE + short Redis cache on `GET /ai/sessions`. Messages are
append-only → cache per `last_message_at`.

### 8.5 Suggested prompts
Static set + small data-aware set computed during insight precompute; cached per
`account + data_version`.

---

## 9. Productionizing the engine

The prototype's `agent.py` has three issues that hurt at scale. Fix all three.

### 9.1 Compile the graph once; inject `account_ids` at runtime
Today the prototype rebuilds the system prompt → graph on **every** call.

```python
# src/app/domains/ai/agent.py
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

_llm_with_tools = ChatOpenAI(model=settings.AI_MODEL, temperature=0).bind_tools(ALL_TOOLS)
_checkpointer: AsyncPostgresSaver | None = None
_compiled = None  # compiled ONCE at startup

def _copilot_node(state, config: RunnableConfig):
    account_ids = config["configurable"]["account_ids"]
    ctx = config["configurable"].get("context_block", "")
    system = SystemMessage(content=f"{SYSTEM_PROMPT}\n{_ids_block(account_ids)}\n{ctx}")
    return {"messages": [_llm_with_tools.invoke([system] + state["messages"])]}

def build_compiled():
    g = StateGraph(MessagesState)
    g.add_node("copilot", _copilot_node)
    g.add_node("tools", ToolNode(ALL_TOOLS))
    g.add_edge(START, "copilot")
    g.add_conditional_edges("copilot", tools_condition)
    g.add_edge("tools", "copilot")
    return g.compile(checkpointer=_checkpointer)
```

### 9.2 Stream with SSE + async
Today it blocks on `compiled.invoke` and holds the worker for the whole turn.

```python
# src/app/domains/ai/router.py
from fastapi.responses import StreamingResponse

@router.post("/sessions/{session_id}/stream")
async def stream(session_id: uuid.UUID, body: MessageRequest,
                 user_id: uuid.UUID = Depends(get_current_user_id)):
    await quota.check(user_id)
    account_ids, ctx = await service.prepare_turn(session_id, user_id, body.content)
    config = {"configurable": {"thread_id": f"{user_id}_{session_id}",
                               "account_ids": account_ids, "context_block": ctx}}

    async def gen():
        full = []
        try:
            async for ev in _compiled.astream_events(
                {"messages": [("human", body.content)]}, config=config, version="v2"):
                kind = ev["event"]
                if kind == "on_chat_model_stream":
                    tok = ev["data"]["chunk"].content
                    if tok:
                        full.append(tok)
                        yield _sse({"type": "token", "v": tok})
                elif kind == "on_tool_start":
                    yield _sse({"type": "tool", "name": ev["name"]})
            msg_id = await service.persist_assistant(session_id, "".join(full))
            await quota.record(user_id, "".join(full))
            yield _sse({"type": "done", "message_id": str(msg_id)})
        except Exception:
            yield _sse({"type": "error", "detail": "Something went wrong."})

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"})

def _sse(d): import json; return f"data: {json.dumps(d)}\n\n"
```

### 9.3 Async DB + async checkpointer + async tools
So a slow LLM call yields the event loop instead of pinning a thread. Run uvicorn
with an async worker; size concurrency to outbound LLM limits, not CPU. Keep tool
DB sessions short (open → query → close, never held across an LLM call).

---

## 10. Scale & cost controls for 50k users (non-negotiable)

- **Per-user monthly CREDIT allowance (Redis + `ai_usage`), checked before every
  turn**, gated by plan — this mirrors Zella's model (Essential 500 credits/mo,
  Pro 1,000/mo, reset monthly, non-increasable) and aligns metering with billing.
  A turn debits credits by cost (a deep multi-tool analysis costs more than a
  lookup), so the unit users see maps to what we actually spend:
  ```python
  # src/app/domains/ai/quota.py
  MONTHLY_CREDITS = {"free": 50, "essential": 500, "pro": 1000}  # from settings.AI_CREDITS_*
  async def check(user_id):
      plan = await plans.get(user_id)
      used = int(await r.get(f"ai:cred:{user_id}:{month()}") or 0)
      if used >= MONTHLY_CREDITS[plan]:
          raise HTTPException(402, "Monthly AI credits used up. Upgrade for more.")

  async def debit(user_id, credits: int):
      key = f"ai:cred:{user_id}:{month()}"
      if await r.incrby(key, credits) == credits:
          await r.expire(key, 40 * 86400)   # safety TTL past month end
      # mirror to ai_usage for billing reconciliation
  ```
  Credit cost per turn ≈ `ceil(tokens / N) + tool_calls` (tune N per model). Show
  remaining credits in Settings (Zella surfaces this in Subscription → Usage).
- **Global concurrency limiter / circuit breaker** on outbound LLM calls (asyncio
  semaphore + provider-429 backoff): a spike degrades to "AI is busy, try again"
  instead of melting the bill.
- **Cap `max_tokens`; truncate/summarize long threads** (LangGraph state trimming).
- **Precompute, don't live-compute** dashboard surfaces (§8.3).
- **Separate small DB pool behind PgBouncer** for the AI process, so LLM-bound
  requests can't exhaust the core API's pool.
- **Observability:** keep LangSmith (already wired in `config.py`); log per-turn
  latency/tokens/cost into `ai_usage`; add a kill-switch env flag to disable the
  AI surface instantly.
- **Edge rate limit** on the stream endpoint (slowapi pattern already in use),
  e.g. `12/minute/user`, independent of the daily quota.
- **Model routing (post-launch):** cheap model default, escalate to a stronger
  model only for explicit "deep analysis."

---

## 11. Memory, Agents & "Start My Day"

Three Zella concepts that go beyond one-off chat. All are **post-MVP** but the
schema (§7) is laid now so we don't migrate twice.

### 11.1 Cross-session memory ("What do you know about me?")
LangGraph checkpoints give *per-thread* memory; Zella also keeps *durable*
trader-level facts (style, goals, recurring mistakes) across all conversations.
We store these in `ai_user_memory.profile` (JSONB) and inject a compact summary
into the system prompt every turn. Writes happen two ways: explicit ("remember
that I trade only London session") and a periodic distillation agent that mines
recent sessions/journals for stable facts. Expose "What do you know about me?" as
a built-in prompt that simply renders the profile.

### 11.2 Agents (background automation — Zella's "My Agents")
Scheduled/triggered Celery jobs in `ai-copilot`, configured per user via
`ai_agents`:
- **Trade Auto-Tagger** — on trade import, tag new trades by setup. First write
  capability with real leverage (reuses the *Capabilities* `tag_trades` path; runs under a
  per-user budget, results reviewable).
- **Session Review** — narrative win/mistake/theme analysis after a trading day;
  writes an `ai_insights` row (kind=`daily_recap`).
- **Market Sentiment Briefing** — scheduled market snapshot (this is the only
  surface that needs *external* market data, not just the trader's own).
- These reuse the same engine + tools; an agent run is just a server-initiated
  turn with no human in the loop, debited from credits like any other.

### 11.3 "Start My Day"
A guided daily flow: pull yesterday's performance + open positions + rule
adherence → generate a game plan (prepare/trade/reflect checklist). Implemented as
a `start_my_day` agent producing a structured insight the dashboard renders. This
is the proactive bookend to Session Review.

---

## 12. Implementation phases

1. **Domain scaffold & port** — create `src/app/domains/ai/` in the layering of
   §0; port the prototype's agent/tools/prompts, fixing every violation in the §0
   table; register models in `app/models/__init__.py`; ship §7 schema as one
   migration on the current head; reuse `get_current_user`. *Acceptance:* `GET
   /ai/sessions` and a non-stream `POST .../message` work end-to-end through the
   app, auth via `get_current_user`, no RULES violations, checkpointer on
   `DATABASE_URL_DIRECT`. *(engine reachable, in-process)*
2. **Productionize engine** — §9 (compile-once, async, SSE) + §10 (quota, breaker,
   token caps). *(scales)*
3. **Proxy + FE surfaces** — proxy `ai/*` routing; build `features/ai/` with the
   shared `ai-chat-core`; ship the **floating dock** (replace the mock modal) and
   the **sidebar "Partna AI" tab → `/ai` full page** with expand-to-fullscreen
   handoff. Greeting, context chip, suggested prompts, read-only Take Action chips.
   *(both entry points work)*
4. **Caching** — §8 tool cache + `data_version` wiring in the core sync task +
   prompt caching. *(fast & cheap)*
5. **In-context rail AI** — `Switch` in journal day/trade rails; context-scoped
   sessions; streamed assistant bubbles. *(journaling vs. ask-AI)*
6. **Write capabilities** — gated write tools + confirm-before-mutate cards,
   starting with `tag_trades` (backs onto existing journal tags). *(Take Action)*
7. **Proactive insights** — post-sync Celery recompute → `ai_insights` → home
   chips & cards. *(TradeZella parity)*
8. **Memory & Agents** — `ai_user_memory` injection + "What do you know about me?";
   Trade Auto-Tagger, Session Review, Start My Day (§11). *(deep parity)*

**MVP = phases 1–3** (working dock + full page, streaming, read-only). Phases 4–8
harden, deepen, and add actions/automation.

---

## 13. Open questions / follow-ups

- Plan tiers & exact daily message limits (needs product input).
- Whether in-context rail AI messages persist in `ai_chat_sessions` (recommended)
  or are ephemeral.
- Insight `kind` taxonomy and refresh cadence (per-sync vs. nightly batch).
- **Playbook & Rules data models** in the core API — prerequisite for the
  "check against playbook" and "add a rule" Take Action chips. Not part of the AI
  MVP; needs its own design.
- Dock vs. full-page: confirm the dock should always deep-link to `/ai` on expand
  (vs. a borderless fullscreen overlay). Spec assumes the former (simpler, shareable URL).
