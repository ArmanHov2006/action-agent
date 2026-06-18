import asyncio
from agent_core import run_agent

GOAL = "Find a black running shoe under $100 on sportchek.ca; give its name and price"
START_URL = "https://www.sportchek.ca"

if __name__ == "__main__":
    asyncio.run(run_agent(GOAL, START_URL))
