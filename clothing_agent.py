import asyncio
import os
import json
import re
from playwright.async_api import async_playwright
from anthropic import AsyncAnthropic
from dotenv import load_dotenv

def clean_json_response(text):
    """Extracts JSON content from a string that might contain extra text."""
    try:
        # Find the first { and the last }
        start = text.find('{')
        end = text.rfind('}')
        if start != -1 and end != -1:
            json_str = text[start:end+1]
            return json.loads(json_str)
        return json.loads(text)
    except Exception:
        raise ValueError(f"Could not parse JSON from response: {text[:100]}...")

async def main():
    load_dotenv()
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        print("Error: ANTHROPIC_API_KEY not found in .env")
        return

    # Clean and diagnostic
    clean_key = api_key.strip()
    client = AsyncAnthropic(api_key=clean_key)

    print("--- Diagnostic Start ---")
    
    # List of 4th generation models
    models_to_try = [
        "claude-haiku-4-5-20251001",  # cheapest — loop iteration model
        "claude-sonnet-4-6",
    ]

    working_model = None
    for model_name in models_to_try:
        try:
            print(f"Testing access to {model_name}...")
            response = await client.messages.create(
                model=model_name,
                max_tokens=10,
                messages=[{"role": "user", "content": "hi"}]
            )
            print(f"SUCCESS: {model_name} is available.")
            working_model = model_name
            break
        except Exception as e:
            print(f"FAILED: {model_name} - {e}")

    if not working_model:
        print("CRITICAL: No models are available. Check your API key and billing at console.anthropic.com")
        return

    print(f"--- Proceeding with {working_model} ---")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        page = await browser.new_page()

        print("--- Setup Complete ---")
        GOAL = "Find a white men's t-shirt on gapcanada.ca and tell me the price."
        state = { "collected": [], "history": [] }

        print("Navigating to Gap Canada...")
        await page.goto("https://www.gapcanada.ca/browse/men?cid=5063")
        await asyncio.sleep(5) 

        for i in range(15):
            print(f"\n--- Turn {i+1} ---")
            current_url = page.url
            try:
                # Wait a bit for any overlays to disappear
                page_text = await page.inner_text("body", timeout=10000)
            except Exception:
                page_text = "Could not read page content."
            
            view = page_text[:4000] 
            print(f"I am at: {current_url}")

            try:
                response = await client.messages.create(
                    model=working_model,
                    max_tokens=400,
                    system=(
                        "You are a web navigation agent. Respond ONLY with a valid JSON object. "
                        "Do not include any conversational text.\n\n"
                        "Allowed actions:\n"
                        "- {\"action\": \"navigate\", \"arg\": \"https://...\", \"why\": \"...\"}\n"
                        "- {\"action\": \"click\", \"arg\": \"text='Name'\", \"why\": \"...\"}\n"
                        "- {\"action\": \"extract\", \"arg\": \"The specific info found\", \"why\": \"...\"}\n"
                        "- {\"action\": \"done\", \"arg\": \"\", \"why\": \"...\"}\n\n"
                        "GUIDELINES:\n"
                        "1. Prefer clicking on visible links rather than guessing complex URLs.\n"
                        "2. If a page says 'No results' or 'Sold out', try a different category or search.\n"
                        "3. Once you have successfully extracted the information required by the GOAL, "
                        "you MUST use the 'done' action to end the mission."
                    ),
                    messages=[
                        {
                            "role": "user",
                            "content": f"GOAL: {GOAL}\nURL: {current_url}\nHISTORY: {json.dumps(state['history'][-3:])}\nPAGE TEXT: {view}\n\nNext action?"
                        }
                    ]
                )

                res_text = response.content[0].text
                action_data = clean_json_response(res_text)
                print(f"Claude's Plan: {action_data.get('why')}")
                
                action = action_data.get("action")
                arg = action_data.get("arg")
                print(f"Action: {action}({arg})")

                if action == "navigate":
                    await page.goto(arg)
                elif action == "click":
                    try:
                        # Try to click normally
                        await page.click(arg, timeout=10000)
                    except Exception:
                        # If intercepted, force the click
                        print("Click intercepted, retrying with force...")
                        await page.click(arg, force=True, timeout=10000)
                elif action == "extract":
                    state["collected"].append(arg)
                    print(f"Extracted: {arg}")
                elif action == "done":
                    print("Goal achieved!")
                    break
                
                state["history"].append(action_data)
                await asyncio.sleep(3)

            except Exception as e:
                print(f"Turn error: {e}")
                state["history"].append({"action": "error", "message": str(e)})
                # Wait a bit before retrying the next turn
                await asyncio.sleep(2)
                continue

        print("\n--- Mission Complete ---")
        print(f"Collected Data: {state['collected']}")
        print(f"Total History Turns: {len(state['history'])}")
        
        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
