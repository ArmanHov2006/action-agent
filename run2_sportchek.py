import asyncio
from agent_core import run_agent

GOAL = "Find a black running shoe on sportchek.ca for under CAD $100. Verify the product is black and currently under $100. Return only name and price of the product. If none exist, return exactly: No results"
START_URL = "https://www.sportchek.ca"

if __name__ == "__main__":
    asyncio.run(run_agent(GOAL, START_URL))
