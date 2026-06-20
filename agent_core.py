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

# MODEL = "claude-haiku-4-5-20251001"  # Anthropic loop model (kept for revert)
MODEL = "gpt-4o-mini"  # ~6-8x cheaper than Haiku 4.5 for this loop

# Stamp every run so the scorer can separate eval results by code version.
# Bump this whenever loop logic changes (e.g. added the repeat-guard).
CODE_VERSION = "v4-block-failed-actions"

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
    "4. Once you have the info the GOAL needs, you MUST use the 'done' action to end."
)


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
            browser = await p.chromium.launch(headless=False)
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
                view = page_text[:4000]
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
                        state["collected"].append(arg)
                        print(f"Extracted: {arg}")
                    elif action == "done":
                        if state["collected"]:
                            state["outcome"] = "done"
                            print("Goal achieved!")
                            break
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
