import json

# Geo-blocked from Armenia (not a bot block) — agent can never reach these,
# so they are invalid fixtures, not agent failures. Excluded from the rate,
# reported separately. Match against start_url.
EXCLUDED_DOMAINS = ("bestbuy.ca",)

def score_run(run):
    collected = run.get("collected", []) or  []
    outcome = run.get("outcome")
    passed = outcome == "done" and len(collected) > 0
    wasted = (outcome == "done" and not collected) or outcome == "max_turns"
    return {
        "passed": passed, "turns_used": run.get("turns_used"), "wasted": wasted
    }

def main():
    passes = total = wasted_n = excluded_n = 0
    by_version = {}  # version -> [passes, total]
    with open("runs.jsonl", encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                run = json.loads(line)
            except json.JSONDecodeError:
                print(f"{i}: BAD JSON, skipped")
                continue
            start_url = run.get("start_url", "")
            if any(d in start_url for d in EXCLUDED_DOMAINS):
                excluded_n += 1
                print(f"{i:2} EXCL (unreachable)  {run.get('goal','')[:40]}")
                continue
            r = score_run(run)
            total += 1
            passes += r["passed"]
            wasted_n += r["wasted"]
            version = run.get("code_version", "v0-untagged")
            v = by_version.setdefault(version, [0, 0])
            v[0] += r["passed"]
            v[1] += 1
            tag = "PASS" if r["passed"] else "FAIL"
            w = " WASTED" if r["wasted"] else ""
            print(f"{i:2} {tag} turns={r['turns_used']}{w}  {run.get('goal','')[:40]}")
    rate = passes / total * 100 if total else 0
    print(f"\nsuccess rate: {passes}/{total} = {rate:.1f}%  ({wasted_n} wasted, {excluded_n} excluded)")
    print("by code version:")
    for version, (vp, vt) in sorted(by_version.items()):
        print(f"  {version}: {vp}/{vt} = {vp / vt * 100:.1f}%")

if __name__ == "__main__":
    main()
