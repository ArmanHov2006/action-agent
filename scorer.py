"""Scorer for runs.jsonl — two layers of truth.

LAYER 1 (default, free, offline): the STOP metric. "passed" = the agent stopped
with outcome 'done' and collected something. This is what the agent THINKS it
achieved. Run: `python scorer.py`

LAYER 2 (opt-in, costs API): the CORRECTNESS metric. An LLM judge reads the goal
and the agent's collected answer and decides whether the answer actually
satisfies EVERY constraint in the goal (e.g. price present AND >=200 reviews AND
>=4.5 stars). This is what the agent ACTUALLY achieved. Run: `python scorer.py --judge`

The gap between the two = how much the stop metric was lying to you. You cannot
improve the agent honestly against the stop metric alone; you optimize it against
correctness.
"""
import json
import os
import sys

# Geo-blocked from Armenia (not a bot block) — agent can never reach these,
# so they are invalid fixtures, not agent failures. Excluded from the rate,
# reported separately. Match against start_url. Confirmed 2026-06-20: all four
# Canadian sites time out on page.goto (net unreachable); only amazon.ca loads.
EXCLUDED_DOMAINS = ("bestbuy.ca", "sportchek.ca", "canadiantire.ca", "cbc.ca")

# Cache so re-running the judge is free and stable: each run is judged once,
# keyed by RUBRIC_VERSION + run_ts. Bump RUBRIC_VERSION when JUDGE_SYSTEM changes
# so old verdicts under a different rubric are not reused.
JUDGMENT_CACHE = "judgments.jsonl"
RUBRIC_VERSION = "r3"  # r3: accept qualifying item (not proof-of-best) + expand K/M counts

JUDGE_MODEL = "gpt-4o-mini"
JUDGE_SYSTEM = (
    "You are a STRICT evaluator of a web agent's answer. You are given the TASK "
    "the agent was set, and the ANSWER the agent collected. Decide whether the "
    "answer satisfies the task's HARD requirements.\n\n"
    "Be strict and evidence-based:\n"
    "- If the task requires a numeric threshold (e.g. >=200 reviews, >=4.5 stars), "
    "the answer must contain EXPLICIT evidence meeting it. Missing evidence = NOT "
    "correct, even if a price is present. Absence of proof is failure.\n"
    "- A bare price with no review/rating evidence does NOT satisfy a task that "
    "demands review/rating thresholds.\n"
    "- Treat a SOFT preference (e.g. 'choose the one with the best reviews') as "
    "satisfied as long as the returned item meets the hard thresholds. Do NOT require "
    "proof that it is the single best-reviewed option that exists.\n"
    "- Interpret abbreviated counts before comparing: '16.7K' = 16700, '4.2K' = 4200, "
    "'1.2M' = 1200000. So '16.7K reviews' satisfies '>=200 reviews'.\n"
    'Respond ONLY with JSON: {"correct": true|false, "reason": "<one sentence>"}.'
)


def score_run(run):
    """LAYER 1 — the stop metric. Did the agent stop claiming success?"""
    collected = run.get("collected", []) or []
    outcome = run.get("outcome")
    passed = outcome == "done" and len(collected) > 0
    wasted = (outcome == "done" and not collected) or outcome == "max_turns"
    return {"passed": passed, "turns_used": run.get("turns_used"), "wasted": wasted}


def load_judgments():
    cache = {}
    if os.path.exists(JUDGMENT_CACHE):
        with open(JUDGMENT_CACHE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                j = json.loads(line)
                cache[j["run_ts"]] = j
    return cache


def judge_run(run, client, cache):
    """LAYER 2 — the correctness metric. Does the answer actually satisfy the goal?

    Returns {"correct": bool, "reason": str}. Cached by run_ts so each run is
    judged exactly once (deterministic + no repeat spend).
    """
    base = run.get("run_ts") or run.get("started_at") or json.dumps(run.get("collected"))
    key = f"{RUBRIC_VERSION}:{base}"  # re-judge when the rubric version changes
    if key in cache:
        return cache[key]

    collected = run.get("collected", []) or []
    if not collected:
        verdict = {"correct": False, "reason": "no answer collected"}
    else:
        answer = " | ".join(str(c) for c in collected)
        resp = client.chat.completions.create(
            model=JUDGE_MODEL,
            temperature=0,                       # deterministic verdicts
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": JUDGE_SYSTEM},
                {"role": "user", "content": f"TASK:\n{run.get('goal','')}\n\nANSWER:\n{answer}"},
            ],
        )
        raw = json.loads(resp.choices[0].message.content)
        verdict = {"correct": bool(raw.get("correct")), "reason": str(raw.get("reason", ""))[:200]}

    # Persist to cache.
    record = {"run_ts": key, **verdict}
    with open(JUDGMENT_CACHE, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
    cache[key] = verdict
    return verdict


def main():
    do_judge = "--judge" in sys.argv
    client = cache = None
    if do_judge:
        from openai import OpenAI
        from dotenv import load_dotenv
        load_dotenv()
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            print("--judge needs OPENAI_API_KEY in .env"); return
        client = OpenAI(api_key=api_key.strip())
        cache = load_judgments()

    passes = correct_n = total = wasted_n = excluded_n = 0
    by_version = {}   # version -> [stop_pass, correct_pass, total]
    by_vt = {}        # (version, task_id) -> [stop_pass, correct_pass, total]
    version_order = []

    with open("runs.jsonl", encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                run = json.loads(line)
            except json.JSONDecodeError:
                print(f"{i}: BAD JSON, skipped")
                continue

            start_url = run.get("start_url", "")
            if any(d in start_url for d in EXCLUDED_DOMAINS):
                excluded_n += 1
                continue

            r = score_run(run)
            total += 1
            passes += r["passed"]
            wasted_n += r["wasted"]

            is_correct = None
            if do_judge:
                v = judge_run(run, client, cache)
                is_correct = v["correct"]
                correct_n += is_correct

            version = run.get("code_version") or "unversioned"
            if version not in by_version:
                version_order.append(version)
            bv = by_version.setdefault(version, [0, 0, 0])
            bv[0] += r["passed"]; bv[1] += (is_correct or 0); bv[2] += 1
            task_id = run.get("task_id") or "?"
            bvt = by_vt.setdefault((version, task_id), [0, 0, 0])
            bvt[0] += r["passed"]; bvt[1] += (is_correct or 0); bvt[2] += 1

            tag = "PASS" if r["passed"] else "FAIL"
            cflag = ""
            if do_judge:
                cflag = "  [CORRECT]" if is_correct else "  [WRONG]"
            print(f"{i:2} {tag}{cflag} turns={r['turns_used']}  {run.get('goal','')[:36]}")

    stop_rate = passes / total * 100 if total else 0
    print(f"\nSTOP rate:    {passes}/{total} = {stop_rate:.1f}%  ({wasted_n} wasted, {excluded_n} excluded)")
    if do_judge:
        correct_rate = correct_n / total * 100 if total else 0
        gap = stop_rate - correct_rate
        print(f"CORRECT rate: {correct_n}/{total} = {correct_rate:.1f}%   <- the honest number")
        print(f"LYING gap:    {gap:.1f}pp  (runs the agent thought it nailed but got wrong)")

    cols = "stop  correct  total" if do_judge else "stop  total"
    print(f"\nby version x task:   ({cols})")
    for (version, task_id), (sp, cp, tt) in sorted(by_vt.items()):
        if do_judge:
            print(f"  {version:<26} {task_id:<16} {sp:>4} {cp:>7} {tt:>6}"
                  f"   stop={sp/tt*100:>5.1f}%  correct={cp/tt*100:>5.1f}%")
        else:
            print(f"  {version:<26} {task_id:<16} {sp:>4} {tt:>6}   stop={sp/tt*100:>5.1f}%")


if __name__ == "__main__":
    main()
