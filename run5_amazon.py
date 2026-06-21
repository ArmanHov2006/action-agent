import asyncio
from agent_core import run_agent

GOAL = "On amazon.ca, find the price of a stainless steel water bottle. If there are multiple options, choose the one with the best reviews. Needs to have at least 200 reviews and a rating of 4.5 stars or higher."
START_URL = "https://www.amazon.ca"

if __name__ == "__main__":
    asyncio.run(run_agent(GOAL, START_URL))
