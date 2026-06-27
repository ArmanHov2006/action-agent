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

def init_db():
    url = os.environ["DATABASE_URL"]
    with psycopg.connect(url) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS runs (
                id          BIGSERIAL PRIMARY KEY,
                run_ts      TEXT,
                goal        TEXT,
                start_url   TEXT,
                outcome     TEXT,
                code_version TEXT,
                turns_used  INTEGER,
                collected   TEXT,
                history     TEXT
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_runs_run_ts
            ON runs (run_ts)
        """)

