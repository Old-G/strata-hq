#!/usr/bin/env python3
"""Check HQ's projects for rot — the deterministic half of `/strata-hq:garden`.

Per registry entry (read only, like hq_sync; a project on another host is `unverifiable`), on
its default branch — the newer of the local branch and its origin copy, so a checkout parked on
a feature branch does not hide what landed:
  - stale wiki: a full-Strata project whose code moved on without its wiki — at least
    STALE_COMMITS commits outside wiki/ since the last commit that touched wiki/, the oldest of
    them more than STALE_DAYS old. Commits that only touch Strata's own plumbing (synced hooks,
    the raw/ mirror, CLAUDE.md) are not code the wiki missed and are not counted;
  - owed ingests: `- pending_ingest: docs/<p>.md` markers in wiki/log.md that no later
    `ingest raw/<p>.md` retired and whose doc still exists — Strata's default rule, read here
    without running anything from the project. A project may narrow that rule in its own
    scripts/lib/pending_ingest.sh; its checkout's copy is run only for project PATHS the owner
    lists under `trust_ingest_rule:` in hq.yaml — a path, not a slug, so a registry rebuild that
    moves a slug to another repo carries no trust along (registering a repo is not trusting it) —
    and only when it is a file inside the project, run with a bare environment (PATH=/usr/bin:/bin,
    HOME, locale) and no stdin, so none of the gardener's tokens reach it;
  - doc drift: a docs/**/*.md whose raw/ mirror is missing or differs (Strata's docs→raw rule),
    checked only where the project keeps a raw/ at all;
  - no wiki: a git project without a Strata wiki that still had commits in the last ACTIVE_DAYS —
    work is landing and leaves no knowledge trace;
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
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hq_registry import read_yaml_subset  # noqa: E402  (same folder)
from hq_sync import QUIET_DAYS, git, read_project, read_text  # noqa: E402

STALE_COMMITS = 5
STALE_DAYS = 14
ACTIVE_DAYS = 30
REPORT = os.path.join("wiki", "garden.md")
# What Strata itself installs and keeps in sync; a commit touching only these is upkeep, not code.
PLUMBING = (".strata", ".claude", ".githooks", "raw", "scripts/hooks", "scripts/lib", "scripts/pre-commit",
            "scripts/git-hooks", "scripts/sync_raw_mirror.sh", "scripts/strata_upgrade_check.sh",
            "scripts/strata_why.sh", "scripts/diagram_check.sh", "scripts/.strata-keep",
            "CLAUDE.md", "WIKI.md")
MARKER = re.compile(r"^\s*-\s+pending_ingest:\s*(\S+)")
INGEST = re.compile(r"ingest\s+(raw/[^\s;,)]+)((?:\s*,\s*raw/[^\s;,)]+)*)")


def main_ref(path):
    """The default branch: the newer of the local one and origin's; HEAD when there is neither."""
    name = (git(path, "rev-parse", "--abbrev-ref", "origin/HEAD") or "").removeprefix("origin/")
    for branch in dict.fromkeys(b for b in (name, "main", "master") if b):
        refs = [r for r in (branch, f"origin/{branch}") if git(path, "rev-parse", "--verify", "-q", r)]
        if refs:
            return max(refs, key=lambda r: int(git(path, "log", "-1", "--format=%ct", r) or 0))
    return "HEAD"


def stale_wiki(path, ref, now):
    last_wiki = git(path, "log", "-1", "--format=%H %cs", ref, "--", "wiki")
    if not last_wiki:
        return None
    sha, wiki_date = last_wiki.split()
    outside = [f"{sha}..{ref}", "--", ".", ":(exclude)wiki", *(f":(exclude){p}" for p in PLUMBING)]
    behind = int(git(path, "rev-list", "--count", "--no-merges", *outside) or 0)
    if behind < STALE_COMMITS:
        return None
    since = (git(path, "log", "--no-merges", "--reverse", "--format=%cs", *outside) or "").split("\n")[0]
    if not since or (now.date() - dt.date.fromisoformat(since)).days <= STALE_DAYS:
        return None
    return {"behind": behind, "since": since, "wiki_sha": sha[:7], "wiki_date": wiki_date}


def owed_ingests(path, ref, trusted):
    """docs/ paths with a pending_ingest marker in wiki/log.md that no later ingest retired."""
    log = git(path, "show", f"{ref}:wiki/log.md")
    if not log:
        return []
    if trusted and os.path.isfile(os.path.join(path, "scripts", "lib", "pending_ingest.sh")):
        owed = project_rule(path, log)
        if owed is not None:
            return owed
    marker, ingested = {}, {}
    for n, line in enumerate(log.splitlines(), 1):
        m = MARKER.match(line)
        if m and re.fullmatch(r"docs/.+\.md", m.group(1)):
            marker[m.group(1)] = n
        for hit in INGEST.finditer(line):
            for raw in [hit.group(1), *re.findall(r"raw/[^\s;,)]+", hit.group(2))]:
                ingested[raw] = n
    docs = set((git(path, "ls-tree", "-r", "--name-only", ref, "--", "docs") or "").splitlines())
    return sorted(p for p, n in marker.items()
                  if p in docs and ingested.get("raw/" + p[len("docs/"):], 0) < n)


def project_rule(path, log):
    """The checkout's own rule (owner-trusted, a file inside the project) over the log at the default
    branch, run bare — no inherited environment, no stdin. None → Strata's default rule."""
    root = os.path.realpath(path)
    lib = os.path.realpath(os.path.join(root, "scripts", "lib", "pending_ingest.sh"))
    if not lib.startswith(root + os.sep) or not os.path.isfile(lib):
        return None
    env = {k: os.environ[k] for k in ("HOME", "LANG", "LC_ALL") if k in os.environ}
    with tempfile.NamedTemporaryFile("w", suffix=".md", encoding="utf-8") as f:
        f.write(log + "\n")
        f.flush()
        try:
            out = subprocess.run(["bash", "-c", '. "$1" && strata_pending_paths', "_", lib],
                                 cwd=root, stdin=subprocess.DEVNULL, capture_output=True,
                                 text=True, timeout=30,
                                 env={**env, "PATH": "/usr/bin:/bin", "STRATA_WIKI_LOG": f.name})
        except (OSError, subprocess.TimeoutExpired):
            return None
    return sorted({p for p in out.stdout.splitlines() if p.strip()}) if out.returncode == 0 else None


def doc_drift(path, ref):
    """docs/**/*.md whose raw/ mirror at the default branch is missing or has other content."""
    listing = git(path, "ls-tree", "-r", ref, "--", "docs", "raw")
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


def recent_commits(path, ref):
    return int(git(path, "rev-list", "--count", "--no-merges", f"--since={ACTIVE_DAYS}.days", ref) or 0)


def check_project(entry, now, trusted=frozenset()):
    facts, why = read_project(entry, now, {})
    if facts is None:
        return {"not_checked": why}
    found = {}
    if facts["status"] == "quiet":
        found["quiet"] = facts["last"]
    if facts["branch"] is not None:
        path = entry["path"]
        ref = main_ref(path)
        if facts["strata"] == "full":
            stale = stale_wiki(path, ref, now)
            if stale:
                found["stale"] = stale
            owed = owed_ingests(path, ref, real(path) in trusted)
            if owed:
                found["owed"] = owed
        else:
            n = recent_commits(path, ref)
            if n:
                found["nowiki"] = n
        drift = doc_drift(path, ref)
        if drift:
            found["drift"] = drift
    return found


def real(path):
    return os.path.realpath(os.path.expanduser(str(path)))


def page(slug):
    return f"[{slug}](../projects/{slug}.md)"


def safe(name):
    """A member's file name as inert text: no backticks, markup or line breaks reach the report."""
    return re.sub(r"[^\w./ -]", "?", name)[:200]


def docs_list(docs, cap=10):
    shown = ", ".join(f"`{safe(d)}`" for d in docs[:cap])
    return shown + (f" and {len(docs) - cap} more" if len(docs) > cap else "")


def render(report):
    out = ["# Garden", "",
           "What the gardener found across HQ's projects. Written by `scripts/hq_garden.py` —",
           "edits here are overwritten. Fix a finding in its own project, not from HQ.", ""]
    out += [f"## Stale wikis ({len(report['stale'])})", ""]
    out += [f"- {page(s)} — {v['behind']} commits since the wiki last changed "
            f"(`{v['wiki_sha']}`, {v['wiki_date']}), the oldest from {v['since']}"
            for s, v in sorted(report["stale"].items())] or ["None."]
    out += ["", f"## Ingests owed ({len(report['owed'])}) — docs mirrored into raw/, never ingested", ""]
    out += [f"- {page(s)} — {len(docs)}: " + docs_list(docs)
            for s, docs in sorted(report["owed"].items())] or ["None."]
    out += ["", f"## Doc drift ({len(report['drift'])})", ""]
    out += [f"- {page(s)} — raw/ mirror missing or stale for " + docs_list(docs)
            for s, docs in sorted(report["drift"].items())] or ["None."]
    out += ["", f"## No wiki ({len(report['nowiki'])}) — commits in the last {ACTIVE_DAYS} days, no Strata wiki", ""]
    out += [f"- {page(s)} — {n} commits" for s, n in sorted(report["nowiki"].items())] or ["None."]
    out += ["", f"## Quiet projects ({len(report['quiet'])}) — no commit for {QUIET_DAYS} days", ""]
    out += [f"- {page(s)} — " + (f"last commit {report['quiet_since'][s]}" if report["quiet_since"][s]
                                 else "no commits yet") for s in report["quiet"]] or ["None."]
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
    try:
        trusted = frozenset(real(p) for p in read_yaml_subset(os.path.join(hq, "hq.yaml")).get("trust_ingest_rule") or [])
    except ValueError:
        trusted = frozenset()  # an unreadable hq.yaml trusts nobody
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    report = {"stale": {}, "owed": {}, "drift": {}, "nowiki": {}, "quiet": [], "quiet_since": {},
              "not_checked": {}}
    for entry in entries:
        found = check_project(entry, now, trusted)
        slug = entry["slug"]
        if "not_checked" in found:
            report["not_checked"][slug] = found["not_checked"]
            continue
        for key in ("stale", "owed", "drift", "nowiki"):
            if key in found:
                report[key][slug] = found[key]
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
                    f"drift {len(report['drift'])}, quiet {len(report['quiet'])}, "
                    f"owed {len(report['owed'])}, no wiki {len(report['nowiki'])}\n")
    del report["quiet_since"]
    out = {k: dict(sorted(v.items())) if isinstance(v, dict) else v for k, v in report.items()}
    print(json.dumps({**out, "changed": changed, "dry_run": args.dry_run}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
