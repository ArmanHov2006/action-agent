"""FastAPI surface for the Action Agent engine.

POST /run {goal, start_url} -> calls run_agent -> returns {result, trace}.

Run it:  uvicorn api:app
         (NO --reload on Windows: it forces SelectorEventLoop, which can't spawn
          Playwright's browser subprocess -> NotImplementedError. Restart manually.)
Docs at: http://127.0.0.1:8000/docs
"""
from fastapi import FastAPI
from pydantic import BaseModel

from agent_core import run_agent


app = FastAPI()


class RunRequest(BaseModel):
    # TODO: declare the two fields FastAPI should parse from the JSON body.
    goal: str
    start_url: str


@app.post("/run")
async def run(req: RunRequest):
    # TODO 1: await run_agent(...) with the fields off req. (it's async — just await)
    # TODO 2: shape the returned state into {"result": ..., "trace": ...}
    #         result = collected items, trace = action history
    state = await run_agent(goal=req.goal, start_url=req.start_url)
    return {"result": state["collected"], "trace": state["history"]}
