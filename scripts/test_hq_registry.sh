#!/usr/bin/env bash
# Behavioural test for template/scripts/hq_registry.py — HQ's registry from the source in
# hq.yaml (ADR-1). Every project, group and path below is MADE UP; the `orca` source is driven
# by a fake CLI that prints fixture JSON.
#
# Usage: bash scripts/test_hq_registry.sh          (exit 0 = all green)

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REG="$ROOT/template/scripts/hq_registry.py"
pass=0; fail=0
ok()   { echo "  ✓ $1"; pass=$((pass+1)); }
bad()  { echo "  ✗ $1"; fail=$((fail+1)); }
check(){ if [ "$2" = "$3" ]; then ok "$1"; else bad "$1 (got '$2', want '$3')"; fi; }

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
export PYTHONDONTWRITEBYTECODE=1 REG
HQ="$WORK/HQ"; mkdir -p "$HQ/projects"
run() { python3 "$REG" --hq "$HQ" "$@"; }
json_key() { python3 -c 'import json,sys;print(json.load(sys.stdin)[sys.argv[1]])' "$1"; }
# field <id> <key>: one value from registry.yaml, read back by the script's own parser.
field() { python3 - "$HQ/registry.yaml" "$1" "$2" <<'PY'
import importlib.util, os, sys
spec = importlib.util.spec_from_file_location("hq_registry", os.environ["REG"])
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
hit = [e for e in m.read_yaml_subset(sys.argv[1]).get("projects") or [] if e["id"] == sys.argv[2]]
print(hit[0].get(sys.argv[3]) if hit else "<absent>")
PY
}
count() { grep -c '^  - id:' "$HQ/registry.yaml"; }

echo "== hq registry: paths source (default) =="
mkdir -p "$WORK/code/alpha/.git" "$WORK/code/notes" "$WORK/code/other/alpha"
cat > "$HQ/hq.yaml" <<EOF
# made-up fixture
source: paths
paths:
  - "$WORK/code/alpha"
  - "$WORK/code/notes"
  - "$WORK/code/other/alpha"
  - "$WORK/code/alpha"
  - "$HQ"
EOF
out="$(run)"; check "first run exits 0" "$?" 0
check "HQ itself and the duplicate are not listed (3 of 5)" "$(count)" 3
check "id of a path entry is its absolute path" "$(field "$WORK/code/alpha" path)" "$WORK/code/alpha"
check "a dir with .git is kind git" "$(field "$WORK/code/alpha" kind)" git
check "a dir without .git is kind folder" "$(field "$WORK/code/notes" kind)" folder
check "colliding name gets a suffix, first listed keeps the plain slug" "$(field "$WORK/code/other/alpha" slug)" alpha-2
check "no group for the paths source" "$(field "$WORK/code/notes" group)" None
before="$(cksum < "$HQ/registry.yaml")"; touch -t 200001010000 "$HQ/registry.yaml"
check "second run without changes reports changed=false" "$(run | json_key changed)" False
check "…and does not rewrite the file (mtime kept)" "$(find "$HQ/registry.yaml" -newermt 2001-01-01 | wc -l | tr -d ' ')" 0
check "…content identical" "$(cksum < "$HQ/registry.yaml")" "$before"
printf '# page\n' > "$HQ/projects/notes.md"
sed -i.bak "/code\/notes/d" "$HQ/hq.yaml" && rm -f "$HQ/hq.yaml.bak"
run >/dev/null
check "a path dropped from hq.yaml leaves the registry" "$(field "$WORK/code/notes" slug)" "<absent>"
check "…and its page moves to projects/_archive/" "$([ -f "$HQ/projects/_archive/notes.md" ] && [ ! -f "$HQ/projects/notes.md" ] && echo yes)" yes
rm -rf "$WORK/code/other"
out="$(run)"
check "a listed path missing on disk stays, with a warning" "$(field "$WORK/code/other/alpha" slug) $(printf '%s' "$out" | json_key warnings | grep -c 'not a directory')" "alpha-2 1"

echo "== hq registry: orca source =="
rm -rf "$HQ"; mkdir -p "$HQ/projects"
FAKE="$WORK/fake-orca"
# fake-orca reads $WORK/repos.json / groups.json; FAKE_FAIL=list|groups makes that call fail.
cat > "$FAKE" <<EOF
#!/usr/bin/env bash
case "\$1 \$2" in
  "repo list")   [ "\${FAKE_FAIL:-}" = list ] && { echo '{"id":"x","ok":false,"error":{"message":"not running"}}'; exit 1; }
                 printf '{"id":"x","ok":true,"result":{"repos":%s}}' "\$(cat "$WORK/repos.json")" ;;
  "repo groups") [ "\${FAKE_FAIL:-}" = groups ] && { echo "unknown command: repo groups" >&2; exit 1; }
                 printf '{"id":"x","ok":true,"result":{"groups":%s}}' "\$(cat "$WORK/groups.json")" ;;
  *) exit 64 ;;
esac
EOF
chmod +x "$FAKE"
printf 'source: orca\norca_cli: "%s"\n' "$FAKE" > "$HQ/hq.yaml"
repos() { printf '%s' "$1" > "$WORK/repos.json"; }
printf '[{"id":"g1","name":"Fixture Group"},{"id":"g2","name":"Other Group"}]' > "$WORK/groups.json"
repos '[
 {"id":"r1","displayName":"Alpha App","path":"/fixture/alpha","kind":"git","projectGroupId":"g1","addedAt":1},
 {"id":"r2","displayName":"beta","path":"/fixture/beta","kind":"folder","addedAt":2},
 {"id":"r3","displayName":"alpha app","path":"/fixture/other/alpha","kind":"git","addedAt":3},
 {"id":"r4","displayName":"remote","path":"/srv/remote","kind":"git","connectionId":"box1","addedAt":4},
 {"id":"rhq","displayName":"HQ","path":"'"$HQ"'","kind":"git","addedAt":5}]'
out="$(run)"; check "first run exits 0" "$?" 0
check "HQ itself is not listed (4 of 5 repos)" "$(count)" 4
check "group name comes from repo groups" "$(field r1 group)" "Fixture Group"
check "SSH repo carries its host" "$(field r4 host)" "ssh:box1"
check "slug from the name; collision in addedAt order" "$(field r1 slug) $(field r3 slug)" "alpha-app alpha-app-2"
repos '[
 {"id":"r1","displayName":"Alpha Renamed","path":"/fixture/moved/alpha","kind":"git","projectGroupId":"g2","addedAt":1},
 {"id":"r2","displayName":"beta","path":"/fixture/beta","kind":"folder","addedAt":2},
 {"id":"r4","displayName":"remote","path":"/srv/remote","kind":"git","connectionId":"box1","addedAt":4}]'
run >/dev/null
check "rename keeps the slug, updates name and path" "$(field r1 slug) | $(field r1 name) | $(field r1 path)" "alpha-app | Alpha Renamed | /fixture/moved/alpha"
check "group change follows Orca" "$(field r1 group)" "Other Group"
check "a repo gone from a successful listing leaves" "$(field r3 slug)" "<absent>"
before="$(cksum < "$HQ/registry.yaml")"
out="$(FAKE_FAIL=groups run)"; check "a CLI without repo groups still exits 0" "$?" 0
check "…keeps the previous group names" "$(field r1 group)" "Other Group"
check "…and says so in warnings" "$(printf '%s' "$out" | json_key warnings | grep -c 'group names unavailable')" 1
check "…registry unchanged" "$(cksum < "$HQ/registry.yaml")" "$before"
FAKE_FAIL=list run >/dev/null 2>&1; check "Orca not running → exit 2" "$?" 2
check "…registry untouched" "$(cksum < "$HQ/registry.yaml")" "$before"
printf 'source: orca\norca_cli: "%s"\n' "$WORK/no-such-cli" > "$HQ/hq.yaml"
run >/dev/null 2>&1; check "missing CLI → exit 2" "$?" 2
check "…registry untouched" "$(cksum < "$HQ/registry.yaml")" "$before"

echo "== hq registry: config errors and dry run =="
printf 'source: nowhere\n' > "$HQ/hq.yaml"
run >/dev/null 2>&1; check "unknown source → exit 3" "$?" 3
printf 'source: paths\n%%%% not yaml\n' > "$HQ/hq.yaml"
run >/dev/null 2>&1; check "unreadable hq.yaml → exit 3" "$?" 3
check "…registry untouched" "$(cksum < "$HQ/registry.yaml")" "$before"
printf 'source: paths\npaths:\n  - "%s"\n' "$WORK/code/alpha" > "$HQ/hq.yaml"
out="$(run --dry-run)"
check "dry run reports the change" "$(printf '%s' "$out" | json_key changed)" True
check "…but writes nothing" "$(cksum < "$HQ/registry.yaml")" "$before"
rm -rf "$HQ"; mkdir -p "$HQ"; cp "$ROOT/template/hq.yaml" "$HQ/hq.yaml"
out="$(run)"; check "the shipped hq.yaml (empty paths) gives an empty registry" "$? $(printf '%s' "$out" | json_key projects)" "0 0"

echo
echo "HQ registry: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
