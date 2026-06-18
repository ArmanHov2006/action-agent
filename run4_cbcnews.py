import asyncio
from agent_core import run_agent

GOAL = "Find today's top headline on cbc.ca/news and report it"
START_URL = "https://www.cbc.ca/news"

if __name__ == "__main__":
    asyncio.run(run_agent(GOAL, START_URL))
