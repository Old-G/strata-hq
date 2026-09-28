#!/usr/bin/env python3
"""Refresh HQ's project pages from registry.yaml — the deterministic half of `hq-sync` (ADR-1).

Per registry entry it reads the project (pure read: git HEAD/branch/remote/log, root manifests,
Strata markers, a hub above it) and then:
  - creates projects/<slug>.md from page-templates/ when the page is missing;
  - rewrites only what it owns: the frontmatter keys in OWNED, the block between
    <!-- hq-sync:changes --> and <!-- /hq-sync:end -->, wiki/index.md between
    <!-- hq-sync:index --> and <!-- /hq-sync:end -->, and the <!-- hq-sync:group --> block of one
    groups/<group>.md per group (its hub — hq.yaml `hubs:` first, else the shared Strata ancestor —
    and its projects);
  - leaves every other key and section (summary, relations, stack after creation, your own
    keys) exactly as they are.
A page whose computed state equals what is on disk is not written, so an unchanged project costs
nothing and a second run rewrites nothing. Pages that need prose from the agent (new, or code
moved) are listed under `needs_summary`.

Usage: python3 scripts/hq_sync.py [--hq DIR] [--dry-run]
Exit: 0 done · 3 registry.yaml or a page template missing/unreadable.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hq_registry import read_yaml_subset, slugify  # noqa: E402  (same folder)

OWNED = ("project", "id", "group", "hub", "path", "remote", "branch", "status", "strata",
         "synced_sha", "synced_at")
CHANGES = ("<!-- hq-sync:changes -->", "<!-- /hq-sync:end -->")
INDEX = ("<!-- hq-sync:index -->", "<!-- /hq-sync:end -->")
GROUP = ("<!-- hq-sync:group -->", "<!-- /hq-sync:end -->")
QUIET_DAYS = 90
MAX_COMMITS = 8
# Paths whose change means the page's prose may be out of date.
PROSE_TRIGGERS = re.compile(r"^(README|CLAUDE|AGENTS)\.md$|^wiki/index\.md$|^docs/|"
                            r"^(package\.json|pyproject\.toml|requirements.*\.txt|composer\.json|"
                            r"go\.mod|Cargo\.toml|Gemfile|docker-compose\.ya?ml|Dockerfile)$")
MANIFESTS = [
    ("package.json", "Node.js"), ("pyproject.toml", "Python"), ("requirements.txt", "Python"),
    ("composer.json", "PHP"), ("go.mod", "Go"), ("Cargo.toml", "Rust"), ("Gemfile", "Ruby"),
    ("pom.xml", "Java"), ("build.gradle", "JVM"),
]
FRAMEWORKS = [  # (manifest, needle, label)
    ("package.json", '"next"', "Next.js"), ("package.json", '"electron"', "Electron"),
    ("package.json", '"react"', "React"), ("package.json", '"vue"', "Vue"),
    ("package.json", '"hono"', "Hono"), ("package.json", '"express"', "Express"),
    ("pyproject.toml", "fastapi", "FastAPI"), ("requirements.txt", "fastapi", "FastAPI"),
    ("pyproject.toml", "django", "Django"), ("requirements.txt", "django", "Django"),
    ("pyproject.toml", "langgraph", "LangGraph"), ("requirements.txt", "langgraph", "LangGraph"),
    ("composer.json", "laravel/lumen", "Lumen"), ("composer.json", "laravel/framework", "Laravel"),
]
DEPLOY = [("docker-compose.yml", "Docker Compose"), ("docker-compose.yaml", "Docker Compose"),
          ("Dockerfile", "Docker"), (".gitlab-ci.yml", "GitLab CI"), (".github/workflows", "GitHub Actions")]


def git(path, *args):
    try:
        out = subprocess.run(["git", "-C", path, *args], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def read_text(path, limit=200_000):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read(limit)
    except OSError:
        return ""


def public_remote(url):
    """A remote URL without credentials: `https://user:token@host/x` → `https://host/x`."""
    return re.sub(r"^(https?://)[^/@]*@", r"\1", url) if url else url


def detect_stack(path):
    parts = []
    for name, label in MANIFESTS:
        if os.path.exists(os.path.join(path, name)) and label not in parts:
            parts.append(label)
    for name, needle, label in FRAMEWORKS:
        if label not in parts and needle in read_text(os.path.join(path, name)).lower():
            parts.append(label)
    for name, label in DEPLOY:
        if os.path.exists(os.path.join(path, name)) and label not in parts:
            parts.append(label)
    return " · ".join(parts) or None


def strata_level(path):
    if os.path.exists(os.path.join(path, "WIKI.md")) and os.path.exists(os.path.join(path, "wiki", "index.md")):
        return "full"
    return "claude-md" if os.path.exists(os.path.join(path, "CLAUDE.md")) else "none"


def find_hub(path):
    """Nearest ancestor that is itself a full Strata project (a hub over the projects below it)."""
    home = os.path.expanduser("~")
    cur = os.path.dirname(os.path.abspath(path))
    while cur and cur != os.path.dirname(cur) and cur != home:
        if strata_level(cur) == "full":
            return cur
        cur = os.path.dirname(cur)
    return None


def folder_fingerprint(path, cap=5000):
    h, n = hashlib.sha1(), 0
    for root, dirs, files in os.walk(path):
        dirs[:] = sorted(d for d in dirs if d not in (".git", "node_modules", ".venv", "__pycache__"))
        for name in sorted(files):
            full = os.path.join(root, name)
            try:
                st = os.stat(full)
            except OSError:
                continue
            h.update(f"{os.path.relpath(full, path)}\0{st.st_size}\0{int(st.st_mtime)}\n".encode())
            n += 1
            if n >= cap:
                return "fp:" + h.hexdigest()[:16]
    return "fp:" + h.hexdigest()[:16]


def read_project(entry, now, hubs):
    """Facts about one project, or None with a reason when it cannot be read from here."""
    path = entry["path"]
    if entry.get("host") not in (None, "local"):
        return None, "unverifiable"  # read through its own host later; never guess from local disk
    if not os.path.isdir(path):
        return None, "missing"
    # A hub named for the project's group in hq.yaml wins; otherwise the nearest Strata ancestor (ADR-1).
    hub = hubs.get(entry.get("group")) or find_hub(path)
    facts = {"strata": strata_level(path), "hub": hub, "stack": detect_stack(path)}
    if entry.get("kind") == "git" and git(path, "rev-parse", "--git-dir") is not None:
        facts["head"] = git(path, "rev-parse", "HEAD")
        facts["branch"] = git(path, "rev-parse", "--abbrev-ref", "HEAD")
        facts["remote"] = public_remote(git(path, "remote", "get-url", "origin"))
        last = git(path, "log", "-1", "--format=%cs")
        facts["last"] = last
        facts["status"] = "active" if last and (now.date() - dt.date.fromisoformat(last)).days <= QUIET_DAYS else "quiet"
    else:
        facts.update(head=folder_fingerprint(path), branch=None, remote=None, last=None, status="active")
    return facts, None


def split_page(text):
    m = re.match(r"^---\n(.*?)\n---\n?(.*)$", text, re.S)
    if not m:
        return None, text
    return m.group(1), m.group(2)


def fm_get(fm, key):
    m = re.search(rf"^{key}:[ \t]*(.*)$", fm, re.M)
    return m.group(1).strip() if m else None


def fm_set(fm, key, value):
    line = f"{key}: {'null' if value is None else value}"
    if re.search(rf"^{key}:", fm, re.M):
        return re.sub(rf"^{key}:.*$", lambda _: line, fm, count=1, flags=re.M)
    return fm + "\n" + line


def replace_block(body, markers, content):
    start, end = markers
    i = body.find(start)
    j = body.find(end, i + len(start)) if i >= 0 else -1
    if i < 0 or j < 0:
        return body, False
    return body[: i + len(start)] + "\n" + content.rstrip("\n") + "\n" + body[j:], True


def changes_text(entry, facts, prev_sha):
    path = entry["path"]
    if facts["branch"] is None:
        return "Folder workspace — no commits; the fingerprint changed." if prev_sha else "Folder workspace — no commits."
    rng = [f"{prev_sha}..HEAD"] if prev_sha and git(path, "cat-file", "-e", f"{prev_sha}^{{commit}}") is not None else []
    log = git(path, "log", "--no-merges", f"-{MAX_COMMITS}", "--format=- `%h` %cs %s", *rng) or ""
    if log:
        return log
    subject = git(path, "log", "-1", "--format=`%h` %s") or ""
    return f"No change since {facts['last']} ({subject})." if facts.get("last") else "No commits yet."


def needs_prose(entry, prev_sha, head):
    if not prev_sha or entry.get("kind") != "git":
        return prev_sha is None
    if git(entry["path"], "cat-file", "-e", f"{prev_sha}^{{commit}}") is None:
        return True
    files = (git(entry["path"], "diff", "--name-only", prev_sha, head) or "").splitlines()
    return any(PROSE_TRIGGERS.search(f) for f in files)


def render_new_page(template, entry, facts):
    text = template
    fm, body = split_page(text)
    if fm is None:
        raise ValueError("page template has no frontmatter")
    fm = "\n".join(line for line in fm.splitlines() if not line.lstrip().startswith("#"))
    if facts.get("stack"):
        fm = fm_set(fm, "stack", facts["stack"])
    body = body.replace("<Display name>", entry["name"])
    return fm, body


def sync_page(hq, entry, templates, now, short_slugs, hubs, dry_run):
    page_path = os.path.join(hq, "projects", f"{entry['slug']}.md")
    exists = os.path.exists(page_path)
    facts, why = read_project(entry, now, hubs)
    if facts is None:
        return {"slug": entry["slug"], "action": "skipped", "reason": why}
    if exists:
        fm, body = split_page(read_text(page_path))
        if fm is None:
            return {"slug": entry["slug"], "action": "skipped", "reason": "page has no frontmatter"}
    else:
        tpl = templates["short" if entry["slug"] in short_slugs else "full"]
        fm, body = render_new_page(tpl, entry, facts)
    prev_sha = fm_get(fm, "synced_sha") if exists else None
    prev_sha = None if prev_sha in (None, "", "null") or prev_sha.startswith("<") else prev_sha
    head = facts["head"]
    wanted = {
        "project": entry["slug"], "id": entry["id"], "group": entry.get("group"), "hub": facts["hub"],
        "path": entry["path"], "remote": facts["remote"], "branch": facts["branch"],
        "status": facts["status"], "strata": facts["strata"], "synced_sha": head,
    }
    new_fm = fm
    for key, value in wanted.items():
        new_fm = fm_set(new_fm, key, value)
    new_body = body
    moved = prev_sha != head
    if moved:
        new_body, has_block = replace_block(body, CHANGES, changes_text(entry, facts, prev_sha))
    if new_fm == fm and new_body == body and exists:
        return {"slug": entry["slug"], "action": "unchanged"}
    new_fm = fm_set(new_fm, "synced_at", now.strftime("%Y-%m-%dT%H:%M:%SZ"))
    if not dry_run:
        os.makedirs(os.path.dirname(page_path), exist_ok=True)
        tmp = page_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(f"---\n{new_fm}\n---\n{new_body}")
        os.replace(tmp, page_path)
    result = {"slug": entry["slug"], "action": "created" if not exists else "updated"}
    if not exists or (moved and needs_prose(entry, prev_sha, head)):
        result["needs_summary"] = True
    if moved and exists and CHANGES[0] not in body:
        result["note"] = "page has no hq-sync:changes block"
    return result


def page_title(hq, slug):
    text = read_text(os.path.join(hq, "projects", f"{slug}.md"))
    m = re.search(r"^# .*? — (.+)$", text, re.M)
    return m.group(1).strip() if m and not m.group(1).startswith("<") else ""


def group_link(name, prefix):
    return f"[{name}]({prefix}{slugify(name)}.md)" if name else "—"


def render_index(hq, entries):
    rows = []
    for e in sorted(entries, key=lambda e: ((e.get("group") or "~"), e["slug"])):
        title = page_title(hq, e["slug"]).replace("|", "\\|")
        rows.append(f"| [{e['slug']}](../projects/{e['slug']}.md) | {group_link(e.get('group'), '../groups/')} | {title} |")
    return "\n".join(["| Project | Group | What it is |", "|---|---|---|", *rows])


def page_hub(hq, slug):
    m = re.search(r"^hub:[ \t]*(.*)$", read_text(os.path.join(hq, "projects", f"{slug}.md")), re.M)
    value = m.group(1).strip() if m else ""
    return None if value in ("", "null") else value


def render_group(hq, name, members, hubs):
    hub = hubs.get(name)
    member_hubs = sorted({h for h in (page_hub(hq, e["slug"]) for e in members) if h})
    if hub:
        hub_line = f"- **Hub:** `{hub}` (set in hq.yaml) → `{hub}/wiki/index.md`"
    elif len(member_hubs) == 1:
        hub_line = f"- **Hub:** `{member_hubs[0]}` (every project lives under it) → `{member_hubs[0]}/wiki/index.md`"
    elif member_hubs:
        hub_line = "- **Hubs:** " + ", ".join(f"`{h}`" for h in member_hubs) + " (projects of this group live under different hubs)"
    else:
        hub_line = "- **Hub:** none — the project pages link straight to each project's own wiki"
    lines = [hub_line, f"- **Projects ({len(members)}):**"]
    for e in sorted(members, key=lambda e: e["slug"]):
        title = page_title(hq, e["slug"])
        lines.append(f"  - [{e['slug']}](../projects/{e['slug']}.md)" + (f" — {title}" if title else ""))
    return "\n".join(lines)


def sync_groups(hq, entries, hubs, dry_run):
    groups = {}
    for e in entries:
        if e.get("group"):
            groups.setdefault(e["group"], []).append(e)
    created, updated = [], []
    for name, members in sorted(groups.items()):
        path = os.path.join(hq, "groups", f"{slugify(name)}.md")
        old = read_text(path) if os.path.exists(path) else f"# {name}\n\n{GROUP[0]}\n{GROUP[1]}\n\n## Notes\n\n"
        new, has_block = replace_block(old, GROUP, render_group(hq, name, members, hubs))
        if not has_block or (new == old and os.path.exists(path)):
            continue
        (updated if os.path.exists(path) else created).append(slugify(name))
        if not dry_run:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(new)
    return created, updated


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--hq", default=".", help="HQ root (default: cwd)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    hq = os.path.abspath(args.hq)
    try:
        entries = read_yaml_subset(os.path.join(hq, "registry.yaml")).get("projects") or []
        config = read_yaml_subset(os.path.join(hq, "hq.yaml"))
    except ValueError as exc:
        print(f"hq_sync: {exc}", file=sys.stderr)
        return 3
    templates = {k: read_text(os.path.join(hq, "page-templates", f)) for k, f in
                 (("full", "project.md"), ("short", "project-short.md"))}
    if not os.path.exists(os.path.join(hq, "registry.yaml")) or not templates["full"]:
        print("hq_sync: needs registry.yaml and page-templates/project.md (run hq_registry.py first)", file=sys.stderr)
        return 3
    templates["short"] = templates["short"] or templates["full"]
    short_slugs = set(config.get("short") or [])
    hubs = {h["group"]: os.path.abspath(os.path.expanduser(h["path"])) for h in (config.get("hubs") or [])
            if isinstance(h, dict) and h.get("group") and h.get("path")}
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    results = [sync_page(hq, e, templates, now, short_slugs, hubs, args.dry_run) for e in entries]
    groups_created, groups_updated = sync_groups(hq, entries, hubs, args.dry_run)

    index_path = os.path.join(hq, "wiki", "index.md")
    index_old = read_text(index_path)
    index_new, has_index = replace_block(index_old, INDEX, render_index(hq, entries))
    index_changed = has_index and index_new != index_old
    if index_changed and not args.dry_run:
        with open(index_path, "w", encoding="utf-8") as f:
            f.write(index_new)

    by = lambda action: sorted(r["slug"] for r in results if r["action"] == action)  # noqa: E731
    report = {
        "created": by("created"), "updated": by("updated"), "unchanged": len(by("unchanged")),
        "skipped": {r["slug"]: r["reason"] for r in results if r["action"] == "skipped"},
        "needs_summary": sorted(r["slug"] for r in results if r.get("needs_summary")),
        "notes": {r["slug"]: r["note"] for r in results if r.get("note")},
        "groups_created": groups_created, "groups_updated": groups_updated,
        "index_changed": index_changed, "index_block": has_index, "dry_run": args.dry_run,
    }
    changed = report["created"] or report["updated"] or index_changed or groups_created or groups_updated
    if changed and not args.dry_run:
        line = (f"[{now.strftime('%Y-%m-%dT%H:%M:%SZ')}] sync → created {len(report['created'])}, "
                f"updated {len(report['updated'])}, unchanged {report['unchanged']}, "
                f"skipped {len(report['skipped'])}\n")
        with open(os.path.join(hq, "wiki", "log.md"), "a", encoding="utf-8") as f:
            f.write(line)
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
