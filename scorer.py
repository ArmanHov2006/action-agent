import json

# Geo-blocked from Armenia (not a bot block) — agent can never reach these,
# so they are invalid fixtures, not agent failures. Excluded from the rate,
# reported separately. Match against start_url. Confirmed 2026-06-20: all four
# Canadian sites time out on page.goto (net unreachable); only amazon.ca loads.
EXCLUDED_DOMAINS = ("bestbuy.ca", "sportchek.ca", "canadiantire.ca", "cbc.ca")

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
    by_vt = {}       # (version, task_id) -> [passes, total]; isolates task-mix bias
    # Order of first appearance in the file = chronological order (append-only log),
    # used below to find "latest" vs "previous" version without trusting string sort.
    version_order = []
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
            # Old lines (pre code_version field) are bucketed separately rather
            # than silently mixed into a real version's stats.
            version = run.get("code_version") or "unversioned"
            if version not in by_version:
                version_order.append(version)
            v = by_version.setdefault(version, [0, 0])
            v[0] += r["passed"]
            v[1] += 1
            task_id = run.get("task_id") or "?"
            vt = by_vt.setdefault((version, task_id), [0, 0])
            vt[0] += r["passed"]
            vt[1] += 1
            tag = "PASS" if r["passed"] else "FAIL"
            w = " WASTED" if r["wasted"] else ""
            print(f"{i:2} {tag} turns={r['turns_used']}{w}  {run.get('goal','')[:40]}")
    rate = passes / total * 100 if total else 0
    print(f"\nsuccess rate: {passes}/{total} = {rate:.1f}%  ({wasted_n} wasted, {excluded_n} excluded)")

    print("\nby code version:")
    print(f"  {'version':<28} {'pass':>5} {'total':>6} {'rate':>7}")
    for version, (vp, vt) in sorted(by_version.items()):
        vrate = vp / vt * 100 if vt else 0
        print(f"  {version:<28} {vp:>5} {vt:>6} {vrate:>6.1f}%")

    # Per (version × task): the only fair v3-vs-v4 comparison is SAME task.
    # A version-level delta can be pure task-mix bias if the two versions were
    # run on different task distributions.
    print("\nby version x task:")
    print(f"  {'version':<28} {'task':<18} {'pass':>5} {'total':>6} {'rate':>7}")
    for (version, task_id), (vp, vt) in sorted(by_vt.items()):
        vrate = vp / vt * 100 if vt else 0
        print(f"  {version:<28} {task_id:<18} {vp:>5} {vt:>6} {vrate:>6.1f}%")

    # Delta: latest version (by chronological appearance in the append-only log)
    # vs. the version immediately before it. Skips "unversioned" as a comparison
    # anchor when possible since it isn't a real code revision.
    real_versions = [v for v in version_order if v != "unversioned"]
    if len(real_versions) >= 2:
        prev_v, latest_v = real_versions[-2], real_versions[-1]
        pp, pt = by_version[prev_v]
        lp, lt = by_version[latest_v]
        prev_rate = pp / pt * 100 if pt else 0
        latest_rate = lp / lt * 100 if lt else 0
        delta = latest_rate - prev_rate
        sign = "+" if delta >= 0 else ""
        print(f"\ndelta: {latest_v} ({latest_rate:.1f}%) vs {prev_v} ({prev_rate:.1f}%) "
              f"= {sign}{delta:.1f}pp")
    else:
        print("\ndelta: not enough distinct code_versions yet to compare")

if __name__ == "__main__":
    main()
