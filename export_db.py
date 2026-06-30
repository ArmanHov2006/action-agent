"""Pull all runs from PostgreSQL and append any missing ones to runs.jsonl."""
import json, os
from datetime import datetime
import psycopg
from dotenv import load_dotenv

load_dotenv()

existing = set()
if os.path.exists("runs.jsonl"):
    with open("runs.jsonl") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    existing.add(json.loads(line).get("run_ts"))
                except Exception:
                    pass

new_count = 0
with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
    rows = conn.execute(
        "SELECT run_ts, goal, start_url, outcome, code_version, turns_used, collected, history "
        "FROM runs ORDER BY id"
    ).fetchall()

with open("runs.jsonl", "a") as f:
    for row in rows:
        run_ts, goal, start_url, outcome, code_version, turns_used, collected, history = row
        if run_ts in existing:
            continue
        record = {
            "run_ts": run_ts,
            "goal": goal,
            "start_url": start_url,
            "outcome": outcome,
            "code_version": code_version,
            "turns_used": turns_used,
            "collected": json.loads(collected) if isinstance(collected, str) else (collected or []),
            "history": json.loads(history) if isinstance(history, str) else (history or []),
        }
        f.write(json.dumps(record, default=lambda o: o.isoformat() if isinstance(o, datetime) else str(o)) + "\n")
        new_count += 1

print(f"Done. {new_count} new runs appended ({len(rows)} total in DB).")
