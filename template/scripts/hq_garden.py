#!/usr/bin/env python3
"""Check HQ's projects for rot — the deterministic half of `/strata-hq:garden`.

Per registry entry (read only, like hq_sync; a project on another host is `unverifiable`):
  - stale wiki: a full-Strata project whose code moved on without its wiki — at least
    STALE_COMMITS commits outside wiki/ since the last commit that touched wiki/, the oldest of
    them more than STALE_DAYS old;
  - doc drift: a docs/**/*.md at HEAD whose raw/ mirror is missing or differs (Strata's
    docs→raw rule), checked only where the project keeps a raw/ at all;
  - quiet: no commit for QUIET_DAYS (the same rule hq_sync writes as `status: quiet`).
The script owns wiki/garden.md whole. The report carries no timestamp, so a night with no new
finding rewrites nothing; a changed report also appends one line to wiki/log.md.

Usage: python3 scripts/hq_garden.py [--hq DIR] [--dry-run]
Exit: 0 done · 3 registry.yaml missing/unreadable.
"""
import argparse
import datetime as dt
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hq_registry import read_yaml_subset  # noqa: E402  (same folder)
from hq_sync import QUIET_DAYS, git, read_project, read_text  # noqa: E402

STALE_COMMITS = 5
STALE_DAYS = 14
REPORT = os.path.join("wiki", "garden.md")


def stale_wiki(path, now):
    last_wiki = git(path, "log", "-1", "--format=%H %cs", "--", "wiki")
    if not last_wiki:
        return None
    sha, wiki_date = last_wiki.split()
    outside = [f"{sha}..HEAD", "--", ".", ":(exclude)wiki"]
    behind = int(git(path, "rev-list", "--count", "--no-merges", *outside) or 0)
    if behind < STALE_COMMITS:
        return None
    since = (git(path, "log", "--no-merges", "--reverse", "--format=%cs", *outside) or "").split("\n")[0]
    if not since or (now.date() - dt.date.fromisoformat(since)).days <= STALE_DAYS:
        return None
    return {"behind": behind, "since": since, "wiki_sha": sha[:7], "wiki_date": wiki_date}


def doc_drift(path):
    """docs/**/*.md whose raw/ mirror at HEAD is missing or has other content."""
    listing = git(path, "ls-tree", "-r", "HEAD", "--", "docs", "raw")
    if not listing:
        return []
    blobs = {}
    for line in listing.splitlines():
        meta, _, name = line.partition("\t")
        blobs[name] = meta.split()[2]
    if not any(name.startswith("raw/") for name in blobs):
        return []
    return sorted(name for name, sha in blobs.items()
                  if name.startswith("docs/") and name.endswith(".md")
                  and blobs.get("raw/" + name[len("docs/"):]) != sha)


def check_project(entry, now):
    facts, why = read_project(entry, now, {})
    if facts is None:
        return {"not_checked": why}
    found = {}
    if facts["status"] == "quiet":
        found["quiet"] = facts["last"]
    if facts["branch"] is not None:
        if facts["strata"] == "full":
            stale = stale_wiki(entry["path"], now)
            if stale:
                found["stale"] = stale
        drift = doc_drift(entry["path"])
        if drift:
            found["drift"] = drift
    return found


def page(slug):
    return f"[{slug}](../projects/{slug}.md)"


def render(report):
    out = ["# Garden", "",
           "What the gardener found across HQ's projects. Written by `scripts/hq_garden.py` —",
           "edits here are overwritten. Fix a finding in its own project, not from HQ.", ""]
    out += [f"## Stale wikis ({len(report['stale'])})", ""]
    out += [f"- {page(s)} — {v['behind']} commits since the wiki last changed "
            f"(`{v['wiki_sha']}`, {v['wiki_date']}), the oldest from {v['since']}"
            for s, v in sorted(report["stale"].items())] or ["None."]
    out += ["", f"## Doc drift ({len(report['drift'])})", ""]
    out += [f"- {page(s)} — raw/ mirror missing or stale for " + ", ".join(f"`{d}`" for d in docs)
            for s, docs in sorted(report["drift"].items())] or ["None."]
    out += ["", f"## Quiet projects ({len(report['quiet'])}) — no commit for {QUIET_DAYS} days", ""]
    out += [f"- {page(s)} — last commit {report['quiet_since'][s]}" for s in report["quiet"]] or ["None."]
    if report["not_checked"]:
        out += ["", f"## Not checked ({len(report['not_checked'])})", ""]
        out += [f"- {page(s)} — {why}" for s, why in sorted(report["not_checked"].items())]
    return "\n".join(out) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--hq", default=".", help="HQ root (default: cwd)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    hq = os.path.abspath(args.hq)
    registry = os.path.join(hq, "registry.yaml")
    if not os.path.exists(registry):
        print("hq_garden: needs registry.yaml (run hq_registry.py first)", file=sys.stderr)
        return 3
    try:
        entries = read_yaml_subset(registry).get("projects") or []
    except ValueError as exc:
        print(f"hq_garden: {exc}", file=sys.stderr)
        return 3
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    report = {"stale": {}, "drift": {}, "quiet": [], "quiet_since": {}, "not_checked": {}}
    for entry in entries:
        found = check_project(entry, now)
        slug = entry["slug"]
        if "not_checked" in found:
            report["not_checked"][slug] = found["not_checked"]
            continue
        if "stale" in found:
            report["stale"][slug] = found["stale"]
        if "drift" in found:
            report["drift"][slug] = found["drift"]
        if "quiet" in found:
            report["quiet"].append(slug)
            report["quiet_since"][slug] = found["quiet"]
    report["quiet"].sort()

    path = os.path.join(hq, REPORT)
    text = render(report)
    changed = read_text(path) != text
    if changed and not args.dry_run:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        with open(os.path.join(hq, "wiki", "log.md"), "a", encoding="utf-8") as f:
            f.write(f"[{now.strftime('%Y-%m-%dT%H:%M:%SZ')}] garden → stale {len(report['stale'])}, "
                    f"drift {len(report['drift'])}, quiet {len(report['quiet'])}\n")
    del report["quiet_since"]
    out = {k: dict(sorted(v.items())) if isinstance(v, dict) else v for k, v in report.items()}
    print(json.dumps({**out, "changed": changed, "dry_run": args.dry_run}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
