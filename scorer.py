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
import re
import sys
from urllib.parse import urlparse

# Geo-blocked from Armenia (not a bot block) — agent can never reach these,
# so they are invalid fixtures, not agent failures. Excluded from the rate,
# reported separately. Match against start_url. Confirmed 2026-06-20: all four
# Canadian sites time out on page.goto (net unreachable); only amazon.ca loads.
# Only EXCLUDE truly-invalid fixtures (unreachable), not sites the agent fails on.
# bestbuy.ca is geo-blocked from Armenia -> can't run -> invalid fixture, excluded.
# A site the agent reaches but gets wrong is a REAL failure and must stay scored.
EXCLUDED_DOMAINS = ("bestbuy.ca",)

# Qualitative claims the floor gate checks. Extend as new fixture goals are added.
# Goodhart warning: agent can stuff these words into collected strings to game the gate.
# Vision eval (Week 2) closes that gap. For now: cheap string presence = floor.
QUALITATIVE_KEYWORDS = {
    # colors (from sportchek black-shoe fixture)
    "black", "white", "red", "blue", "green", "grey", "gray", "pink", "yellow",
    # materials (from amazon stainless-steel fixture)
    "stainless steel", "leather", "waterproof", "mesh", "synthetic",
    # type attributes (from canadiantire cordless fixture)
    "cordless", "wireless", "portable", "rechargeable",
}

# Cache so re-running the judge is free and stable: each run is judged once,
# keyed by RUBRIC_VERSION + run_ts. Bump RUBRIC_VERSION when JUDGE_SYSTEM changes
# so old verdicts under a different rubric are not reused.
JUDGMENT_CACHE = "judgments.jsonl"
RUBRIC_VERSION = "r5"  # r5: commitment gate — non-done runs can't be CORRECT

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


def to_number(val):
    """Parse a numeric value from a messy string: '16.7K'->16700, '1.2M'->1200000,
    '$29.99'->29.99, '1,234'->1234, '4.5 stars'->4.5. Returns float or None."""
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip().lower().replace(",", "").replace("$", "")
    m = re.search(r"(\d+(?:\.\d+)?)\s*([km])?", s)
    if not m:
        return None
    num = float(m.group(1))
    suffix = m.group(2)
    if suffix == "k":
        num *= 1_000
    elif suffix == "m":
        num *= 1_000_000
    return num


def _find_threshold(goal, keyword):
    """Find the numeric threshold tied to `keyword` in the goal (e.g. the 200 in
    '>=200 reviews' / '200+ reviews' / 'at least 200 reviews', or the 4.5 in
    '4.5 stars' / 'rating of at least 4.5'). Returns float or None."""
    g = goal.lower()
    # number immediately before the keyword: "200+ reviews", ">=4.5 stars"
    m = re.search(rf"(\d+(?:\.\d+)?\s*[km]?)\s*\+?\s*{keyword}", g)
    if m:
        return to_number(m.group(1))
    # number shortly after the keyword: "reviews >= 200", "rating of 4.5"
    m = re.search(rf"{keyword}[^\d]{{0,12}}(\d+(?:\.\d+)?\s*[km]?)", g)
    if m:
        return to_number(m.group(1))
    return None


def parse_thresholds(goal):
    """Pull HARD numeric thresholds out of a goal. Only the unambiguous keywords:
    'reviews' -> review_count, 'stars' -> rating. Returns {field: min_value}."""
    g = (goal or "").lower()
    thr = {}
    rev = _find_threshold(g, "reviews")
    if rev is not None:
        thr["review_count"] = rev
    rat = _find_threshold(g, "stars")
    if rat is not None:
        thr["rating"] = rat
    return thr


def extract_fields_and_sources(collected):
    """Pull price/rating/review_count numbers out of collected items, AND record the
    source_url of the item each field was read from. Prefers the structured JSON
    object the agent now emits (v7); silently skips prose items.

    Returns (fields, sources) where sources[name] = the source_url string of the
    item that supplied fields[name] (None if that item carried no provenance)."""
    fields, sources = {}, {}
    for c in collected:
        obj = c if isinstance(c, dict) else None
        if obj is None and isinstance(c, str) and c.strip().startswith("{"):
            try:
                obj = json.loads(c)
            except Exception:
                obj = None
        if not isinstance(obj, dict):
            continue
        src = obj.get("source_url")
        for k, v in obj.items():
            kl = k.lower()
            if "review" in kl and "source" not in kl and "review_count" not in fields:
                fields["review_count"] = to_number(v); sources["review_count"] = src
            elif ("rating" in kl or "star" in kl) and "rating" not in fields:
                fields["rating"] = to_number(v); sources["rating"] = src
            elif "price" in kl and "price" not in fields:
                fields["price"] = to_number(v); sources["price"] = src
    return fields, sources


def extract_fields(collected):
    """Back-compat thin wrapper — fields only."""
    return extract_fields_and_sources(collected)[0]


def _domain(url):
    """Registrable-ish domain: last two labels of the netloc, lowercased.
    'https://www.amazon.ca/dp/x' -> 'amazon.ca'. Empty string if unparseable."""
    try:
        host = urlparse(url or "").netloc.lower()
    except Exception:
        return ""
    host = host.split("@")[-1].split(":")[0]  # strip auth + port
    labels = [l for l in host.split(".") if l]
    return ".".join(labels[-2:]) if len(labels) >= 2 else host


def score_run(run):
    """LAYER 1 — the stop metric. Did the agent stop claiming success?"""
    collected = run.get("collected", []) or []
    outcome = run.get("outcome")
    passed = outcome == "done" and len(collected) > 0
    wasted = (outcome == "done" and not collected) or outcome == "max_turns"
    goal = run.get("goal", "")
    has_claim = any(kw in goal.lower() for kw in QUALITATIVE_KEYWORDS)
    assertion = assertion_in_evidence(goal, collected) if has_claim else None
    return {"passed": passed, "turns_used": run.get("turns_used"), "wasted": wasted, "assertion": assertion}


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

    def _persist(verdict):
        record = {"run_ts": key, **verdict}
        with open(JUDGMENT_CACHE, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
        cache[key] = verdict
        return verdict

    if not collected:
        return _persist({"correct": False, "reason": "no answer collected"})

    if run.get("outcome") != "done":
          return _persist({"correct": False,
                           "reason": f"no committed pick (outcome={run.get('outcome')})"})

    # Cheap deterministic gate FIRST. If the goal states numeric thresholds,
    # parse them, pull the structured fields, compare in Python, and fail fast on
    # a miss — no API spend. The LLM is only needed for the subjective remainder.
    thresholds = parse_thresholds(run.get("goal", ""))
    if thresholds:
        fields, sources = extract_fields_and_sources(collected)
        target_domain = _domain(run.get("start_url", ""))
        for name, minval in thresholds.items():
            got = fields.get(name)
            if got is None:
                return _persist({"correct": False,
                                 "reason": f"no {name} evidence for >={minval:g} threshold"})
            if got < minval:
                return _persist({"correct": False,
                                 "reason": f"{name} {got:g} < required {minval:g}"})
            # Provenance gate (r4): a threshold value is only trustworthy if it was
            # read off a page on the task's own site. No source_url => unprovenanced
            # (older runs, or prose the agent invented). Off-domain => read from the
            # wrong place. Either way the number is not evidence the goal was met.
            # NOTE — what this does NOT prove: that `got` literally appeared on that
            # page (right-site/right-page but wrong-number stays uncaught until the
            # agent logs the page text it read from). Holes named, not hidden.
            src = sources.get(name)
            if not src:
                return _persist({"correct": False,
                                 "reason": f"{name}={got:g} has no source_url — unprovenanced"})
            if target_domain and _domain(src) != target_domain:
                return _persist({"correct": False,
                                 "reason": f"{name} read from {_domain(src)}, not task site {target_domain}"})

    # Numeric checks passed (or none present) -> LLM judges the subjective part.
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
    return _persist({"correct": bool(raw.get("correct")), "reason": str(raw.get("reason", ""))[:200]})


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

            # Infra noise != wrong answer. A run that crashed because the PAGE
            # never loaded (Page.goto timeout) is unreachable-this-time, same as
            # bestbuy -> exclude, don't score it against the agent. Scoped to
            # initial-navigation crashes so an agent-caused mid-run crash still
            # counts as a real failure.
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


def assertion_in_evidence(goal: str, collected: list[str]) -> bool:
    claims = [ kw for kw in QUALITATIVE_KEYWORDS if _has(kw, goal) ]
    if not claims:
        return True
    return all(_has(kw, collected) for kw in claims)

def _has(word, text):
    return re.search(rf"\b{re.escape(word)}\b", str(text).lower()) is not None

if __name__ == "__main__":
    main()
