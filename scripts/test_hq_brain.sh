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
# a chat whose rows put "type" first (key order is not fixed)
s5 = "11111111-aaaa-4aaa-8aaa-000000000005"
write(f"{cl}/{s5}.jsonl", [{"type": "user", "sessionId": s5, "timestamp": "2026-01-14T10:00:00Z",
                            "message": {"role": "user", "content": "Ocelot ocelotword first."}},
                           {"type": "assistant", "sessionId": s5, "timestamp": "2026-01-14T10:01:00Z",
                            "message": {"role": "assistant", "content": [text("Reply with marmotword.")]}}])
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
check "a row with \"type\" first is still a message" "$(keys "ocelotword") $(keys "marmotword")" "11111111-aaaa-4aaa-8aaa-000000000005 11111111-aaaa-4aaa-8aaa-000000000005"
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

echo "== atoms =="
S5=11111111-aaaa-4aaa-8aaa-000000000005; S6=11111111-aaaa-4aaa-8aaa-000000000006
python3 - "$CL" "$S5" "$S6" <<'PY'
import json, sys
cl, s5, s6 = sys.argv[1:]
def row(sid, kind, ts, text):
    content = text if kind == "user" else [{"type": "text", "text": text}]
    return {"parentUuid": None, "isSidechain": False, "type": kind, "sessionId": sid, "cwd": "/work/alpha",
            "message": {"role": kind, "content": content}, "uuid": f"{kind}-{ts}", "timestamp": ts}
def write(path, rows):
    with open(path, "w") as f:
        for r in rows: f.write(json.dumps(r, ensure_ascii=False) + "\n")
write(f"{cl}/{s5}.jsonl", [
    row(s5, "user", "2026-01-15T09:00:00Z", "Пиши Bob на bob@acme.example, ключ sk-abcdefghijklmnopqrstuvwx. Как быть с ночным бэкапом?"),
    row(s5, "assistant", "2026-01-15T09:01:00Z", "Бэкап падал, потому что диск переполнен; почистил старые снимки, теперь зелёный."),
    row(s5, "user", "2026-01-15T09:02:00Z", "Хорошо. Решаем так: бэкап храним четырнадцать дней, не больше."),
    row(s5, "assistant", "2026-01-15T09:03:00Z", "Принято, срок хранения четырнадцать дней."),
])
write(f"{cl}/{s6}.jsonl", [
    row(s6, "user", "2026-01-16T09:00:00Z", "Nightly digest run: собери сводку."),
    row(s6, "assistant", "2026-01-16T09:01:00Z", "Сводка собрана."),
    row(s6, "user", "2026-01-16T09:02:00Z", "Ещё раз."),
    row(s6, "assistant", "2026-01-16T09:03:00Z", "Готово."),
])
PY
cat > "$WORK/model.py" <<'PY'
# A made-up model: Codex-style JSONL events. Keeps the prompt so the test can see what was sent.
import json, os, sys
prompt = sys.stdin.read()
open(os.environ["PROMPT_LOG"], "a").write(prompt + "\n=====\n" + ("checker-command\n" if "--checker" in sys.argv else ""))
atoms = [
  {"kind": "decision", "claim": "Капибара-бэкап хранится четырнадцать дней.", "quote": "бэкап храним четырнадцать дней, не больше",
   "msg": 3, "entities": ["backup"], "confidence": 0.9},
  {"kind": "fix", "claim": "Ночной бэкап падал из-за диска, архивариус почистил снимки.",
   "quote": "Бэкап падал, потому что диск переполнен", "msg": 1, "entities": [], "confidence": 0.8},
  {"kind": "fact", "claim": "Придуманный факт.", "quote": "этой фразы в чате нет совсем", "msg": 2, "entities": [], "confidence": 0.9},
  {"kind": "bogus", "claim": "Неизвестный тип.", "quote": "Принято, срок хранения", "msg": 4},
  {"kind": "fact", "claim": "Срок хранения записан в DEV-999.", "quote": "Принято, срок хранения четырнадцать дней", "msg": 4},
  {"kind": "preference", "claim": "Агент любит короткие сроки.", "quote": "Принято, срок хранения четырнадцать дней", "msg": 4},
  {"kind": "fact", "claim": "Срок хранения согласовали с Bob.", "quote": "Принято, срок хранения четырнадцать дней", "msg": 4},
]
if "Weeks later he" in prompt:
    n = len([l for l in prompt.splitlines() if l[:1] == "[" and "]" in l[:5]])
    answer = {"questions": [{"i": i, "q": ["сколько держим резервные копии", "retention срок"]} for i in range(1, n + 1)]}
elif prompt.startswith("You check knowledge atoms"):
    # The checker: drops what Bob did not say, retypes nothing, keeps the rest.
    n = len([l for l in prompt.splitlines() if l[:1] == "[" and "]" in l[:5]])
    claims = [l.split(": ", 1)[1] for l in prompt.splitlines() if l[:1] == "[" and "]" in l[:5]]
    answer = {"verdicts": [{"i": i, "keep": "Bob" not in c, "claim": None, "kind": None, "why": "-"}
                           for i, c in enumerate(claims, 1)]}
else:
    answer = {"atoms": atoms}
print(json.dumps({"type": "thread.started"}))
print(json.dumps({"type": "item.completed", "item": {"type": "agent_message",
                  "text": "```json\n" + json.dumps(answer, ensure_ascii=False) + "\n```"}}))
print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 1000, "cached_input_tokens": 400, "output_tokens": 50}}))
PY
brain index >/dev/null 2>&1
brain atoms --source claude >/dev/null 2>&1; check "atoms without brain_model exits 3" "$?" 3
cat >> "$HQ/hq.yaml" <<EOF
brain_model: "python3 $WORK/model.py"
brain_atoms_skip:
  - "nightly digest run"
EOF
check "dry run lists the chat, skips the automation run by brain_atoms_skip" "$(brain atoms --source claude --dry-run | tail -n 1)" "1 chats would be distilled"
export PROMPT_LOG="$WORK/prompt.log"
out="$(brain atoms --source claude 2>&1)"; check "atoms exits 0" "$?" 0
check "checks: 2 kept, 5 dropped (no quote, unknown kind, id not in its message, agent's «preference», checker said no)" "$(printf '%s\n' "$out" | tail -n 1 | grep -o '1 chats, 2 kept, 5 dropped')" "1 chats, 2 kept, 5 dropped"
check "the checker is a second model call; tokens of both are counted" "$(printf '%s\n' "$out" | tail -n 1 | grep -o '2 model calls · tokens in 2000 (cached 800) out 100')" "2 model calls · tokens in 2000 (cached 800) out 100"
check "the checker sees each atom with the message its quote is from" "$(grep -c '  message (assistant, 2026-01-15): ' "$PROMPT_LOG")" 2
check "emails and keys are masked before either model call sees them" "$(grep -c 'bob@acme.example\|sk-abcdefghij' "$PROMPT_LOG") $(grep -c '<email>' "$PROMPT_LOG")" "0 3"
check "tool output never reaches the model" "$(grep -c zebracorn "$PROMPT_LOG")" 0
out="$(brain search "капибара" --json)"
check "an atom is searchable and points to its chat" "$(printf '%s' "$out" | field hits 0 key) $(printf '%s' "$out" | field hits 0 atom kind)" "$S5 decision"
check "…with the verbatim quote" "$(printf '%s' "$out" | field hits 0 atom quote)" "бэкап храним четырнадцать дней, не больше"
check "--no-atoms: raw units only" "$(keys "капибара" --no-atoms)" ""
check "an atom is dated by the message it quotes (--before)" "$(keys "капибара" --before 2026-01-15T09:01:30Z)" ""
check "a misnumbered quote is relinked to the message it is in" "$(brain search "архивариус" --json | field hits 0 atom kind)" "fix"
check "a second run distills nothing new" "$(brain atoms --source claude | tail -n 1 | grep -o '^atoms: 0 chats')" "atoms: 0 chats"
check "stats count atoms by kind" "$(brain stats --json | field atoms decision)/$(brain stats --json | field atom_runs dropped)" "1/5"
out="$(brain eval "$WORK/questions.yaml" --json)"
check "eval runs with atoms on and can turn them off" "$(printf '%s' "$out" | field atoms) $(brain eval "$WORK/questions.yaml" --no-atoms --json | field atoms)" "True False"
check "claims default to the chat's language" "$(grep -c "one self-contained sentence in the chat's language" "$PROMPT_LOG")" 1
check "the prompt tells the owner's words from relayed ones and the agent's self-corrections" "$(grep -c 'an agent correcting itself is a fix or a fact, never a correction' "$PROMPT_LOG")" 1
cid="$(brain atoms --source claude --redo --dry-run | head -n 1 | awk '{print $1}')"
printf 'brain_language: "Russian"\n' >> "$HQ/hq.yaml"
: > "$PROMPT_LOG"
check "--doc re-distills exactly that chat" "$(brain atoms --source claude --doc "$cid" | tail -n 1 | grep -o '^atoms: 1 chats, 2 kept')" "atoms: 1 chats, 2 kept"
printf 'brain_verify: false\n' >> "$HQ/hq.yaml"
check "brain_verify: false skips the checker (one call, its «Bob» atom kept)" "$(brain atoms --source claude --doc "$cid" | tail -n 1 | grep -o '1 chats, 3 kept.*1 model calls' | sed 's/,.*·//')" "1 chats 1 model calls"
sed -i '' '/^brain_verify: false$/d' "$HQ/hq.yaml"
printf 'brain_verify_model: "python3 %s/model.py --checker"\n' "$WORK" >> "$HQ/hq.yaml"
brain atoms --source claude --doc "$cid" >/dev/null
check "brain_verify_model runs the checker with its own command" "$(grep -c 'checker-command' "$PROMPT_LOG")" 1
sed -i '' '/^brain_verify_model:/d' "$HQ/hq.yaml"
check "brain_language sets the claims' language (the three runs above)" "$(grep -c 'one self-contained sentence in Russian' "$PROMPT_LOG")" 3
check "--doc with an unknown id distills nothing" "$(brain atoms --source claude --doc 999999999 | tail -n 1 | grep -o '^atoms: 0 chats')" "atoms: 0 chats"
printf 'brain_owner: "Ann"\nbrain_relay_markers:\n  - "Continue from"\n' >> "$HQ/hq.yaml"
: > "$PROMPT_LOG"
brain atoms --source claude --doc "$cid" >/dev/null
check "brain_owner names the owner in the prompt" "$(grep -c 'between the owner, Ann (user)' "$PROMPT_LOG")" 1
check "without a matching marker no message is relayed" "$(grep -c '] relayed ' "$PROMPT_LOG")" 0
check "an id or number the claim names must be in its message (a year is fine)" "$(cd "$HQ" && python3 -c 'import sys; sys.path.insert(0, "scripts"); import hq_brain as b; print(*b.unsupported_ids("MR !86 влит коммитом e1dd50e9 в 2026, тестов 2801", ["Пайплайн зелёный, вливаю MR !86. Тестов 2 801."], 1767225600))')" "e1dd50e9"
check "before questions, a word only a searcher would type finds no atom" "$(brain search "резервные копии" --json | field hits 0 atom kind 2>/dev/null)" ""
out="$(brain questions)"; check "questions exits 0 and covers every atom" "$(printf '%s' "$out" | grep -o '1 chats, 2 atoms got questions')" "1 chats, 2 atoms got questions"
check "an atom is found by its search question" "$(brain search "резервные копии" --json | field hits 0 atom kind)" "decision"
check "a second questions run asks nothing" "$(brain questions | grep -o '0 chats')" "0 chats"
check "parse_atoms takes the first JSON object when the model repeats it" "$(cd "$HQ" && python3 -c 'import sys; sys.path.insert(0, "scripts"); import hq_brain as b; print(len(b.parse_atoms("{\"atoms\":[{\"kind\":\"fact\"}]}{\"atoms\":[]}")))')" 1
check "a user message starting with a relay marker is shown as relayed" "$(cd "$HQ" && python3 -c 'import sys; sys.path.insert(0, "scripts"); import hq_brain as b; print(b.render_chat([{"n": 1, "role": "user", "ts": 0, "text": "Continue from x", "shown": "Continue from x"}, {"n": 2, "role": "user", "ts": 0, "text": "ok", "shown": "ok"}], ["Continue from"]).count("relayed"))')" 1

echo "== push: brief and hint =="
NOW=2026-01-20T00:00:00Z
check "brief: the project's decision, newest first" "$(brain brief --project alpha --now $NOW --json | field lines 0 claim)" "Капибара-бэкап хранится четырнадцать дней."
check "brief: --cwd finds the project from the chat's folder" "$(brain brief --cwd "$WORK/alpha/src" --now $NOW --json | field project)" "alpha"
check "brief: only the recent window" "$(brain brief --project alpha --now 2026-06-01T00:00:00Z)" ""
check "brief: a chat is not told its own atoms" "$(brain brief --project alpha --now $NOW --session "$S5")" ""
check "brief: text starts with the [brain] label" "$(brain brief --project alpha --now $NOW | head -n 1 | cut -c1-7)" "[brain]"
mkdir -p "$WORK/wt/alpha/feature-x"
printf 'brain_worktree_roots:\n  - "%s/wt"\n' "$WORK" >> "$HQ/hq.yaml"
check "a worktree under brain_worktree_roots counts for its repo's project" "$(brain brief --cwd "$WORK/wt/alpha/feature-x" --now $NOW --json | field project)" "alpha"
check "…and chats indexed under the worktree's name count too" "$(cd "$HQ" && python3 -c 'import sys; sys.path.insert(0, "scripts"); import hq_brain as b; print("feature-x" in b.project_names(".", "alpha"))')" "True"
hk() { brain hint "$1" --project alpha "${@:2}" --json | python3 -c 'import json,sys;print(" ".join(h["kind"] for h in json.load(sys.stdin)["hits"]))'; }
check "hint: everyday words alone bring nothing" "$(hk "сколько хранится бэкап")" ""
check "hint: one name is not enough for a plain message" "$(hk "сколько Капибара-бэкап хранится")" ""
printf 'brain_recall_cues:\n  - "мы же"\nbrain_hint_skip:\n  - "Nightly digest"\n' >> "$HQ/hq.yaml"
check "hint: a weak match stays out (brain_hint_min_score, default 25)" "$(hk "мы же решили, сколько Капибара-бэкап хранится?")" ""
printf 'brain_hint_min_score: 0\n' >> "$HQ/hq.yaml"   # a three-chat index scores low
check "hint: a message recalling the past gets the atom" "$(hk "мы же решили, сколько Капибара-бэкап хранится?")" "decision"
check "hint: not into the chat it came from" "$(hk "мы же решили, сколько Капибара-бэкап хранится?" --session "$S5")" ""
check "hint: not on an automation's prompt (brain_hint_skip)" "$(hk "Nightly digest: мы же решили, сколько Капибара-бэкап хранится?")" ""
check "hint: not on a diff" "$(hk "$(printf '+ мы же решили\n+ Капибара-бэкап хранится\n- old\n@@ -1 +1 @@')")" ""
check "hint: a recalling message with no atom points to the raw place it was said" "$(brain hint "мы же обсуждали Маяк lighthouse, когда проверим?" --project alpha --json | python3 -c 'import json,sys;h=json.load(sys.stdin)["hits"];print([(x["kind"], x["key"]) for x in h][:1])')" "[('said', '$S3')]"
ONCE="$WORK/once.txt"; LOG="$WORK/hints.jsonl"
check "hint --once: shown the first time…" "$(hk "мы же решили, сколько Капибара-бэкап хранится?" --once "$ONCE" --log "$LOG")" "decision"
check "…never twice in one chat" "$(hk "мы же решили, сколько Капибара-бэкап хранится?" --once "$ONCE" --log "$LOG")" ""
check "hint --log: one line per call with ms" "$(python3 -c 'import json,sys;r=[json.loads(l) for l in open(sys.argv[1])];print(len(r), all("ms" in x for x in r), len(r[0]["atoms"]))' "$LOG")" "2 True 1"
check "hint text: the label, then the atom with its date" "$(brain hint "мы же решили, сколько Капибара-бэкап хранится?" --project alpha | sed -n 2p)" "- 15.01 · decision: Капибара-бэкап хранится четырнадцать дней."

echo "== loops: owner's words, repeats, persona =="
S7=11111111-aaaa-4aaa-8aaa-000000000007; S8=11111111-aaaa-4aaa-8aaa-000000000008
python3 - "$CL" "$S7" "$S8" <<'PY'
import json, sys
cl, s7, s8 = sys.argv[1:]
def row(sid, kind, ts, text, **kw):
    content = text if kind == "user" else [{"type": "text", "text": text}]
    return {"parentUuid": None, "isSidechain": False, "type": kind, "sessionId": sid, "cwd": "/work/alpha",
            "message": {"role": kind, "content": content}, "uuid": f"{kind}-{ts}", "timestamp": ts, **kw}
def write(path, rows):
    with open(path, "w") as f:
        for r in rows: f.write(json.dumps(r, ensure_ascii=False) + "\n")
write(f"{cl}/{s7}.jsonl", [
    row(s7, "user", "2026-01-17T09:00:00Z", "Классифицируй: бэкап хранится четырнадцать дней, ответь JSON.", entrypoint="sdk-cli"),
    row(s7, "assistant", "2026-01-17T09:01:00Z", "{}", entrypoint="sdk-cli")])
write(f"{cl}/{s8}.jsonl", [
    row(s8, "user", "2026-01-18T09:00:00Z", "Слушай, мы же решили, сколько храним бэкап? Снова четырнадцать дней обсуждаем."),
    row(s8, "assistant", "2026-01-18T09:01:00Z", "Да, четырнадцать дней.")])
PY
brain index >/dev/null 2>&1
ukeys() { brain search "$@" --json | python3 -c 'import json,sys;print(" ".join(h["key"] for h in json.load(sys.stdin)["hits"]))'; }
check "a print-mode run's prompt (entrypoint sdk-cli) is not the owner's words" "$(ukeys "классифицируй" --role user)" ""
check "…but stays searchable" "$(ukeys "классифицируй" --limit 1)" "$S7"
check "an SDK run (sdk-ts, sdk-py) is a program's prompt too" "$(cd "$HQ" && python3 -c 'import sys; sys.path.insert(0, "scripts"); import hq_brain as b; print(sorted(b.PROGRAM_ENTRYPOINTS))')" "['sdk-cli', 'sdk-py', 'sdk-ts']"
check "the same message from two sources: the copy that says the owner typed it wins" "$(cd "$HQ" && python3 - "$WORK/dedupe.sqlite" <<'PY'
import sys, zlib; sys.path.insert(0, "scripts"); import hq_brain as b
db = b.connect(sys.argv[1], write=True)
db.execute("INSERT INTO docs(id, key, source) VALUES (1, 'chat', 'app'), (2, 'copy', 'claude')")
db.execute("UPDATE docs SET canon=1 WHERE id=2")
for uid, doc, role, text in ((10, 2, "relay", "Сделай отчёт по складу"), (11, 1, "user", "Сделай отчёт по складу"),
                             (12, 1, "assistant", "Готово")):
    db.execute("INSERT INTO units(id, doc, ts, role, text) VALUES (?,?,?,?,?)", (uid, doc, 100 + uid % 10, role, zlib.compress(text.encode())))
print(" ".join(m["role"] for m in b.chat_dialogue(db, 1)))
PY
)" "user assistant"
check "search --role user --since: the owner's own messages after a time" "$(ukeys "храним четырнадцать" --role user --since 2026-01-16)" "$S8"
check "search --json: spread over chats, days, projects" "$(brain search "храним четырнадцать" --role user --json | python3 -c 'import json,sys;print(json.load(sys.stdin)["spread"])')" "{'chats': 2, 'days': 2, 'projects': 1}"
out="$(brain repeats --since 2026-01-18 --until 2026-01-19 --json)"
check "repeats: the owner's message recalling the past" "$(printf '%s' "$out" | field count) $(printf '%s' "$out" | field repeats 0 key)" "1 $S8"
check "…with where it was said before" "$(printf '%s' "$out" | field repeats 0 earlier 0 key)" "$S5"
check "repeats: nothing on a day without such messages" "$(brain repeats --since 2026-01-17 --until 2026-01-18 --json | field count)" "0"
brain index --full >/dev/null 2>&1
check "atoms keep their message after a full re-index (relinked by quote)" "$(python3 -c 'import sqlite3,sys;db=sqlite3.connect(sys.argv[1]);print(db.execute("select count(*) from atoms a where a.unit is null or not exists(select 1 from units u where u.id=a.unit)").fetchone()[0])' "$WORK/index/brain.sqlite")" "0"
tl() { brain timeline "$@" --json | python3 -c 'import json,sys;d=json.load(sys.stdin);print(d["matched_by"], " ".join(sorted({r["key"] for r in d["rows"]})))'; }
check "timeline: a task id finds its chat" "$(tl DEV-12345)" "id chat-1"
check "timeline: a project lists its documents" "$(brain timeline alpha --json | python3 -c 'import json,sys;d=json.load(sys.stdin);print(d["matched_by"], len(d["rows"]) > 2)')" "project True"
check "timeline: a name finds where it is said" "$(tl Ann)" "words chat-1"
check "timeline: a program's run is left out" "$(tl классифицируй)" "words "
check "persona: three sections" "$(brain persona --json | python3 -c 'import json,sys;print(" ".join(s["kind"] for s in json.load(sys.stdin)))')" "preference correction person"
brain persona --out "$WORK/persona.md" >/dev/null
check "persona --out writes the portrait" "$(head -n 1 "$WORK/persona.md" | cut -c1-2)" "# "

echo "== config errors =="
printf 'brain:\n  - name: "nokind"\n' > "$WORK/bad.yaml"; mkdir -p "$WORK/bad"; cp "$WORK/bad.yaml" "$WORK/bad/hq.yaml"
python3 "$HQ/scripts/hq_brain.py" --hq "$WORK/bad" index >/dev/null 2>&1; check "a source without kind exits 3" "$?" 3
rm -f "$WORK/bad/hq.yaml"; printf 'source: "paths"\n' > "$WORK/bad/hq.yaml"
python3 "$HQ/scripts/hq_brain.py" --hq "$WORK/bad" search "x" >/dev/null 2>&1; check "search without an index exits 3" "$?" 3

echo
echo "hq_brain: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
