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
