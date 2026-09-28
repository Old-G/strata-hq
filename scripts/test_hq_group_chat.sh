#!/usr/bin/env bash
# Behavioural test for template/scripts/hq_group_chat.py — the chat folder of one group.
# Every project, group and path below is MADE UP, created in a throwaway dir.
#
# Usage: bash scripts/test_hq_group_chat.sh          (exit 0 = all green)

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
pass=0; fail=0
ok()   { echo "  ✓ $1"; pass=$((pass+1)); }
bad()  { echo "  ✗ $1"; fail=$((fail+1)); }
check(){ if [ "$2" = "$3" ]; then ok "$1"; else bad "$1 (got '$2', want '$3')"; fi; }

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
export PYTHONDONTWRITEBYTECODE=1

mkdir -p "$WORK/hub/alpha" "$WORK/hub/beta" "$WORK/solo"
printf 'a\n' > "$WORK/hub/alpha/a.txt"; printf 'b\n' > "$WORK/hub/beta/b.txt"
HQ="$WORK/HQ"; cp -R "$ROOT/template" "$HQ"
entry() { printf '  - id: "%s"\n    slug: "%s"\n    name: "%s"\n    path: "%s"\n    kind: "folder"\n    host: "%s"\n    group: "%s"\n    group_id: "%s"\n' "$@"; }
registry() {  # registry <entries...> — writes registry.yaml from the given entry lines
  { printf 'source: "paths"\nprojects:\n'; cat; } > "$HQ/registry.yaml"
}
{ entry r-alpha alpha alpha "$WORK/hub/alpha" local "Team A" g-a
  entry r-beta beta beta "$WORK/hub/beta" local "Team A" g-a
  entry r-far far far "/srv/far" "ssh:made-up-host" "Team A" g-a
  entry r-gone gone gone "$WORK/hub/gone" local "Team A" g-a
  entry r-solo solo solo "$WORK/solo" local "Team B" g-b; } | registry
printf -- '---\nproject: alpha\nhub: %s\n---\n# alpha — made-up service\n' "$WORK/hub" > "$HQ/projects/alpha.md"
printf -- '---\nproject: beta\nhub: %s\n---\n# beta — made-up worker\n' "$WORK/hub" > "$HQ/projects/beta.md"
chat() { python3 "$HQ/scripts/hq_group_chat.py" --hq "$HQ" "$@"; }
jq_()  { python3 -c 'import json,sys;d=json.load(sys.stdin);v=d;[v:=v[k] for k in sys.argv[1:]];print(v)' "$@"; }
C="$HQ/chats/team-a"

echo "== first open, by id =="
out="$(chat --group-id g-a)"; check "exit 0" "$?" "0"
check "report path" "$(printf '%s' "$out" | jq_ path)" "$C"
check "linked alpha and beta" "$(printf '%s' "$out" | jq_ linked)" "['alpha', 'beta']"
check "remote project skipped" "$(printf '%s' "$out" | jq_ skipped far)" "on another host"
check "missing path skipped" "$(printf '%s' "$out" | jq_ skipped gone)" "path missing"
check "changed on first open" "$(printf '%s' "$out" | jq_ changed)" "True"
check "link resolves into the project" "$(cat "$C/alpha/a.txt")" "a"
check "no link for the remote project" "$([ -e "$C/far" ] && echo yes || echo no)" "no"
grep -q '`../../groups/team-a.md`' "$C/CLAUDE.md" && ok "CLAUDE.md points at the group page" || bad "CLAUDE.md lacks the group page"
grep -q "Hub:\*\* \`$WORK/hub\`" "$C/CLAUDE.md" && ok "CLAUDE.md names the shared hub" || bad "CLAUDE.md lacks the hub"
grep -q 'page `../../projects/alpha.md` — made-up service' "$C/CLAUDE.md" && ok "CLAUDE.md lists a project with its page title" || bad "CLAUDE.md project line wrong"
grep -q '`/srv/far` (on another host)' "$C/CLAUDE.md" && ok "CLAUDE.md says why a project is not linked" || bad "CLAUDE.md lacks the skip reason"
dirs="$(jq_ permissions additionalDirectories < "$C/.claude/settings.local.json")"
check "Claude Code may read HQ, the projects and the hub" "$dirs" "['$HQ', '$WORK/hub/alpha', '$WORK/hub/beta', '$WORK/hub']"

echo "== second open rewrites nothing =="
out="$(chat --group-id g-a)"
check "changed false" "$(printf '%s' "$out" | jq_ changed)" "False"

echo "== what is not ours stays =="
printf 'mine\n' > "$C/notes.md"
python3 - "$C/.claude/settings.local.json" <<'PY'
import json, sys
p = sys.argv[1]; d = json.load(open(p)); d["permissions"]["allow"] = ["Bash(ls:*)"]; d["model"] = "made-up"
json.dump(d, open(p, "w"))
PY
out="$(chat --group-id g-a)"
check "own file kept" "$(cat "$C/notes.md")" "mine"
check "saved permission kept" "$(jq_ permissions allow < "$C/.claude/settings.local.json")" "['Bash(ls:*)']"
check "other settings key kept" "$(jq_ model < "$C/.claude/settings.local.json")" "made-up"

echo "== a project leaves the group, the group is renamed =="
{ entry r-alpha alpha alpha "$WORK/hub/alpha" local "Team A2" g-a
  entry r-solo solo solo "$WORK/solo" local "Team B" g-b; } | registry
out="$(chat --group-id g-a --group "Team A")"
check "renamed group found by id" "$(printf '%s' "$out" | jq_ group)" "Team A2"
C2="$HQ/chats/team-a2"
check "folder follows the new name" "$(printf '%s' "$out" | jq_ path)" "$C2"
check "only alpha linked" "$(printf '%s' "$out" | jq_ linked)" "['alpha']"
mkdir -p "$C2"; ln -s "$WORK/hub/beta" "$C2/beta"; : > "$C2/taken"
out="$(chat --group-id g-a)"
check "stale link removed" "$([ -L "$C2/beta" ] && echo yes || echo no)" "no"
check "a plain file is never removed" "$([ -e "$C2/taken" ] && echo yes || echo no)" "yes"

echo "== by name, and unknown groups =="
out="$(chat --group "Team B")"
check "found by name" "$(printf '%s' "$out" | jq_ linked)" "['solo']"
grep -q 'Hub:\*\* none' "$HQ/chats/team-b/CLAUDE.md" && ok "no hub said plainly" || bad "hub line wrong without a hub"
chat --group "Nobody" >/dev/null 2>&1; check "unknown group exits 4" "$?" "4"
chat >/dev/null 2>&1; check "no group given exits 2" "$?" "2"
rm "$HQ/registry.yaml"; chat --group "Team B" >/dev/null 2>&1; check "no registry exits 3" "$?" "3"

echo "== members untouched =="
check "project folders unchanged" "$(ls "$WORK/hub/alpha" "$WORK/hub/beta" | tr '\n' ' ')" "$WORK/hub/alpha: a.txt  $WORK/hub/beta: b.txt "

echo
echo "hq_group_chat: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
