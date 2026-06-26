# SPEC v9 — Hybrid Perception + the Qualitative-Claim Gate

**Status: proposed · 2026-06-26 · trigger = sportchek live SOFT-FAIL (see `live-runs.jsonl`)**

## The bug v9 fixes

Live run, sportchek fixture. Goal: *"Find a black running shoe under CAD $100. **Verify the product is black.**"*
Agent returned `Men's Nike Revolution 5 Running Shoes — $99.99` and asserted *"This black running shoe meets the budget."*

The trace never verified color. The returned name contains no "black." The agent
fabricated a qualitative claim it had **no evidence for**.

**Root cause:** the agent perceives only the **DOM text** (accessibility tree), never
pixels. Facts that live in text (review count `4.5K`, rating `4.6`) are extractable and
gate-checked. Facts that live in **images / swatches / filters** (color) are invisible to
text extraction — so the agent guessed. Today's gates guard numeric thresholds; they are
blind to qualitative claims.

> The eval guards facts that live in text. It is blind to facts that live in pixels.

## The decision: tiered hybrid perception

Not vision-always (pays screenshot cost + latency every run for facts text already had).
Not text-only (today's blind spot). **Tiered** — cheap path default, vision is the scalpel:

1. **DOM text (default, every run)** — structure, actions, numeric thresholds. Unchanged.
2. **Site facet / filter (preferred for qualitative attrs)** — color, size, brand are
   structured as filters (`?color=black`). Using the merchant's own taxonomy is *cheaper
   and more reliable than vision* — it's the site's ground-truth tag, not a pixel guess.
3. **Vision / screenshot (edge cases only)** — fires ONLY when a required qualitative
   claim cannot be confirmed from text or facet. Confirms actual appearance. Expensive,
   so gated behind a trigger, never the default.

Order of trust for "is it black": **facet > vision > text-in-title**. Facet is site truth;
vision is pixel truth (with its own ambiguity — navy vs black, multicolor); bare title
text is weakest.

## New gate: assertion-must-appear-in-evidence

Cheap guard that catches today's case with zero vision:

> If the agent's claim asserts attribute X (e.g. "black"), the extracted evidence
> (name / attributes / applied-filter) must contain X. Else the claim FAILS.

Applied to sportchek today → name has no "black", no color filter applied → FAIL.
No screenshot needed. This is the floor; vision is the ceiling for genuine edge cases.

## Open questions (not built today — spec only)

- What counts as "black" under vision? (navy? black/white colorway?) — judgment moves,
  doesn't disappear.
- Vision trigger: per-claim flag in the goal parser, or post-hoc gate that re-runs with
  vision on fail?
- Cost ceiling: cap vision passes per run.

## Eval impact

Add a qualitative-claim fixture class to the harness. Today's numeric-only baseline
(18.5% localhost) does not exercise this path. Re-score with the new gate before claiming
v9 beats v8.
