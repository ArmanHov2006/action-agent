import asyncio
from agent_core import run_agent

GOAL = "On amazon.ca, find the price of a stainless steel water bottle"
START_URL = "https://www.amazon.ca"

if __name__ == "__main__":
    asyncio.run(run_agent(GOAL, START_URL))
