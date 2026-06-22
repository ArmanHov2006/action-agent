# Baseline — Eval-Driven Action Agent

**Locked: 2026-06-21 · code v6-converge-on-evidence · judge rubric r3**

The reference line every future change is measured against. Re-score with
`python scorer.py --judge`; a change is only "better" if it beats these numbers
on the same fixtures.

## Headline

| Metric | Value | Meaning |
|---|---|---|
| **CORRECT rate** | **18.5%** (10/54) | LLM-judge: answer actually satisfies the goal. The honest number. |
| STOP rate | 53.7% (29/54) | Agent *claimed* success (stopped `done` with collected output). |
| Lying gap | 35.2pp | Runs the agent thought it nailed but got wrong. The core failure mode. |
| Excluded | 14 | Infra-invalid runs, not scored (see methodology). |

## r4 measurement (2026-06-22 · provenance gate · judge rubric r4)

NOT a replacement for the locked r3 line above — a **new measurement under a
stricter judge**. r4 fails any threshold value with no `source_url` or an
off-domain one (see `Action Agent — The Provenance Gate (06-22)` note).

| Metric | r3 (locked) | r4 (provenance) | Read |
|---|---|---|---|
| CORRECT rate | 18.5% (10/54) | **12.0% (9/75)** | Dropped — gate removed unverifiable wins. Honesty, not regression. |
| STOP rate | 53.7% | 62.7% (47/75) | — |
| Lying gap | 35.2pp | 50.7pp | Wider: many STOP-success runs now fail provenance. |

**The signal is in the per-version split, not the headline:**

| Version | amazon CORRECT | Why |
|---|---|---|
| v7-structured-extract | 85.7% → **0.0%** | Pre-`source_url` runs — no provenance → all fail the gate. The 85.7% was the fake. |
| **v8-source-url** | **60.0% (3/5)** | The only runs that actually carry provenance. The real forward number. |

> [!honest] What 12.0% is and isn't
> Legacy runs (v3–v7) have no `source_url`, so r4 fails them wholesale — that
> deflates the overall rate but is *correct*: an unprovenanced number isn't a
> verified win. Judge v8-only and provenance-true correctness is **~60%**. The
> gate still misses same-domain homepage hallucination (floor, not finish).

## What counts (methodology — why these numbers are trustworthy)

The denominator is honest in **both** directions. Two corrections were made to
the scorer to get here, each documented so the number can't be gamed:

1. **Correctness, not self-report.** STOP only means the agent *stopped claiming
   success*. CORRECT is an LLM judge reading goal + collected answer against the
   task's hard requirements (e.g. ">=200 reviews AND >=4.5 stars"). The judge is
   cached per run (`judgments.jsonl`) and gated against hand-labels.

2. **Exclude unreachable, never hard.** A fixture is dropped only when the page
   could not be reached, so the agent never got a fair attempt:
   - `bestbuy.ca` — geo-blocked from Armenia. Invalid fixture.
   - `outcome=crashed` on `Page.goto` timeout (page never loaded) — infra noise,
     excluded by **outcome**, not by domain (the site is reachable other times).
   A site the agent *reaches and fails on* stays scored. Excluding a hard site
   would flatter the number; counting a crash as a wrong answer would deflate it.
   Both are eval dishonesty. 14/68 runs (21%) were infra crashes — scoring them
   as failures would have understated the agent by hiding 21% of noise in the
   denominator.

## Per-site (code v6)

| Site | CORRECT | N | note |
|---|---|---|---|
| canadiantire.ca | 33.3% | 3 | strongest |
| sportchek.ca | 25.0% | 4 | |
| amazon.ca | 18.2% | 11 | hardest site, most data, most iterated |
| cbc.ca | **0.0%** | 3 | **failure cluster** — see below |

## The measured fix (the portfolio story)

The eval caught a regression and reversed it — on amazon.ca, same fixture:

| Version | change | CORRECT |
|---|---|---|
| v4-block-failed-actions | block actions that already failed | 7.1% |
| v5-verify-before-done | force verify step before stopping | **0.0%** ← regression |
| v6-converge-on-evidence | converge on collected evidence | **18.2%** ← recovered + beat v4 |

v5's "verify before done" was intuitively right but **measured worse** — it made
the agent loop without ever stopping (0% stop, 0% correct). Without the eval this
would have shipped as an "improvement." v6 recovered to 2.6x the v4 baseline.
Lesson: intuition proposes, the number decides.

## Known failure cluster (next fix target)

**cbc.ca news headlines: 0% correct across all reachable runs.** Failure mode:
the agent stops at turn 1 returning *a* headline without confirming it is *the
top* headline — premature stop, wrong answer. This is the lying gap in miniature.
Caveat: N is small (3 v6 runs); confirm with more runs before declaring the fix
moved it.

## Limitations (don't oversell this)

- Per-site N is thin (3–4 outside amazon). Diverse but not yet robust; non-amazon
  rates are directional, not settled.
- Legacy unversioned ("?") runs are mixed-task and excluded from per-site reads.
- One model (gpt-4o-mini). No model sweep yet.

## Rejected experiment — v7-select-and-commit (Goodhart's law, recorded)

Tried to fix the amazon "list-not-pick" cluster by adding a prompt rule: emit one
`FINAL ANSWER:` and commit. Result looked spectacular — **amazon 8/8 = 100%**,
overall 18.5%→29%. Reverted anyway, because inspection showed the number was
**gamed, not earned**:

- 2/8 runs **hallucinated** — extracted a product + price + review count while
  still on the amazon.ca homepage, having never searched. The judge passed them
  blind: it has no ground-truth check that the agent actually saw the data.
- 6/8 picked RAYMYLO (16.7K reviews) when Owala (117.5K) is the best-reviewed.
  The task asks for *best* reviews; rubric r3 only checks *a qualifying* item, so
  it passed the wrong pick.

True correctness was ~0-1/8, not 8/8. The fix taught the agent to **commit
confidently to unverified answers**, and both the agent's self-report and the LLM
judge reward confident, well-formed output regardless of truth. Classic Goodhart:
once a number is the target, the cheapest way to move it is to game the measurer.

**Lesson: a fix that moves the number is not a real improvement until the judge
can't be gamed.** v6 stays the locked baseline.

## Judge hardening — Part 1: deterministic numeric checks (DONE)

Moved the numeric `>=` comparisons OUT of the LLM and into Python — the eval's
under-crediting bug (06-20: gpt-4o-mini ruled `117,500 < 200`). Implemented in
`scorer.py`: `parse_thresholds(goal)` pulls hard thresholds from the goal,
`extract_fields(collected)` reads the structured `{price,rating,review_count}`
dict the agent now emits, `to_number` normalizes (`16.7K`→16700, `117,542`→117542),
and `judge_run` runs a deterministic gate (fail-fast on missing field or
`got < minval`) BEFORE any LLM call. Agent side: `agent_core` SYSTEM rule 6 forces
the final `extract` to be a JSON object, not prose.

Result — **v7-structured-extract amazon: CORRECT 6/7 = 85.7%** (vs v6 18.2%).

> [!honest] What this number is and isn't
> - **Real:** the under-crediting (Failure 2) is fixed — genuinely qualifying items
>   (16.7K reviews) now pass the math correctly. v6's 18% → estimated true ~45%.
> - **Still inflated (~45 → 85.7):** the OVER-crediting holes remain.
>   1. **Provenance: uninstrumented, unknown.** Runs don't log which page each
>      `extract` came from, so we cannot verify the value was actually seen vs
>      hallucinated. Can't be checked until `agent_core` logs it.
>   2. **"Best" is ambiguous.** 6/7 picked RAYMYLO (16.7K reviews, 4.7★) over Owala
>      (117.5K reviews, 4.6★). "Best reviews" is undefined (rating vs count) — a
>      FIXTURE bug. Design decision to record: `best = highest rating among items
>      with >=1000 reviews`. Fix the goal text first, then enforce in the judge.
>
> True amazon correctness sits between ~45% and 85.7%. Not yet nailed. Floor done;
> the two remaining holes are now concrete and located.

## Next (judge hardening comes before the next agent fix)

1. **Harden the judge** so it can't be gamed:
   - require the agent to cite WHERE it read each value (page/url) and check the
     run actually visited a results page before trusting extracted numbers.
   - enforce the "best reviews" condition (tighten rubric past r3's "any
     qualifying item").
2. Re-run the v7 select-and-commit idea against the hardened judge — only then is
   any improvement real.
3. Grow cbc + non-amazon N to >=8 each so per-site rates settle.
4. Regression gate: scorer exits non-zero if CORRECT drops below the locked
   baseline.
