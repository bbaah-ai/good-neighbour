# 3-Minute Video Walkthrough Script — *The Good Neighbour*

> **Target runtime:** 2:45 – 3:00 (optional YouTube link on the assessment submission form)
> **Goal:** Walk a human or automated reviewer through all 5 pillars of the **AgentOps Code Review Matrix (95 pts)** with live terminal proof.

---

## Pre-Recording Setup (2 terminals + browser tab)

1. **Terminal 1 (Repo root, `.venv` active):**
   ```bash
   cd good-neighbour && source .venv/bin/activate
   pytest -q
   ```
2. **Terminal 2 (Ready to run the two live demos):**
   ```bash
   # Demo A — Fast path (zero downstream LLM tokens):
   python scripts/run_local.py "12 loaves of sourdough bread at First Canadian Place food court, ambient, call 416-555-0144"

   # Demo B — Perishable lot (Flash matcher -> Pro risk review -> HITL pause/resume):
   python scripts/run_local.py "55 hot trays of braised beef and rice at MTCC South Building prepared 90 minutes ago"
   ```
3. **Editor / Browser tabs open to:**
   * [`README.md`](../README.md) (Mermaid architecture graph visible)
   * [`RUBRIC.md`](../RUBRIC.md) (19-row traceability table)
   * *The Good Neighbour — Downtown Toronto Pilot* Google Slides deck (cover + target map slide)

---

## Script & Shot List

### `0:00 – 0:30` | Hook & Problem: Downtown Toronto Surplus Food Dispatch
* **On screen:** Google Slides deck (Slide 3 "The Paradox" → Slide 8 "Downtown Toronto Target Map") then switch to [`README.md`](../README.md) Mermaid diagram.
* **Voiceover:**
  > "Hi, I'm Brian Baah. For the AI in 5 Days capstone, I built **The Good Neighbour** — an open-source, vendor-neutral AI dispatch layer for surplus food, designed around a Downtown Toronto pilot.
  >
  > Toronto food banks recorded 4.1 million visits last year, while convention centres, hotels, and underground PATH food courts sit less than two kilometres from Downtown East drop-ins. Existing food-rescue marketplaces handle scheduled grocery pallets well, but small, perishable catering lots often expire before a coordinator can manually triage a text message, verify refrigeration capacity, and calculate food-safety holding windows.
  >
  > The Good Neighbour solves that using an **ADK 2.0 `Workflow` graph** that combines deterministic policy nodes with three specialized Gemini agents."

---

### `0:30 – 1:05` | Pillar 1 & 3: Guardrails, Strategic Model Routing & Zero-LLM Fast Path
* **On screen:** Open [`good_neighbour/agent.py`](../good_neighbour/agent.py) (`build_workflow`) alongside Terminal 2 running **Demo A**.
* **Voiceover:**
  > "Every submission enters `screen_submission` first — a deterministic node that scrubs PII like phone numbers and emails and blocks prompt-injection heuristics before any model is called.
  >
  > Next, `triage_agent` uses **Gemini 2.5 Flash** with strict Pydantic `output_schema=DonationTriage` and the `lookup_donor_site` tool to normalize the offer.
  >
  > Then our zero-token policy router, `route_by_risk`, inspects the triage record. Here in Demo A — 12 loaves of ambient bread at First Canadian Place with a phone number in the message — you can see the phone number was redacted at intake, the router selected `low_risk`, and `auto_match` dispatched the lot to the nearest drop-in with **zero downstream LLM calls**."

---

### `1:05 – 1:45` | Pillar 1 & 3 (cont.): Guided Tool Errors, Pro Risk Review & HITL Gate
* **On screen:** Run **Demo B** in Terminal 2 (`55 hot trays at MTCC`), let it pause at `[Coordinator decision — approve | revise | reject]>`, then type `approve`. Briefly show [`good_neighbour/tools.py`](../good_neighbour/tools.py) and [`good_neighbour/nodes.py`](../good_neighbour/nodes.py) (`coordinator_approval`).
* **Voiceover:**
  > "Now let's run 55 hot-held trays from the Metro Toronto Convention Centre. Because this lot is both perishable and over 20 meals, `route_by_risk` routes to `needs_review`.
  >
  > `matcher_agent` — also on **Gemini 2.5 Flash** — calls `find_recipient_orgs`, `check_org_capacity`, and `load_memory`. Every tool uses strict Pydantic schemas with `extra='forbid'` and returns structured `ToolError` objects with a `recovery_instruction` field so the model can self-correct — for example, widening the search radius if no org is within 5 kilometres.
  >
  > Next, we route to **Gemini 2.5 Pro** for `risk_reviewer_agent`, which calls `estimate_food_safety_window` and evaluates time-and-temperature hazards.
  >
  > Finally, the workflow hits a hard human-in-the-loop pause at `coordinator_approval` using ADK's `RequestInput` and `ResumabilityConfig`. Even if a coordinator types `approve`, the node enforces a hard policy override that refuses approval if the risk level is `unsafe`. When I approve this medium-risk lot, `dispatch_donation` sends an idempotent notification."

---

### `1:45 – 2:20` | Pillar 2 & 4: Sessions, Compaction, Async Memory & Observability
* **On screen:** Show [`good_neighbour/services.py`](../good_neighbour/services.py), [`good_neighbour/observability.py`](../good_neighbour/observability.py), and JSON log lines in Terminal 2.
* **Voiceover:**
  > "For state and memory: locally we persist sessions in SQLite via `DatabaseSessionService` and switch automatically to **Vertex AI Agent Engine Sessions and Memory Bank** in enterprise mode. `App` configures `EventsCompactionConfig` with a 5-invocation sliding window so long coordinator sessions stay compact. And in `record_outcome`, long-term memory writes are scheduled with `asyncio.create_task` so memory consolidation never blocks the turn.
  >
  > Every tool call and dispatch logs structured JSON with explicit `phase='INTENT'` before execution and `phase='OUTCOME'` with latency afterwards, wrapped in OpenTelemetry spans and a root-logger `PiiRedactingFilter`."

---

### `2:20 – 3:00` | Pillar 5: Golden Eval Suite, Offline Graph Tests, Terraform & Secret Manager
* **On screen:** Switch to Terminal 1 showing `42 passed` in `pytest`, plus [`eval/good_neighbour.evalset.json`](../eval/good_neighbour.evalset.json), [`terraform/main.tf`](../terraform/main.tf), and [`RUBRIC.md`](../RUBRIC.md).
* **Voiceover:**
  > "Finally, for production readiness: `secrets.py` hydrates credentials from environment variables or **Google Cloud Secret Manager** with zero hardcoded keys.
  >
  > Our test suite runs 42 unit and full-workflow tests offline in under two seconds, alongside an 8-case ADK golden evaluation dataset in `eval/good_neighbour.evalset.json`.
  >
  > And `terraform/` provisions the least-privilege service account, Secret Manager secret, Pub/Sub intake topic, staging bucket, and an optional Cloud Run push receiver, wired into GitHub Actions CI. Full rubric traceability is in `RUBRIC.md`. Thanks for watching!"
