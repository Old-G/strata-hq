#!/usr/bin/env bash
# Behavioural test for template/scripts/hq_brain.py (+ hq_stem.py) — the brain's local index.
# Every chat, note, person and project below is MADE UP, created in a throwaway dir.
#
# Usage: bash scripts/test_hq_brain.sh          (exit 0 = all green)

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
pass=0; fail=0
ok()   { echo "  ✓ $1"; pass=$((pass+1)); }
bad()  { echo "  ✗ $1"; fail=$((fail+1)); }
check(){ if [ "$2" = "$3" ]; then ok "$1"; else bad "$1 (got '$2', want '$3')"; fi; }

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
export PYTHONDONTWRITEBYTECODE=1
export GIT_AUTHOR_NAME=Fixture GIT_AUTHOR_EMAIL=fixture@example.invalid GIT_COMMITTER_NAME=Fixture GIT_COMMITTER_EMAIL=fixture@example.invalid
G() { git -c core.hooksPath=/dev/null -c commit.gpgsign=false -C "$@"; }
at() { GIT_AUTHOR_DATE="$2" GIT_COMMITTER_DATE="$2" G "$1" commit -q -am "$3"; }

HQ="$WORK/HQ"; cp -R "$ROOT/template" "$HQ"
brain() { python3 "$HQ/scripts/hq_brain.py" --hq "$HQ" "$@"; }
keys()  { brain search "$@" --json | python3 -c 'import json,sys;print(" ".join(h["key"] for h in json.load(sys.stdin)["hits"]))'; }
field() { python3 -c 'import json,sys;d=json.load(sys.stdin);v=d;[v:=v[int(k) if k.isdigit() else k] for k in sys.argv[1:]];print(v)' "$@"; }

echo "== stemmer =="
check "Russian forms meet: ожидание / ожиданиях / ожиданий" \
  "$(python3 -c 'import sys;sys.path.insert(0,sys.argv[1]);from hq_stem import stem;print({stem(w) for w in ("ожидание","ожиданиях","ожиданий","ожидания")})' "$HQ/scripts")" "{'ожидан'}"
check "English Porter2: snippets / running / hoping / generalization" \
  "$(python3 -c 'import sys;sys.path.insert(0,sys.argv[1]);from hq_stem import stem;print(*[stem(w) for w in ("snippets","running","hoping","generalization")])' "$HQ/scripts")" "snippet run hope general"

# ---------------------------------------------------------------- made-up sources
CL="$WORK/claude/projects/-work-alpha"; AR="$WORK/archive/-work-alpha"; mkdir -p "$CL" "$AR" "$WORK/codex/2026/01/12"
S1=11111111-aaaa-4aaa-8aaa-000000000001; S2=11111111-aaaa-4aaa-8aaa-000000000002
S3=11111111-aaaa-4aaa-8aaa-000000000003; S4=11111111-aaaa-4aaa-8aaa-000000000004
PASTE="Решение по складу: отгрузку для Acme переносим на понедельник, потому что поставщик не успевает упаковать заказ вовремя."
python3 - "$CL" "$AR" "$PASTE" "$S1" "$S2" "$S3" "$S4" <<'PY'
import json, sys, subprocess
cl, ar, paste, s1, s2, s3, s4 = sys.argv[1:]
def user(sid, ts, text, **kw):
    return {"parentUuid": None, "isSidechain": False, "type": "user", "sessionId": sid, "cwd": "/work/alpha",
            "message": {"role": "user", "content": text}, "uuid": f"u-{ts}", "timestamp": ts, **kw}
def asst(sid, ts, *blocks):
    return {"parentUuid": None, "isSidechain": False, "type": "assistant", "sessionId": sid, "cwd": "/work/alpha",
            "message": {"role": "assistant", "content": list(blocks)}, "uuid": f"a-{ts}", "timestamp": ts}
def text(t): return {"type": "text", "text": t}
def write(path, rows):
    with open(path, "w") as f:
        for r in rows: f.write(json.dumps(r, ensure_ascii=False) + "\n")
write(f"{cl}/{s1}.jsonl", [
    {"type": "ai-title", "aiTitle": "Склад и отгрузка", "sessionId": s1},
    user(s1, "2026-01-10T09:00:00Z", "<system-reminder>quokkabanner</system-reminder>Что у нас по ожиданиям от Ann?"),
    asst(s1, "2026-01-10T09:01:00Z", {"type": "thinking", "thinking": "thinkonly"}, text("В ожиданиях два пункта. DEV-12345 и MR !19 в snippets/acme-gateway.conf.")),
    asst(s1, "2026-01-10T09:02:00Z", {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "toolinputword"}}),
    {"parentUuid": None, "isSidechain": False, "type": "user", "sessionId": s1, "timestamp": "2026-01-10T09:02:01Z",
     "message": {"role": "user", "content": [{"tool_use_id": "t1", "type": "tool_result", "content": "zebracorn output"}]},
     "toolUseResult": {"stdout": "zebracorn"}},
    asst(s1, "2026-01-10T09:03:00Z", text(paste)),
])
# a later chat that pastes the same block and adds nothing
write(f"{cl}/{s2}.jsonl", [user(s2, "2026-01-12T10:00:00Z", paste)])
# a later chat, with a word only it has
write(f"{cl}/{s3}.jsonl", [user(s3, "2026-02-20T10:00:00Z", "Маяк lighthouse: проверим в феврале.")])
# a chat that survives only in the zstd archive
rows = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in [asst(s4, "2026-01-05T08:00:00Z", text("Архивный чат: walrusword."))])
try:
    from compression import zstd
    with zstd.open(f"{ar}/{s4}.jsonl.zst", "wb") as f: f.write(rows.encode())
except ImportError:
    subprocess.run(["zstd", "-q", "-o", f"{ar}/{s4}.jsonl.zst"], input=rows.encode(), check=True)
PY
cat > "$WORK/codex/2026/01/12/rollout-2026-01-12T10-00-00-c0dex.jsonl" <<'EOF'
{"timestamp":"2026-01-12T10:00:00Z","type":"session_meta","payload":{"id":"c0dex000-0000-4000-8000-000000000001","cwd":"/work/alpha"}}
{"timestamp":"2026-01-12T10:00:01Z","type":"response_item","payload":{"type":"message","role":"user","content":[{"type":"input_text","text":"# AGENTS.md instructions for /work/alpha\nagentsnoise"}]}}
{"timestamp":"2026-01-12T10:00:02Z","type":"response_item","payload":{"type":"message","role":"developer","content":[{"type":"input_text","text":"developernoise"}]}}
{"timestamp":"2026-01-12T10:00:03Z","type":"response_item","payload":{"type":"message","role":"assistant","content":[{"type":"output_text","text":"Pelican deploy is green."}]}}
EOF
cat > "$WORK/feed.py" <<EOF
import json, os
rows = [
  {"id": "m1", "doc": "chat-1", "aliases": ["$S1"], "source": "made-up-chat", "ts": "2026-01-10T09:30:00Z",
   "text": "Ann ответила: ждём до пятницы.", "title": "Склад", "project": "alpha"},
  {"id": "d1", "doc": "decisions:d1", "ts": 1768212000, "kind": "decision", "title": "Archive policy",
   "text": os.environ.get("FEED_TEXT", "Решили: архив храним девяносто дней.")},
]
for r in rows: print(json.dumps(r, ensure_ascii=False))
EOF
mkdir -p "$WORK/alpha/wiki"; printf '# Alpha\n\nTapir notes for the alpha service.\n' > "$WORK/alpha/wiki/index.md"
git init -q -b main "$WORK/alpha"; G "$WORK/alpha" add -A; at "$WORK/alpha" "2026-01-02T12:00:00" "init"
printf 'source: "paths"\nprojects:\n  - id: "a"\n    slug: "alpha"\n    name: "alpha"\n    path: "%s"\n    kind: "git"\n    host: "local"\n' "$WORK/alpha" > "$HQ/registry.yaml"
mkdir -p "$HQ/plans" "$HQ/brain"
printf '# Roadmap\n\n## Склейка дублей\n\nСклейка дублей в хабе: сначала точные совпадения.\n' > "$HQ/plans/roadmap.md"
printf '# Eval\n\nexcludedword\n' > "$HQ/brain/notes.md"
python3 -c 'import sys;open(sys.argv[1],"w").write("".join(f"## Part {i}\n\nfiller{i}\n\n" for i in range(1100))+"## Last\n\nyakword\n")' "$HQ/plans/many.md"
git init -q -b main "$HQ"; G "$HQ" add -A; at "$HQ" "2026-01-05T12:00:00" "init"
printf '\n## Позже\n\nОкапи появится в марте.\n' >> "$HQ/plans/roadmap.md"; at "$HQ" "2026-03-01T12:00:00" "later"
cat >> "$HQ/hq.yaml" <<EOF
brain_index: "$WORK/index/brain.sqlite"
brain:
  - kind: "claude-transcripts"
    name: "claude"
    path: "$WORK/claude/projects"
    archive: "$WORK/archive"
    group: "chat"
  - kind: "codex-sessions"
    name: "codex"
    path: "$WORK/codex"
    group: "chat"
  - kind: "command"
    name: "feed"
    run: "python3 $WORK/feed.py"
  - kind: "markdown"
    name: "hq"
    path: "."
    exclude: ["brain/"]
  - kind: "markdown"
    name: "wiki"
    registry: true
EOF

echo "== index =="
out="$(brain index 2>&1)"; check "first index exits 0" "$?" 0
check "every source indexed" "$(printf '%s\n' "$out" | grep -c '✓')" 5
check "index lives where hq.yaml says, outside the HQ" "$([ -f "$WORK/index/brain.sqlite" ] && echo yes)" yes

echo "== search =="
check "«ожиданиях» finds what «ожидание» finds" "$(keys "ожиданиях")" "$(keys "ожидание")"
check "…and that is the chat, grouped under its alias doc" "$(keys "ожидание" --limit 1)" "chat-1"
check "an id matches exactly" "$(keys "DEV-12345" --limit 1)" "chat-1"
check "a merge request number matches" "$(keys "!19" --limit 1)" "chat-1"
check "a file name matches" "$(keys "acme-gateway.conf" --limit 1)" "chat-1"
check "tool output is not indexed" "$(keys "zebracorn")" ""
check "tool input and thinking are not indexed" "$(keys "toolinputword thinkonly")" ""
check "hook boilerplate is not indexed" "$(keys "quokkabanner")" ""
check "a copy-pasted block ranks under its first occurrence" "$(keys "склад отгрузка поставщик упаковать" --limit 2)" "chat-1 $S2"
check "…and says what it repeats" "$(brain search "поставщик упаковать" --json | field hits 1 repeat_of)" "chat-1"
check "--before hides what came later" "$(keys "lighthouse" --before 2026-02-01)" ""
check "…and shows it after" "$(keys "lighthouse" --before 2026-03-01)" "$S3"
check "a chat kept only in the archive" "$(keys "walrusword")" "$S4"
check "codex: assistant text in, AGENTS.md and developer text out" "$(keys "pelican") | $(keys "agentsnoise developernoise")" "c0dex000-0000-4000-8000-000000000001 | "
check "a command row is searchable" "$(keys "архив девяносто" --limit 1)" "decisions:d1"
check "markdown section, dated by git blame" "$(keys "склейка дублей" --before 2026-02-01 --limit 1)" "plans/roadmap.md"
check "…a section added later is not there before it was written" "$(keys "окапи" --before 2026-02-01)" ""
check "excluded folders stay out" "$(keys "excludedword")" ""
check "a file of 1100 sections from one commit (one timestamp) is indexed whole" "$(keys "yakword")" "plans/many.md"
check "project wikis from the registry" "$(keys "tapir")" "alpha/wiki/index.md"
check "--project filters" "$(keys "ожидание" --project nowhere)" ""
check "filler and stopwords alone find nothing" "$(keys "слушай мы же вроде")" ""
out="$(brain search "DEV-12345" --json)"
check "json carries aliases, source, snippet" "$(printf '%s' "$out" | field hits 0 aliases) $(printf '%s' "$out" | python3 -c 'import json,sys;h=json.load(sys.stdin)["hits"][0];print(bool(h["snippet"]), "DEV-12345" in h["snippet"])')" "['$S1'] True True"

echo "== incremental =="
out="$(brain index 2>&1)"
check "a second run re-reads nothing" "$(printf '%s\n' "$out" | grep -c ' 0 (re)indexed')" 5
python3 - "$CL/$S3.jsonl" <<'PY'
import json, sys
with open(sys.argv[1], "a") as f:
    f.write(json.dumps({"parentUuid": None, "isSidechain": False, "type": "assistant", "sessionId": "x", "timestamp": "2026-02-21T10:00:00Z",
                        "message": {"role": "assistant", "content": [{"type": "text", "text": "Narwhal added later."}]}}) + "\n")
PY
touch -t 202602211200 "$CL/$S3.jsonl"
out="$(FEED_TEXT="Решили: архив храним сто двадцать дней." brain index 2>&1)"
check "a grown chat file is re-read, alone" "$(printf '%s\n' "$out" | grep -c 'claude: .* 1 (re)indexed')" 1
check "a changed command row is replaced, alone" "$(printf '%s\n' "$out" | grep -c 'feed: 2 seen, 1 (re)indexed')" 1
check "new text found, old text gone" "$(keys "narwhal") | $(keys "двадцать" --limit 1) | $(keys "девяносто")" "$S3 | decisions:d1 | "

echo "== eval =="
cat > "$WORK/questions.yaml" <<EOF
questions:
  - id: q1
    asked_at: "2026-01-11T12:00:00+00:00"
    asked_in: "chat-9"
    question: "слушай, а что мы там по ожиданиям от Ann решили?"
    keywords: "ожидания Ann"
    sources:
      - kind: chat
        ref: "made:chat-1 claude:$S1 @2026-01-10T09:01:00Z (assistant)"
  - id: q2
    asked_at: "2026-02-01T12:00:00+00:00"
    question: "мы проверяли lighthouse?"
    sources:
      - kind: chat
        ref: "claude:$S3"
  - id: q3
    asked_at: "2026-02-01T12:00:00+00:00"
    question: "где план про склейку дублей?"
    sources:
      - kind: hq
        ref: "plans/roadmap.md:3 (made-up)"
EOF
out="$(brain eval "$WORK/questions.yaml" --json)"; check "eval exits 0" "$?" 0
check "strict recall: a question finds only what existed before it" "$(printf '%s' "$out" | field totals typed strict)/$(printf '%s' "$out" | field totals typed asked)" "2/3"
check "keywords field runs too" "$(printf '%s' "$out" | field totals keywords strict)/$(printf '%s' "$out" | field totals keywords asked)" "1/1"
check "without the date filter the later chat counts" "$(brain eval "$WORK/questions.yaml" --no-before --json | field totals typed strict)" 3
check "latency is reported" "$(printf '%s' "$out" | python3 -c 'import json,sys;l=json.load(sys.stdin)["latency"];print(l["queries"], l["p95_ms"] is not None)')" "4 True"

echo "== config errors =="
printf 'brain:\n  - name: "nokind"\n' > "$WORK/bad.yaml"; mkdir -p "$WORK/bad"; cp "$WORK/bad.yaml" "$WORK/bad/hq.yaml"
python3 "$HQ/scripts/hq_brain.py" --hq "$WORK/bad" index >/dev/null 2>&1; check "a source without kind exits 3" "$?" 3
rm -f "$WORK/bad/hq.yaml"; printf 'source: "paths"\n' > "$WORK/bad/hq.yaml"
python3 "$HQ/scripts/hq_brain.py" --hq "$WORK/bad" search "x" >/dev/null 2>&1; check "search without an index exits 3" "$?" 3

echo
echo "hq_brain: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
