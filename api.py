"""FastAPI surface for the Action Agent engine.

POST /run {goal, start_url} -> calls run_agent -> returns {result, trace}.

Run it:  uvicorn api:app
         (NO --reload on Windows: it forces SelectorEventLoop, which can't spawn
          Playwright's browser subprocess -> NotImplementedError. Restart manually.)
Docs at: http://127.0.0.1:8000/docs
"""

import json
import os
import time
from html import escape   # escape() for untrusted content; distinct from fastapi's HTMLResponse
from pydantic import BaseModel
import secrets
from fastapi import FastAPI, Header, HTTPException, Request, Depends
from fastapi.responses import HTMLResponse
from dotenv import load_dotenv
from agent_core import run_agent
from db import save_run, init_db
from scorer import EXCLUDED_DOMAINS, RUBRIC_VERSION, load_judgments, score_run


load_dotenv()  # read .env at import so os.environ has AGENT_API_KEY before any request
init_db()      # create table + index if not exists (idempotent)

app = FastAPI()

# --- rate limit (task 3): in-memory, per-process. Resets on restart, not shared across workers. ---
RATE_LIMIT = 5        # max requests
WINDOW_SEC = 60       # per this many seconds, per IP
_hits: dict[str, list[float]] = {}   # ip -> list of request timestamps
Qualitative_keywords

def render_run(state: dict) -> str:
    """Render a finished run as HTML.

    Every interpolated value is escape()'d: history `arg`/`why` and `collected` are
    scraped page content + model output (untrusted) -> raw interpolation = stored XSS.
    Order matters: escape FIRST, then add real <br> tags (escaping after would turn your
    own <br> into literal &lt;br&gt;).
    """
    steps = []
    for h in state.get("history") or []:
        action = escape(str(h.get("action", "")))
        arg = escape(str(h.get("arg", "")))
        why = escape(str(h.get("why", ""))).replace("\n", "<br>")  # escape FIRST, then <br>
        steps.append(f"<li><b>{action}</b> <code>{arg}</code><br>{why}</li>")
    collected = "".join(f"<li>{escape(str(c))}</li>" for c in (state.get("collected") or []))
    return (
        f"<h2>Outcome: {escape(str(state.get('outcome', '')))}</h2>"
        f"<h3>Collected</h3><ul>{collected}</ul>"
        f"<h3>Steps</h3><ol>{''.join(steps)}</ol>"
    )


class RunRequest(BaseModel):
    # TODO: declare the two fields FastAPI should parse from the JSON body.
    goal: str
    start_url: str


@app.post("/run")
async def run(req: RunRequest, request: Request, x_api_key: str | None = Header(None)):
    # x_api_key: FastAPI injects the X-API-Key header here (param name underscores -> hyphens).

    # env check (server misconfig -> 500, caller can't fix it)
    if "AGENT_API_KEY" in os.environ:
        api_key = os.environ["AGENT_API_KEY"]
        if not api_key:
            raise HTTPException(status_code=500, detail="AGENT_API_KEY is empty")
    else:
        raise HTTPException(status_code=500, detail="AGENT_API_KEY not set in environment")

    # --- YOUR REP (auth check) ---
    # TODO 1: x_api_key missing (None/empty) -> raise HTTPException(401)   [guard BEFORE compare]
    if not x_api_key:
        raise HTTPException(status_code=401, detail="Missing X-API-Key header")
    # TODO 2: secrets.compare_digest(x_api_key, api_key) is False -> raise HTTPException(401)
    if not secrets.compare_digest(x_api_key, api_key):
        raise HTTPException(status_code=401, detail="Invalid X-API-Key header")
    # --- rate limit (task 3): sliding window, per IP ---
    ip = request.client.host
    now = time.time()
    hits = _hits.setdefault(ip, [])              # this ip's timestamp list (empty first time)
    if len(hits) > 0 and now - hits[0] >= WINDOW_SEC:   # evict: oldest stale -> drop all stale
        hits = [t for t in hits if now - t < WINDOW_SEC]
        _hits[ip] = hits
    if len(hits) >= RATE_LIMIT:                  # decide: at limit -> reject
        raise HTTPException(status_code=429, detail="Rate limit exceeded. Try again later.")
    hits.append(now)                             # append: count this request

    state = await run_agent(goal=req.goal, start_url=req.start_url)
    save_run(state)
    return HTMLResponse(render_run(state))

@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    with open("index.html", encoding="utf-8") as f:
        return HTMLResponse(f.read())

@app.get("/dashboard")
def dashboard():
    if not os.path.exists("runs.jsonl"):
        return {"error": "runs.jsonl not found"}

    judgments = load_judgments()
    total = passes = correct_n = judged_n = excluded_n = wasted_n = assertion_total = assertion_lying = 0

    with open("runs.jsonl", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                run = json.loads(line)
            except json.JSONDecodeError:
                continue

            start_url = run.get("start_url", "")
            if any(d in start_url for d in EXCLUDED_DOMAINS):
                excluded_n += 1
                continue

            if run.get("outcome") == "crashed" and any(
                a.get("action") == "crash" and "Page.goto" in (a.get("message") or "")
                for a in (run.get("history") or [])
            ):
                excluded_n += 1
                continue

            r = score_run(run)
            total += 1
            passes += r["passed"]
            wasted_n += r["wasted"]
            if r["assertion"] is not None:
                assertion_total += 1
                if r["passed"] and r["assertion"] is False:
                    assertion_lying += 1

            base = (
                run.get("run_ts")
                or run.get("started_at")
                or json.dumps(run.get("collected"))
            )
            key = f"{RUBRIC_VERSION}:{base}"
            j = judgments.get(key)
            if j is not None:
                judged_n += 1
                if j.get("correct"):
                    correct_n += 1

    stop_rate = round(passes / total * 100, 1) if total else 0
    correct_rate = round(correct_n / total * 100, 1) if total else None

    return {
        "total_runs": total,
        "excluded": excluded_n,
        "wasted": wasted_n,
        "stop": {"passes": passes, "total": total, "rate_pct": stop_rate},
        "correct": {"passes": correct_n, "judged": judged_n, "total": total, "rate_pct": correct_rate},
        "gap_pp": round(stop_rate - correct_rate, 1) if correct_rate is not None else None,
        "assertion_gate": {
            "lying": assertion_lying,
            "total_with_claim": assertion_total,
            "lying_pct": round(assertion_lying / assertion_total * 100, 1) if assertion_total else None,
        },
    }

def render_history(history: list[dict]) -> str:
    """Render the history of steps as an HTML string."""
    html = "<h1>Agent Run History</h1>"
    for i, step in enumerate(history):
        html += f"<h2>Step {i + 1}: {step.get('action', 'unknown')}</h2>"
        html += f"<p><strong>Message:</strong> {step.get('message', '')}</p>"
        html += f"<p><strong>URL:</strong> {step.get('url', '')}</p>"
        html += f"<p><strong>Outcome:</strong> {step.get('outcome', '')}</p>"
        html += "<hr>"
    return html

