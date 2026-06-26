import os, json
import psycopg

def save_run(state):
    url = os.environ["DATABASE_URL"]                    # which env var? (the one in .env)
    with psycopg.connect(url) as conn:
        conn.execute(
            "insert into runs "
            "(run_ts, goal, start_url, outcome, code_version, turns_used, collected, history) "
            "values (%s, %s, %s, %s, %s, %s, %s, %s)",                    # how many %s? one per column. comma-separated.
            (
                state["run_ts"],
                state["goal"],
                state["start_url"],                  # the start url
                state["outcome"],
                state["code_version"],
                state["turns_used"],
                json.dumps(state["collected"]),       # the list of items collected
                json.dumps(state["history"]),
            ),
        )
