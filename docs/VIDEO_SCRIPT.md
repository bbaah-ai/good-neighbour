# 3-Minute Video Walkthrough — *The Good Neighbour*

> **Runtime:** 2:45–3:00 · **Audience:** course reviewers and Google Cloud leadership
> **One idea to land:** *Code owns the rules, Gemini handles language and judgment, people make the final call.*

---

## Setup

| Window | Ready to show |
|---|---|
| **Deck A:** *Downtown Toronto Pilot* (Slide Ops) | Slide 3 (hunger vs. waste), Slide 5 (corridor map), Slide 6 (flow) |
| **Deck B:** *Architecture and Trade-Offs* (Slide Ops) | Slide 5 (safety layers), Slide 7 (production proof) |
| **Terminal 1** (repo root, `.venv` active) | `pytest -q` |
| **Terminal 2** | Demo A and Demo B commands below |
| **Editor** | `good_neighbour/agent.py`, `nodes.py`, `RUBRIC.md` |

```bash
# Demo A: fast path (phone number gets redacted, no model calls after intake)
python scripts/run_local.py "12 loaves of sourdough bread at First Canadian Place food court, ambient, call 416-555-0144"
# Demo B: perishable path (Flash match -> Pro safety review -> human approval)
python scripts/run_local.py "55 hot trays of braised beef and rice at MTCC South Building prepared 90 minutes ago"
```

---

## Script

### 0:00–0:25 · The problem *(Deck A, Slides 3 → 5)*
> "I'm Brian Baah. Toronto food banks logged a record 4.1 million visits in a year, and Canada wastes almost 9 million tonnes of edible food. Downtown, the kitchens and the drop-ins are only about a kilometre and a half apart. The gap isn't supply. It's the match. A tray of hot food spoils while someone reads a text and phones around. So I built **The Good Neighbour**."

### 0:25–0:45 · The design idea *(Deck A, Slide 6)*
> "One principle drives the design: **code owns the rules, Gemini handles language and judgment, and people make the final call.** It's an ADK 2.0 workflow graph, so the model can't skip a step."

### 0:45–1:10 · Fast path *(Terminal 2: Demo A, then `agent.py`)*
> "Every message is screened first, before any model sees it. Personal data is stripped and prompt attacks are blocked. Gemini 2.5 Flash turns the text into a typed record. Then plain code decides the route. Twelve loaves of bread is small and shelf-stable, so it's matched and sent with no further model calls. And see, the phone number was redacted at intake."

### 1:10–1:50 · Perishable path and human gate *(Terminal 2: Demo B, type `approve`; then `nodes.py`)*
> "Fifty-five hot trays is perishable, so it takes the review path. Flash finds drop-ins with fridge space, using strict tool schemas that return a recovery hint when a search comes up empty. Gemini 2.5 Pro then reviews the time and temperature risk. The workflow pauses for a coordinator. Even if a person clicks approve, code refuses anything the review marked unsafe. I'll approve, and the dispatch fires once, with no double alerts."

### 1:50–2:20 · Safety, memory and audit trail *(Deck B, Slide 5; JSON logs)*
> "That's three independent safety layers. Sessions persist across the human pause and are compacted every five turns. Memory is written in the background so it never slows a reply. Every action logs an INTENT before and an OUTCOME after, traced in Cloud Trace, with personal data filtered out."

### 2:20–3:00 · Proof it runs *(Terminal 1: `42 passed`; Deck B, Slide 7; `RUBRIC.md`)*
> "42 tests run offline in under two seconds and cover every branch of the graph. An 8-case eval grades real Gemini output. Terraform sets up least-privilege infrastructure, and keys live in Secret Manager. All 19 rubric rows map to code in RUBRIC.md. The next step is a 90-day pilot with one sponsor, ten kitchens and six drop-ins. Thanks for watching."
