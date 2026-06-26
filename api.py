"""FastAPI surface for the Action Agent engine.

POST /run {goal, start_url} -> calls run_agent -> returns {result, trace}.

Run it:  uvicorn api:app
         (NO --reload on Windows: it forces SelectorEventLoop, which can't spawn
          Playwright's browser subprocess -> NotImplementedError. Restart manually.)
Docs at: http://127.0.0.1:8000/docs
"""

import json
import os

from fastapi import FastAPI
from pydantic import BaseModel

from agent_core import run_agent
from db import save_run
from scorer import EXCLUDED_DOMAINS, RUBRIC_VERSION, load_judgments, score_run


app = FastAPI()


class RunRequest(BaseModel):
    # TODO: declare the two fields FastAPI should parse from the JSON body.
    goal: str
    start_url: str


@app.post("/run")
async def run(req: RunRequest):
    state = await run_agent(goal=req.goal, start_url=req.start_url)
    save_run(state)
    return {"result": state["collected"], "trace": state["history"]}


@app.get("/dashboard")
def dashboard():
    if not os.path.exists("runs.jsonl"):
        return {"error": "runs.jsonl not found"}

    judgments = load_judgments()
    total = passes = correct_n = judged_n = excluded_n = wasted_n = 0

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
    }
