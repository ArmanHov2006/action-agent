import asyncio
from agent_core import run_agent

GOAL = "On canadiantire.ca, find a cordless drill and tell me its price"
START_URL = "https://www.canadiantire.ca"

if __name__ == "__main__":
    asyncio.run(run_agent(GOAL, START_URL))
