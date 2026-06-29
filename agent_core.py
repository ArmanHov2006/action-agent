"""The Action Agent engine — one reusable loop. Tasks call run_agent(goal, start_url).

The engine is the asset; the task is swappable. Each run appends a trace to runs.jsonl
(your eval set seeds itself).
"""
import asyncio
import os
import json
import time
from datetime import datetime, timezone
from urllib.parse import urlparse
from playwright.async_api import async_playwright
# from anthropic import AsyncAnthropic  # swapped to OpenAI for cost — uncomment to revert
from openai import AsyncOpenAI
from dotenv import load_dotenv
from scorer import QUALITATIVE_KEYWORDS, assertion_in_evidence
# MODEL = "claude-haiku-4-5-20251001"  # Anthropic loop model (kept for revert)
MODEL = "gpt-4o-mini"  # ~6-8x cheaper than Haiku 4.5 for this loop

# Stamp every run so the scorer can separate eval results by code version.
# Bump this whenever loop logic changes (e.g. added the repeat-guard).
CODE_VERSION = "v9-qualitative-gate"

SYSTEM = (
    "You are a web navigation agent. Respond ONLY with a valid JSON object. "
    "Do not include any conversational text.\n\n"
    "Allowed actions:\n"
    '- {"action": "navigate", "arg": "https://...", "why": "..."}\n'
    '- {"action": "click", "arg": "text=\'Name\'", "why": "..."}\n'
    '- {"action": "type", "arg": "<selector>::<text to type>", "why": "..."}  '
    '(fills a search/input box then presses Enter; selector and text split on \'::\', '
    'e.g. "input[name=\'q\']::running shoes")\n'
    '- {"action": "extract", "arg": "The specific info found", "why": "..."}\n'
    '- {"action": "done", "arg": "", "why": "..."}\n\n'
    "GUIDELINES:\n"
    "1. Prefer clicking visible links over guessing complex URLs.\n"
    "2. Use ONE simple selector per click (e.g. text='Tees' or text='Classic T-Shirt' >> nth=0). "
    "Never combine selectors with 'and'.\n"
    "2b. If a click errors with 'Element is not visible' or 'resolved to N elements', "
    "the selector is ambiguous (often matches a hidden women's/men's nav duplicate). "
    "Do NOT repeat the same selector. Add 'visible=true' (e.g. text='Running Shoes' >> visible=true) "
    "or make the text more specific instead.\n"
    "3. If a page says 'No results' or 'Sold out', try a different category.\n"
    "3b. SEARCH-RESULTS pages usually already show each item's price, star rating "
    "AND review count in the PAGE TEXT. If the info the GOAL needs is already visible "
    "there, EXTRACT it straight from the results — do NOT click into the product page. "
    "Clicking individual product titles is slow and often fails; only click when the "
    "needed info is genuinely not on the current page.\n"
    "4. Before 'done', make sure you have EXTRACTED every piece of info the GOAL "
    "requires — including the EVIDENCE for any condition or numeric threshold "
    "(e.g. if the goal needs >=200 reviews and >=4.5 stars, you must have extracted "
    "the chosen item's actual review count AND star rating, not just its price). "
    "Extract ONE item that satisfies all the goal's conditions; do not re-extract the "
    "same item. Do NOT 'done' on a bare answer that lacks this evidence.\n"
    "5. Once you have ONE qualifying item with its price + evidence, use 'done' to end.\n"
    "6. The FINAL 'extract' (the one that satisfies the goal) MUST set arg to a JSON "
    'object, not a sentence: {"price": "...", "rating": "...", "review_count": "..."}. '
    "Use the actual values you read from the page; omit a key only if the goal does not "
    "require it. Do NOT wrap the numbers in prose."
)

# v5: a generic self-verification gate. Before accepting 'done', ask the model
# whether COLLECTED actually satisfies every requirement of the GOAL with explicit
# evidence. Task-agnostic — the engine stays reusable; the goal supplies the rules.
VERIFY_SYSTEM = (
    "You verify whether a web agent has gathered enough to satisfy a goal. "
    "Given the GOAL and what the agent COLLECTED, decide if there is ONE item in "
    "COLLECTED that meets every HARD condition of the goal with EXPLICIT evidence. "
    "Numeric thresholds (e.g. >=200 reviews, >=4.5 stars) require the actual numbers "
    "to be present and to satisfy the threshold. A bare price is NOT enough when the "
    "goal also demands review/rating conditions.\n"
    "Treat a soft preference like 'choose the best reviews' as satisfied as long as "
    "the returned item meets the hard thresholds — do NOT require proof that it is the "
    "single best-reviewed option in existence.\n"
    "Interpret abbreviated counts before comparing: '16.7K' = 16700, '4.2K' = 4200, "
    "'1.2M' = 1200000. So '16.7K reviews' easily satisfies '>=200 reviews'.\n"
    'Respond ONLY with JSON: {"satisfied": true|false, "missing": "<one sentence: '
    'what evidence is still needed>"}.'
)


async def verify_goal_met(client, model, goal, collected):
    """Return (satisfied: bool, missing: str). Self-critique before 'done'."""
    answer = " | ".join(str(c) for c in collected)
    resp = await client.chat.completions.create(
        model=model,
        max_tokens=200,
        temperature=0,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": VERIFY_SYSTEM},
            {"role": "user", "content": f"GOAL:\n{goal}\n\nCOLLECTED:\n{answer}"},
        ],
    )
    raw = clean_json_response(resp.choices[0].message.content)
    return bool(raw.get("satisfied")), str(raw.get("missing", ""))[:200]


def clean_json_response(text):
    """Extract the JSON object from a possibly chatty response."""
    start = text.find('{')
    end = text.rfind('}')
    if start != -1 and end != -1:
        return json.loads(text[start:end + 1])
    return json.loads(text)


async def run_agent(goal, start_url, model=MODEL, max_turns=15, task_id=None):
    load_dotenv()
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not found in .env")
    client = AsyncOpenAI(api_key=api_key.strip())

    # task_id identifies which fixture/site this run targets, so the scorer can
    # later break results down by task as well as by code_version. Derived from
    # the start_url's domain unless a run script passes one explicitly.
    if task_id is None:
        task_id = urlparse(start_url).netloc or start_url

    state = {
        "goal": goal,
        "model": model,
        "code_version": CODE_VERSION,
        "task_id": task_id,
        "start_url": start_url,
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "run_ts": datetime.now(timezone.utc).isoformat(),
        "collected": [],
        "history": [],
        "outcome": "max_turns",  # overwritten to "done" if the agent finishes
    }
    attempted = []
    failed_sigs = set()  # (action, arg) pairs that errored — never retry these

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            print(f"GOAL: {goal}")
            print(f"Navigating to {start_url} ...")
            await page.goto(start_url)
            await asyncio.sleep(5)

            for i in range(max_turns):
                print(f"\n--- Turn {i + 1} ---")
                current_url = page.url
                try:
                    for _ in range(3):
                        if await page.locator("dialog[open]").count() == 0:
                            break
                        await page.keyboard.press("Escape")
                        await page.wait_for_timeout(300)
                except Exception:
                    pass
                try:
                    page_text = await page.inner_text("body", timeout=10000)
                except Exception:
                    page_text = "Could not read page content."
                view = page_text[:7000]  # v6: wider view so more result candidates are visible
                print(f"I am at: {current_url}")

                sig = None  # set when we parse an action; guards the except below
                try:
                    response = await client.chat.completions.create(
                        model=model,
                        max_tokens=400,
                        response_format={"type": "json_object"},
                        messages=[
                            {"role": "system", "content": SYSTEM},
                            {
                                "role": "user",
                                "content": (
                                    f"GOAL: {goal}\nURL: {current_url}\n"
                                    f"FAILED ACTIONS (never repeat these): "
                                    f"{json.dumps([list(s) for s in failed_sigs])}\n"
                                    f"HISTORY: {json.dumps(state['history'][-3:])}\n"
                                    f"PAGE TEXT: {view}\n\nNext action?"
                                ),
                            },
                        ],
                    )

                    action_data = clean_json_response(response.choices[0].message.content)
                    action = action_data.get("action")
                    arg = action_data.get("arg")
                    # The model sometimes returns a structured arg (dict/list) — e.g.
                    # an extract payload of {price, rating, review_count}. Normalize to
                    # a string so it's hashable for the (action, arg) signature and
                    # usable as collected evidence / a selector.
                    if not isinstance(arg, str):
                        arg = json.dumps(arg, ensure_ascii=False)
                    print(f"Plan: {action_data.get('why')}")
                    print(f"Action: {action}({arg})")


                    sig = (action, arg)

                    # Recovery (v4): an action that already errored is dead. Don't run it
                    # again — force a different plan next turn instead of looping to "stuck".
                    if sig in failed_sigs:
                        print(f"Blocked repeat of failed action {sig}; forcing a new plan.")
                        state["history"].append({
                            "action": "blocked",
                            "failed_action": list(sig),
                            "message": ("This exact action already FAILED. Do NOT repeat it. "
                                        "Use a different/looser selector, or 'extract' the "
                                        "answer straight from PAGE TEXT."),
                        })
                        await asyncio.sleep(1)
                        continue

                    attempted.append(sig)
                    repeats = attempted.count(sig)

                    if repeats >= 3:
                        print(f"Stuck: repeated {sig} {repeats}x. Breaking.")
                        state["outcome"] = "stuck"   # or leave as max_turns
                        break

                    if action == "navigate":
                        await page.goto(arg)
                    elif action == "click":
                        try:
                            await page.click(arg, timeout=10000)
                        except Exception:
                            print("Click failed, retrying on first visible match...")
                            await page.locator(arg).locator("visible=true").first.click(timeout=10000)
                    elif action == "type":
                        selector, _, text = arg.partition("::")
                        await page.fill(selector.strip(), text.strip(), timeout=10000)
                        await page.press(selector.strip(), "Enter")
                    elif action == "extract":
                        # Every extract carries provenance. Dicts (the structured
                        # {price,rating,review_count} payload) get source_url added in
                        # place so scorer.extract_fields still sees those keys; prose /
                        # list extracts are wrapped so they're never unprovenanced.
                        try:
                            parsed_arg = json.loads(arg)
                            if isinstance(parsed_arg, dict):
                                parsed_arg["source_url"] = current_url
                                state["collected"].append(parsed_arg)
                            else:
                                state["collected"].append(
                                    {"value": parsed_arg, "source_url": current_url})
                        except json.JSONDecodeError:
                            state["collected"].append(
                                {"value": arg, "source_url": current_url})
                            print(f"Failed to parse extracted argument: {arg}")
                        print(f"Extracted: {arg}")
                        # v6: converge the moment the evidence is sufficient. The model
                        # tends to find a qualifying item then re-extract it into the
                        # repeat-guard instead of calling 'done'. So verify right here;
                        # if the goal is satisfied, finish now.

                        if not assertion_in_evidence(goal, state["collected"]):
                            print("Extracted item does NOT satisfy the goal's conditions yet.")
                            state["history"].append({
                                "action": "assertion_failed",
                                "extracted": arg,
                                "message": "Qualitative claim not confirmed in evidence. "
                                           "Apply the site's filter (e.g. ?color=Black in the URL) "
                                           "before extracting. Do NOT extract a product whose name "
                                           "does not explicitly contain the required attribute.",
                            })
                            await asyncio.sleep(2)
                            continue
                        satisfied, missing = await verify_goal_met(
                            client, model, goal, state["collected"])
                        if satisfied:
                            state["history"].append(action_data)
                            state["outcome"] = "done"
                            print("Goal satisfied on extract — done.")
                            break
                        # Not enough: feed the rejection back so the model stops
                        # re-extracting the same losing item and moves to another.
                        print(f"Not enough yet: {missing}")
                        state["history"].append({
                            "action": "insufficient",
                            "extracted": arg,
                            "message": (f"That item does NOT qualify: {missing} "
                                        "Do NOT extract it again. Look at OTHER items in "
                                        "the search results and extract a DIFFERENT one "
                                        "that meets ALL the goal's thresholds."),
                        })
                        await asyncio.sleep(2)
                        continue
                    elif action == "done":
                        if state["collected"]:
                            # v5 gate: don't trust the model's "done" — verify the
                            # collected answer actually satisfies the goal's evidence.
                            satisfied, missing = await verify_goal_met(
                                client, model, goal, state["collected"])
                            if satisfied:
                                state["outcome"] = "done"
                                print("Goal verified achieved!")
                                break
                            print(f"'done' rejected — still missing: {missing}")
                            state["history"].append({
                                "action": "verify_failed",
                                "message": (f"NOT done yet. Missing: {missing} "
                                            "Go extract that evidence (e.g. the chosen "
                                            "item's review count AND star rating) before 'done'."),
                            })
                            await asyncio.sleep(1)
                            continue
                        else:
                            print("Goal not achieved.")


                    state["history"].append(action_data)
                    await asyncio.sleep(3)

                except Exception as e:
                    print(f"Turn error: {e}")
                    if sig is not None:
                        failed_sigs.add(sig)  # remember the dead action; never retry it
                    state["history"].append({
                        "action": "error",
                        "failed_action": list(sig) if sig is not None else None,
                        "message": f"{str(e)[:200]} -- do NOT repeat this; try a different selector or 'extract'.",
                    })
                    await asyncio.sleep(2)
                    continue

                finally:
                    if i == max_turns - 1 and state["outcome"] != "done":
                        print("Reached maximum turns without completing the goal.")
                    elif state["outcome"] == "done":
                        print("Agent indicated completion with 'done' action.")

            await browser.close()

    except Exception as e:
        state["outcome"] = "crashed"
        state["history"].append({"action": "crash", "message": str(e)})

    finally:
        state["turns_used"] = len(state["history"])
        # Durable trace: one JSON line per run = your self-seeding eval set.
        with open("runs.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(state) + "\n")
        print("\n--- Mission Complete ---")
        print(f"Outcome: {state['outcome']} | Turns: {state['turns_used']}")
        print(f"Collected: {state['collected']}")

    return state
