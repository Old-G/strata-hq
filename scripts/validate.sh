#!/usr/bin/env bash
# Validate the strata-hq plugin: manifests, skills, templates, behaviour tests, nothing private.
# Usage: bash scripts/validate.sh   (exit 0 = all green)
cd "$(dirname "$0")/.." || exit 2
fail=0
ok()  { echo "  ✓ $1"; }
err() { echo "  ✗ $1"; fail=$((fail+1)); }

echo "== 1. manifests parse and versions agree =="
python3 - <<'PY' && ok "plugin.json = marketplace.json" || err "manifest check failed"
import json, sys
p = json.load(open(".claude-plugin/plugin.json")); m = json.load(open(".claude-plugin/marketplace.json"))
entry = next(x for x in m["plugins"] if x["name"] == p["name"])
sys.exit(0 if entry["version"] == p["version"] else 1)
PY

echo "== 2. skills: name matches folder, EN+RU triggers, a Do-NOT guard =="
python3 - <<'PY' && ok "skills well-formed" || err "skill check failed (see above)"
import glob, os, re, sys
bad = False
for path in sorted(glob.glob("skills/*/SKILL.md")):
    text = open(path, encoding="utf-8").read()
    name = re.search(r"^name:\s*(\S+)", text, re.M)
    desc = re.search(r"^description:[ \t]*(.+?)(?=^\w+:|^---)", text, re.M | re.S)
    desc = " ".join(desc.group(1).split()) if desc else ""
    problems = []
    if not name or name.group(1) != os.path.basename(os.path.dirname(path)): problems.append("name != folder")
    if not desc or len(desc) > 1024: problems.append("description missing or > 1024 chars")
    if not re.search(r"[Ѐ-ӿ]", desc): problems.append("no Russian trigger")
    if "## Do NOT use when" not in text: problems.append("no '## Do NOT use when'")
    if problems: print(f"  {path}: {', '.join(problems)}", file=sys.stderr); bad = True
sys.exit(1 if bad else 0)
PY

echo "== 3. python templates parse =="
for f in template/scripts/*.py; do
  python3 -c "import ast,sys; ast.parse(open(sys.argv[1]).read())" "$f" && ok "$f" || err "$f has a syntax error"
done

echo "== 4. behaviour tests =="
for t in scripts/test_hq_registry.sh scripts/test_hq_sync.sh; do
  if out="$(bash "$t" 2>&1)"; then ok "$(printf '%s' "$out" | tail -n 1)"; else printf '%s\n' "$out" | grep '✗' >&2; err "$t failed"; fi
done

echo "== 5. no private markers in tracked files (public repo) =="
# Built in: a real home directory (case-sensitive: /Users/USER placeholders are fine). Your own
# words go one ERE per line into .private-markers, which is gitignored.
hits="$(git grep -nIE '/(Users|home)/[a-z][a-z0-9_-]+/' -- . 2>/dev/null)"
if [ -f .private-markers ]; then
  markers=""
  while IFS= read -r m; do case "$m" in ''|'#'*) continue ;; esac; markers="${markers:+$markers|}$m"; done < .private-markers
  [ -n "$markers" ] && hits="$hits$(git grep -nIiE "$markers" -- . ':!.private-markers' 2>/dev/null)"
fi
if [ -n "$hits" ]; then printf '%s\n' "$hits" | cut -c1-160 >&2; err "private marker found (above)"; else ok "nothing private"; fi

echo
if [ "$fail" -eq 0 ]; then echo "✅ strata-hq validation PASSED"; else echo "❌ validation FAILED"; fi
exit "$fail"
