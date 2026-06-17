import asyncio
import os
import json
from anthropic import AsyncAnthropic
from dotenv import load_dotenv

async def diagnostic():
    load_dotenv()
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        print("Error: ANTHROPIC_API_KEY not found.")
        return

    client = AsyncAnthropic(api_key=api_key.strip())
    
    # Try the oldest possible models to see if the key is valid
    test_models = [
        "claude-2.1",
        "claude-instant-1.2",
        "claude-3-haiku-20240307"
    ]

    print("--- Final Account Diagnostic ---")
    for model in test_models:
        try:
            print(f"Testing {model}...")
            await client.messages.create(
                model=model,
                max_tokens=10,
                messages=[{"role": "user", "content": "hi"}]
            )
            print(f"-> SUCCESS: {model} works!")
            return
        except Exception as e:
            print(f"-> FAILED: {model} - {e}")

    print("\nCONCLUSION:")
    print("1. If all failed with 404: Your account likely needs a $5 minimum deposit to activate.")
    print("2. Go to: https://console.anthropic.com/settings/plans")
    print("3. Add $2 more to your balance (to hit the $5 total).")

if __name__ == "__main__":
    asyncio.run(diagnostic())
