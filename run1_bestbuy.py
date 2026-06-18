import asyncio
from agent_core import run_agent

GOAL = "Find the price of Apple AirPods Pro on bestbuy.ca"
START_URL = "https://www.bestbuy.ca"

if __name__ == "__main__":
    asyncio.run(run_agent(GOAL, START_URL))
