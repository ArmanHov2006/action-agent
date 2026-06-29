"""Red test for the v9 qualitative gate — assertion_in_evidence.

Zero deps: run `python test_assertion.py`. Encodes the eval decisions:
  - a qualitative claim with no supporting evidence => the run is WRONG (False)
  - EVERY claim in the goal must be backed (AND), not just the first one
  - matching is word-boundary, so "covered" is not a "red" claim and
    "BlackBerry" does not satisfy a "black" claim

These start RED on the current single-keyword/substring implementation.
Make them GREEN by rewriting assertion_in_evidence (your hands).
"""
from scorer import assertion_in_evidence

CASES = [
    # name, goal, collected, expected
    ("no claim -> nothing to assert",
     "cheapest laptop under $500", [], True),

    ("single claim satisfied (dict evidence)",
     "black running shoes",
     [{"name": "Nike black runner", "price": "$89", "source_url": "https://x"}],
     True),

    ("multi-claim, only ONE backed -> WRONG (the AND bug)",
     "black waterproof shoes",
     [{"name": "Adidas black trainer", "source_url": "https://x"}],
     False),

    ("multi-claim, BOTH backed -> ok",
     "black waterproof shoes",
     [{"name": "black waterproof hiking shoe", "source_url": "https://x"}],
     True),

    ("word-boundary on GOAL: 'covered' is not a 'red' claim",
     "covered laptop case", [], True),

    ("word-boundary on EVIDENCE: 'BlackBerry' != 'black'",
     "black shoes",
     [{"name": "BlackBerry phone", "source_url": "https://x"}],
     False),
]


def run():
    fails = 0
    for name, goal, collected, expected in CASES:
        try:
            got = assertion_in_evidence(goal, collected)
        except Exception as e:
            got = f"EXC:{e!r}"
        ok = got == expected
        fails += not ok
        print(f"{'PASS' if ok else 'FAIL'}  {name}\n      expected={expected} got={got}")
    print(f"\n{len(CASES) - fails}/{len(CASES)} passed")
    return fails


if __name__ == "__main__":
    raise SystemExit(1 if run() else 0)
