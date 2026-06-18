"""The Action Agent engine — one reusable loop. Tasks call run_agent(goal, start_url).

The engine is the asset; the task is swappable. Each run appends a trace to runs.jsonl
(your eval set seeds itself).
"""
import asyncio
import os
import json
import time
from playwright.async_api import async_playwright
from anthropic import AsyncAnthropic
from dotenv import load_dotenv

MODEL = "claude-haiku-4-5-20251001"  # cheapest — loop iteration model

SYSTEM = (
    "You are a web navigation agent. Respond ONLY with a valid JSON object. "
    "Do not include any conversational text.\n\n"
    "Allowed actions:\n"
    '- {"action": "navigate", "arg": "https://...", "why": "..."}\n'
    '- {"action": "click", "arg": "text=\'Name\'", "why": "..."}\n'
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


async def run_agent(goal, start_url, model=MODEL, max_turns=15):
    load_dotenv()
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not found in .env")
    client = AsyncAnthropic(api_key=api_key.strip())

    state = {
        "goal": goal,
        "model": model,
        "start_url": start_url,
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "collected": [],
        "history": [],
        "outcome": "max_turns",  # overwritten to "done" if the agent finishes
    }

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
                    page_text = await page.inner_text("body", timeout=10000)
                except Exception:
                    page_text = "Could not read page content."
                view = page_text[:4000]
                print(f"I am at: {current_url}")

                try:
                    response = await client.messages.create(
                        model=model,
                        max_tokens=400,
                        system=SYSTEM,
                        messages=[{
                            "role": "user",
                            "content": (
                                f"GOAL: {goal}\nURL: {current_url}\n"
                                f"HISTORY: {json.dumps(state['history'][-3:])}\n"
                                f"PAGE TEXT: {view}\n\nNext action?"
                            ),
                        }],
                    )

                    action_data = clean_json_response(response.content[0].text)
                    action = action_data.get("action")
                    arg = action_data.get("arg")
                    print(f"Plan: {action_data.get('why')}")
                    print(f"Action: {action}({arg})")

                    if action == "navigate":
                        await page.goto(arg)
                    elif action == "click":
                        try:
                            await page.click(arg, timeout=10000)
                        except Exception:
                            print("Click failed, retrying on first visible match...")
                            await page.locator(arg).locator("visible=true").first.click(timeout=10000)
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
                    state["history"].append({"action": "error", "message": str(e)})
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
