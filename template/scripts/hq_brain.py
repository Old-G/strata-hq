#!/usr/bin/env python3
"""HQ's brain, index layer: one local full-text index over agent chats, notes and wikis. No model.

Sources are declared in hq.yaml (a list under `brain:`, flat mappings, read by hq_registry's YAML
subset); the index lives at `brain_index:` (default ~/.local/share/strata-hq/<HQ folder>/brain.sqlite,
outside git). Built-in source kinds:
  claude-transcripts  path: live Claude Code projects dir (<project>/<session>.jsonl), archive: a
                      dir with the same layout of *.jsonl.zst; the newer copy of each session wins.
                      User and assistant text only — tool calls, tool output, thinking, hook and
                      system boilerplate are dropped.
  codex-sessions      path: Codex sessions dir (**/rollout-*.jsonl), user and assistant text.
  markdown            path: a folder (relative to HQ or absolute), or `registry: true` with
                      dirs: ["wiki"] — those folders of every local project in registry.yaml.
                      Git-tracked *.md only when inside git; unit = a section (heading + text);
                      its time is when its oldest line was committed (git blame).
  command             run: a command (cwd = HQ) printing JSONL, one unit per line:
                      {id, text, ts, doc?, title?, project?, link?, kind?, role?, source?, aliases?}
                      role: user (the owner typed it) · assistant · relay (user-role text someone or
                      something else wrote — a handoff, a launch prompt: atoms see it as relayed)
                      id is stable; doc groups units (default = id); aliases are other keys of
                      that doc (e.g. a provider's session id); kind: chatter | error | decision |
                      knowledge. BRAIN_SINCE (epoch seconds, 0 = everything) says what the last
                      successful run already saw; printing everything again is fine — unchanged
                      rows are skipped by content hash.
Optional per source: name (label, default = kind), group (repeats collapse within a group),
weight (multiplier), project, exclude (path prefixes, markdown).

Index: unit = one message, one markdown section, one command row (long ones split ~2500 chars).
FTS5 with two columns — Snowball-stemmed words (Russian + English, hq_stem.py) and raw ids
(DEV-123, !19, file names, snake_case, paths) unstemmed. A paragraph seen earlier in another
document of the same group is a repeat: dropped from the later unit, and a unit that is mostly
repeats keeps a pointer to the first document and a fraction of the score. A chat whose opening
prompt is mostly a repeat (an automation run) weighs less.

Search: stems of the words minus stopwords and filler, ids matched exactly, OR + BM25. Score =
BM25 × type weight × decay(age, type) — chatter halves every 60 days, errors every 180, decisions
and knowledge never; age counts back from --before (or now). Units are grouped by document (a chat
session with its aliases, a file, a row) and the top documents come back.

Atoms (layer 3): a model reads one chat's dialogue (owner and agent text, no tool output, masked,
cut to `brain_atoms_budget` chars) and returns typed claims — decision · fact · fix (error→fix) ·
preference · question (open) · person (fact about a person) · correction (the owner corrected the
agent) — each with a verbatim quote. An atom whose quote is not in the input is dropped. Atoms live
in the same index (table atoms + atoms_fts) and `search` ranks them with the raw units: an atom hit
counts for its source chat and points to the message it quotes. The model is a command in hq.yaml,
`brain_model:` — the prompt goes to its stdin; it prints the answer as text, as JSONL events (the last
`agent_message`, usage from `turn.completed`) or as one JSON object with `result` and `usage`.
`brain_atoms_skip:` — title/first-prompt substrings of chats not worth distilling (automation runs).
`brain_language:` — the language atoms' claims are written in (default: the chat's own language).
`brain_verify_model:` — the checker's command (default: `brain_model`; a stronger model catches more).
`brain_verify:` (default true) — a second model call checks every atom against its message and keeps only
those it confirms; ids and numbers a claim names must be in its message, and a preference or correction must
be the owner's own words, verified or not. `brain_owner:` — the owner's name, so claims say who decided. `brain_relay_markers:` — prefixes of user messages
written by a script, another agent or another person (handoffs, relayed replies); the model sees them as relayed.

Push (layer 4, for hooks): `brief` — what a new chat of a project should know: recent decisions, the owner's
corrections and preferences, open questions, error→fix (a SessionStart hook prints it). `hint` — for one
message, at most 3 atoms close enough to put in front of the agent as background (a UserPromptSubmit hook):
an atom must share rare words with the message, some of them specific (an id with a digit, a name written
with a capital letter, a word rare in the raw chats). A message that recalls the past — `brain_recall_cues:`,
regexes in the owner's language — needs fewer. Below `brain_hint_min_score:` (default 25) only a shared
specific id keeps an atom. `brain_hint_skip:` and `brain_relay_markers:` mark messages
the owner did not type; diffs and code are skipped too. `brain_worktree_roots:` — folders laid out as
<root>/<repo folder>/<worktree>: a chat there counts for the repo's project.

Loops (layer 5): `repeats` — the owner's own messages in a period that recall the past (`brain_repeat_cues:`,
stricter than the hint's; default `brain_recall_cues:`),
each with where the index finds it earlier and whether the hint hook showed anything (its --log); the daily
and weekly reviews turn them into rules with evidence. `persona` — the owner's portrait from atoms
(preferences, corrections, people), near-repeats once, for a new account or another agent. `search --role
user` keeps only the owner's own typing (no relayed or launch prompts, no automation prompts) — evidence for a rule.

Usage:
  python3 scripts/hq_brain.py index  [--hq DIR] [--full] [--source NAME]
  python3 scripts/hq_brain.py questions [--limit N] [--jobs ≤3] [--redo] [--doc ID]
  python3 scripts/hq_brain.py search "<text>" [--before ISO] [--project P] [--limit 8] [--json] [--no-atoms]
  python3 scripts/hq_brain.py atoms  [--limit 20] [--source NAME ...] [--jobs 1] [--dry-run] [--redo]
  python3 scripts/hq_brain.py eval   questions.yaml [--limit 8] [--no-before] [--json] [--no-atoms]
  python3 scripts/hq_brain.py stats  [--json]
  python3 scripts/hq_brain.py brief  --project P | --cwd DIR [--session ID ...] [--lines 24] [--json]
  python3 scripts/hq_brain.py repeats [--since ISO] [--until ISO] [--log FILE] [--json]
  python3 scripts/hq_brain.py persona [--out FILE] [--limit 40] [--json]
  python3 scripts/hq_brain.py hint   "<message>" --project P | --cwd DIR [--session ID ...] [--once FILE] [--log FILE] [--json]
Exit: 0 ok · 2 a source failed (the rest was indexed) · 3 bad hq.yaml or no index yet.
"""
import argparse
import datetime as dt
import hashlib
import json
import math
import os
import re
import shlex
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import time
import zlib
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hq_registry import read_yaml_subset  # noqa: E402  (same folder)
from hq_stem import stem, stem_en, translit, words  # noqa: E402

VERSION = "0.8.0"
SCHEMA = 2
CHUNK = 2500            # chars per unit; longer messages and sections are split at paragraphs
MIN_PARA = 60           # a paragraph shorter than this (normalised) is never a "repeat"
REPEAT_SHARE = 0.8      # a unit this much repeated keeps only DUP_WEIGHT of its score
DUP_WEIGHT = 0.15
AUTO_SHARE = 0.5        # a chat whose first prompt is this much repeated is an automation run
AUTO_WEIGHT = 0.4
POOL = 400              # candidate units re-ranked per query
COMMON_DF = 0.15        # a query word in more than this share of units is dropped (when others remain)
COLS = (1.0, 3.0)       # bm25 weights: stemmed text, ids
TYPE_WEIGHT = {"chatter": 1.0, "error": 1.0, "decision": 1.3, "knowledge": 1.0}
HALF_LIFE = {"chatter": 60.0, "error": 180.0, "decision": None, "knowledge": None}
ERROR_RE = re.compile(r"ошибк|падает|упал[аио]?\b|сломал|\bбаг|traceback|exception|\berror|\bfail|исправ|починил|\bfix",
                      re.I)

STOP = frozenset("""
и в во не что он на я с со как а то все всё она так его но да ты к у же вы за бы по только ее её мне было вот от
меня еще ещё нет о из ему теперь когда даже ну вдруг ли если уже или ни быть был него до вас нибудь опять уж вам
ведь там потом себя ничего ей может они тут где есть надо ней для мы тебя их чем была сам чтоб без будто чего раз
тоже себе под будет ж тогда кто этот того потому этого какой совсем ним здесь этом один почти мой тем чтобы нее
неё сейчас были куда зачем всех никогда можно при наконец два об другой хоть после над больше тот через эти нас
про всего них какая много разве три эту моя впрочем хорошо свою этой перед иногда лучше чуть том нельзя такой им
более всегда конечно всю между это эта то те та
a an the and or but if then else of to in on at by for with from is are was were be been being it its this that
these those i you he she we they me him her us them my your our their do does did have has had not no so as can
will would should could there here what which who whom when where why how all any both each few more most other
some such only own same than too very just also into about over again once up down out off s t re ll ve d m
""".split())
FILLER = frozenset("""
слушай слушайте вроде кажется помнишь наверное вообще короче типа типо щас поищи посмотри глянь найди проверь
скажи подскажи расскажи напомни мол как-то какой-то какие-то что-то где-то когда-то кто-то пожалуйста плиз
please hey ok okay pls
""".split())

ID_RE = re.compile(r"(?<![\w!#])[!#]\d+\b|[A-Za-z0-9][\w.\-/]*[\w]")
TAGS = ("system-reminder", "command-name", "command-message", "local-command-stdout", "local-command-stderr",
        "local-command-caveat", "bash-input", "bash-stdout", "bash-stderr", "task-notification",
        "user-prompt-submit-hook", "ide_opened_file", "ide_selection", "environment_context",
        "user_instructions", "INSTRUCTIONS", "permissions instructions", "turn_aborted")
TAG_RE = re.compile(r"<(%s)\b[^>]*>.*?</\1>" % "|".join(re.escape(t) for t in TAGS), re.S)
ARGS_RE = re.compile(r"</?command-args>")
NOISE_LINE = re.compile(r"^(Caveat: The messages below were generated by the user while running local commands.*|"
                        r"\[Request interrupted by user[^\]]*\]|# AGENTS\.md instructions for .*)$", re.M)


# ---------------------------------------------------------------- small helpers

SLOTS = 1 << 20


def rowid_floor(ts):
    return int(ts) * SLOTS


def h64(text):
    return int.from_bytes(hashlib.blake2b(text.encode("utf-8", "surrogatepass"), digest_size=8).digest(),
                          "big", signed=True)


def parse_ts(value):
    """ISO 8601 / epoch seconds / epoch ms → epoch seconds (float), or None."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        v = float(value)
        return v / 1000.0 if v > 1e11 else v
    s = str(value).strip()
    if re.fullmatch(r"\d+(\.\d+)?", s):
        return parse_ts(float(s))
    try:
        d = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.astimezone()
    return d.timestamp()


def iso(ts):
    if ts is None:
        return None
    return dt.datetime.fromtimestamp(ts).astimezone().isoformat(timespec="seconds")


def expand(path, hq):
    if not path:
        return None
    path = os.path.expanduser(str(path))
    return path if os.path.isabs(path) else os.path.normpath(os.path.join(hq, path))


def open_text(path):
    """Lines of a text file, or of a .zst (compression.zstd when present, else the zstd CLI)."""
    if not path.endswith(".zst"):
        return open(path, encoding="utf-8", errors="replace")
    try:
        from compression import zstd  # Python 3.14+
        return zstd.open(path, "rt", encoding="utf-8", errors="replace")
    except ImportError:
        out = subprocess.run(["zstd", "-dcq", path], capture_output=True, check=True).stdout
        return out.decode("utf-8", "replace").splitlines(True)


def clean_chat(text):
    """Drop hook/system boilerplate from a chat message; keep slash-command arguments."""
    if not text:
        return ""
    text = TAG_RE.sub("", text)
    text = ARGS_RE.sub("", text)
    text = NOISE_LINE.sub("", text)
    return text.strip()


def extract_ids(text, cap=300):
    """Identifier-like tokens, normalised: DEV-123 → dev_123, !19 → bang_19, a/b-c.md → a_b_c_md + b_c_md."""
    out = []
    seen = set()

    def put(tok):
        t = re.sub(r"[^0-9a-zа-яё]+", "_", tok.lower()).strip("_")
        if len(t) >= 3 and t not in seen and len(t) <= 80:
            seen.add(t)
            out.append(t)

    for m in ID_RE.finditer(text):
        tok = m.group(0)
        if tok[0] in "!#":
            put(("bang_" if tok[0] == "!" else "num_") + tok[1:])
            continue
        parts = [p for p in tok.split("/") if p]
        whole = len(parts) > 1
        for i, p in enumerate(parts):
            idish = re.search(r"[._\-]", p.strip("._-")) or (re.search(r"\d", p) and re.search(r"[A-Za-z]", p))
            if idish or (whole and i == len(parts) - 1 and "." in p) or (len(p) >= 5 and p.isdigit() and whole):
                put(p)
        if whole:
            put(tok)
        if len(out) >= cap:
            break
    return out


def stem_text(text):
    return " ".join(stem(t) for t in words(text) if t not in STOP and len(t) <= 40)


def chunks(text, size=CHUNK):
    """Split at blank lines (then lines, then hard) into pieces of at most ~size chars."""
    if len(text) <= size:
        return [text]
    out, cur = [], ""
    for para in re.split(r"(\n\s*\n)", text):
        if len(cur) + len(para) <= size:
            cur += para
            continue
        if cur.strip():
            out.append(cur)
        cur = ""
        while len(para) > size:
            cut = para.rfind("\n", 0, size)
            cut = cut if cut > size // 3 else size
            out.append(para[:cut])
            para = para[cut:]
        cur = para
    if cur.strip():
        out.append(cur)
    return out


# ---------------------------------------------------------------- tiny YAML reader (eval sets)

def _yaml_scalar(s):
    s = s.strip()
    if s == "":
        return None
    if s[0] == '"':
        return json.loads(s[:s.rindex('"') + 1])
    if s[0] == "'":
        return s[1:s.rindex("'")].replace("''", "'")
    s = s.split(" #")[0].strip()
    if s in ("true", "false"):
        return s == "true"
    if s in ("null", "~"):
        return None
    for cast in (int, float):
        try:
            return cast(s)
        except ValueError:
            pass
    return s


def parse_yaml(text):
    """Block mappings, block lists (also `- key: value` items), one-line scalars. No anchors or `|`."""
    toks = []
    for n, raw in enumerate(text.splitlines(), 1):
        if raw.strip() and not raw.lstrip().startswith("#"):
            toks.append([len(raw) - len(raw.lstrip(" ")), raw.strip(), n])
    pos = 0
    key_re = re.compile(r"^([A-Za-z_][\w.-]*):(?:\s+(.*))?$")

    def is_item(t):
        return t[1] == "-" or t[1].startswith("- ")

    def block(indent):
        nonlocal pos
        if is_item(toks[pos]):
            out = []
            while pos < len(toks) and toks[pos][0] == indent and is_item(toks[pos]):
                rest = toks[pos][1][1:].strip()
                if not rest:
                    pos += 1
                    out.append(block(toks[pos][0]))
                elif key_re.match(rest):
                    toks[pos] = [indent + 2, rest, toks[pos][2]]
                    out.append(block(indent + 2))
                else:
                    out.append(_yaml_scalar(rest))
                    pos += 1
            return out
        out = {}
        while pos < len(toks) and toks[pos][0] == indent and not is_item(toks[pos]):
            m = key_re.match(toks[pos][1])
            if not m:
                raise ValueError(f"line {toks[pos][2]}: cannot read {toks[pos][1][:60]!r}")
            key, val = m.group(1), m.group(2)
            pos += 1
            if val in (None, ""):
                nxt = toks[pos] if pos < len(toks) else None
                if nxt and (nxt[0] > indent or (nxt[0] == indent and is_item(nxt))):
                    out[key] = block(nxt[0])
                else:
                    out[key] = None
            elif val in ("|", ">", "|-", ">-"):
                raise ValueError(f"line {toks[pos - 1][2]}: block scalars are not supported")
            else:
                out[key] = _yaml_scalar(val)
        return out

    return block(toks[0][0]) if toks else {}


# ---------------------------------------------------------------- config

def load_config(hq):
    cfg = read_yaml_subset(os.path.join(hq, "hq.yaml"))
    sources = []
    for i, s in enumerate(cfg.get("brain") or []):
        if not isinstance(s, dict) or not s.get("kind"):
            raise ValueError(f"hq.yaml brain[{i}]: each source needs a kind")
        s = dict(s)
        s.setdefault("name", s["kind"])
        s.setdefault("group", s["name"])
        sources.append(s)
    names = [s["name"] for s in sources]
    if len(names) != len(set(names)):
        raise ValueError("hq.yaml brain: source names must be unique (set name:)")
    default = os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"),
                           "strata-hq", os.path.basename(os.path.abspath(hq)), "brain.sqlite")
    return sources, expand(cfg.get("brain_index"), hq) or default


def read_registry(hq):
    reg = read_yaml_subset(os.path.join(hq, "registry.yaml"))
    return [p for p in reg.get("projects") or [] if isinstance(p, dict) and p.get("path")]


def worktree_roots(hq):
    """`brain_worktree_roots:` — folders laid out as <root>/<repo folder>/<worktree>; such a chat is the repo's."""
    cfg = read_yaml_subset(os.path.join(hq, "hq.yaml"))
    return [os.path.expanduser(str(r)).rstrip("/") for r in as_list(cfg.get("brain_worktree_roots"))]


def project_of_path(registry, roots, value):
    """A path → the registry slug of the longest matching project path, or of the repo a worktree belongs
    to, else the path's basename."""
    path = os.path.expanduser(str(value)).rstrip("/")
    best = None
    for p in registry:
        root = os.path.expanduser(p["path"]).rstrip("/")
        if (path == root or path.startswith(root + "/")) and (best is None or len(root) > len(best[0])):
            best = (root, p.get("slug") or os.path.basename(root))
    if best:
        return best[1]
    for wt in roots:
        if path.startswith(wt + "/"):
            repo = path[len(wt) + 1:].split("/")[0]
            for p in registry:
                if repo in (os.path.basename(os.path.expanduser(p["path"]).rstrip("/")), p.get("slug")):
                    return p.get("slug") or repo
            return repo
    return os.path.basename(path)


def project_names(hq, slug):
    """The names a project's chats were indexed under: its slug, its folder name and, under each worktree
    root, the folders of its worktrees (chats indexed before the roots were set carry the worktree name)."""
    reg = {p.get("slug"): os.path.basename(os.path.expanduser(p["path"]).rstrip("/")) for p in read_registry(hq)}
    folder = reg.get(slug, slug)
    names = {slug, folder}
    for wt in worktree_roots(hq):
        try:
            names.update(e.name for e in os.scandir(os.path.join(wt, folder)) if e.is_dir())
        except OSError:
            pass
    return names


# ---------------------------------------------------------------- the index

DDL = """
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS docs(id INTEGER PRIMARY KEY, key TEXT UNIQUE NOT NULL, canon INTEGER, source TEXT,
  project TEXT, title TEXT, link TEXT, weight REAL NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS units(id INTEGER PRIMARY KEY, doc INTEGER NOT NULL, file INTEGER, msg INTEGER,
  ts REAL, role TEXT, kind TEXT, weight REAL, dup INTEGER, chash INTEGER, text BLOB);
CREATE INDEX IF NOT EXISTS units_doc ON units(doc);
CREATE INDEX IF NOT EXISTS units_file ON units(file) WHERE file IS NOT NULL;
CREATE INDEX IF NOT EXISTS units_msg ON units(msg) WHERE msg IS NOT NULL;
CREATE TABLE IF NOT EXISTS files(id INTEGER PRIMARY KEY, source TEXT NOT NULL, path TEXT NOT NULL, stamp TEXT,
  UNIQUE(source, path));
CREATE TABLE IF NOT EXISTS paras(h INTEGER PRIMARY KEY, ts INTEGER, doc INTEGER);
CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(stem, ids, content='', contentless_delete=1,
  tokenize="unicode61 remove_diacritics 0 tokenchars '_'");
CREATE VIRTUAL TABLE IF NOT EXISTS fts_v USING fts5vocab(fts, 'row');
CREATE TABLE IF NOT EXISTS atoms(id INTEGER PRIMARY KEY, doc INTEGER NOT NULL, unit INTEGER, kind TEXT,
  claim TEXT, quote TEXT, entities TEXT, ts REAL, confidence REAL, superseded_by INTEGER, model TEXT, made REAL);
CREATE INDEX IF NOT EXISTS atoms_doc ON atoms(doc);
CREATE TABLE IF NOT EXISTS atom_runs(doc INTEGER PRIMARY KEY, at REAL, model TEXT, status TEXT, calls INTEGER,
  chars INTEGER, in_tokens INTEGER, cached_tokens INTEGER, out_tokens INTEGER, secs REAL, kept INTEGER,
  dropped INTEGER, error TEXT);
CREATE VIRTUAL TABLE IF NOT EXISTS atoms_fts USING fts5(stem, ids, content='', contentless_delete=1,
  tokenize="unicode61 remove_diacritics 0 tokenchars '_'");
CREATE VIRTUAL TABLE IF NOT EXISTS atoms_v USING fts5vocab(atoms_fts, 'row');
CREATE TABLE IF NOT EXISTS atom_questions(atom INTEGER PRIMARY KEY, questions TEXT, model TEXT, made REAL);
"""


def connect(path, write=False):
    if write:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        db = sqlite3.connect(path)
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=NORMAL")
        db.executescript(DDL)
        db.execute("INSERT OR IGNORE INTO meta VALUES ('schema', ?)", (str(SCHEMA),))
        found = db.execute("SELECT v FROM meta WHERE k='schema'").fetchone()[0]
        if found != str(SCHEMA):
            raise ValueError(f"{path} has index schema {found}, this script writes {SCHEMA}: delete it and index again")
        return db
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


class Indexer:
    def __init__(self, db, hq, registry):
        self.db, self.hq, self.registry = db, hq, registry
        self.roots = worktree_roots(hq)
        self.doc_cache = {}
        self.added = 0
        self.pending = 0

    # -- documents
    def doc(self, key, source, project=None, title=None, link=None, fill_only=False):
        """The doc id for `key`. A file source owns its doc's fields; a command row only fills gaps."""
        did = self.doc_cache.get(key)
        if did is None:
            row = self.db.execute("SELECT id FROM docs WHERE key=?", (key,)).fetchone()
            if row:
                did = row[0]
            else:
                did = self.db.execute("INSERT INTO docs(key, source) VALUES (?, ?)", (key, source)).lastrowid
            self.doc_cache[key] = did
        if project or title or link:
            a, b = ("{0}", "?") if fill_only else ("?", "{0}")
            sets = ", ".join(f"{c}=COALESCE({a.format(c)}, {b.format(c)})" for c in ("project", "title", "link"))
            self.db.execute(f"UPDATE docs SET {sets} WHERE id=?", (self.project(project), title, link, did))
        return did

    def alias(self, alias_key, did, source):
        if not alias_key:
            return
        aid = self.doc(str(alias_key), source)
        if aid != did:
            self.db.execute("UPDATE docs SET canon=? WHERE id=?", (did, aid))

    def project(self, value):
        if not value:
            return None
        value = str(value)
        if not value.startswith(("/", "~")):
            return value
        return project_of_path(self.registry, self.roots, value)

    # -- files (incremental unit of the file-based sources)
    def file_state(self, source, path):
        return self.db.execute("SELECT id, stamp FROM files WHERE source=? AND path=?", (source, path)).fetchone()

    def file_begin(self, source, path, stamp):
        row = self.file_state(source, path)
        if row:
            self.drop("file", row[0])
            self.db.execute("UPDATE files SET stamp=? WHERE id=?", (stamp, row[0]))
            return row[0]
        return self.db.execute("INSERT INTO files(source, path, stamp) VALUES (?,?,?)", (source, path, stamp)).lastrowid

    def drop(self, column, value):
        ids = [r[0] for r in self.db.execute(f"SELECT id FROM units WHERE {column}=?", (value,))]
        for uid in ids:
            self.db.execute("DELETE FROM fts WHERE rowid=?", (uid,))
        self.db.execute(f"DELETE FROM units WHERE {column}=?", (value,))
        return len(ids)

    # -- units
    def _rowid(self, ts):
        """Rowids grow with time (second << 20 | n), so --before is a rowid range FTS5 cuts natively."""
        base = rowid_floor(ts)
        top = self.db.execute("SELECT max(id) FROM units WHERE id >= ? AND id < ?", (base, base + SLOTS)).fetchone()[0]
        if top is not None and top + 1 >= base + SLOTS:
            raise RuntimeError("more than a million units in one second")
        return base if top is None else top + 1

    def add(self, src, did, ts, text, role="", kind=None, weight=1.0, file_id=None, msg=None, chash=None):
        """Index one message/section/row. Returns the share of its text that repeats an earlier document."""
        if not text or not text.strip() or ts is None:
            return 0.0
        group = src["group"]
        novel, rep, total, first = [], 0, 0, None
        for para in re.split(r"\n\s*\n", text):
            if not para.strip():
                continue
            total += len(para)
            norm = " ".join(words(para))
            if len(norm) >= MIN_PARA:
                h = h64(group + "\0" + norm)
                row = self.db.execute("SELECT ts, doc FROM paras WHERE h=?", (h,)).fetchone()
                if row is None:
                    self.db.execute("INSERT INTO paras VALUES (?,?,?)", (h, int(ts), did))
                elif row[1] != did and row[0] <= ts:
                    rep += len(para)
                    first = first or row[1]
                    continue
                elif row[1] != did:
                    self.db.execute("UPDATE paras SET ts=?, doc=? WHERE h=?", (int(ts), did, h))
            novel.append(para)
        share = rep / total if total else 0.0
        dup = None
        if share >= REPEAT_SHARE or not novel:
            dup, weight, body = first, weight * DUP_WEIGHT, text
        else:
            body = "\n\n".join(novel)
        kind = kind if kind in TYPE_WEIGHT else ("error" if ERROR_RE.search(body) else "chatter")
        w = weight * float(src.get("weight") or 1.0)
        for piece in chunks(body):
            s, ids = stem_text(piece), " ".join(extract_ids(piece))
            if not s and not ids:
                continue
            uid = self._rowid(ts)
            self.db.execute("INSERT INTO units(id, doc, file, msg, ts, role, kind, weight, dup, chash, text) "
                            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                            (uid, did, file_id, msg, ts, role, kind, w, dup, chash,
                             zlib.compress(piece.encode("utf-8", "replace"), 6)))
            self.db.execute("INSERT INTO fts(rowid, stem, ids) VALUES (?,?,?)", (uid, s, ids))
            self.added += 1
            self.pending += 1
        if self.pending >= 20000:
            self.db.commit()
            self.pending = 0
        return share


# ---------------------------------------------------------------- sources

def index_claude(ix, src, full):
    live, arch = expand(src.get("path"), ix.hq), expand(src.get("archive"), ix.hq)
    found = {}
    for root, suffix in ((arch, ".jsonl.zst"), (live, ".jsonl")):
        if not root or not os.path.isdir(root):
            continue
        for proj in os.scandir(root):
            if not proj.is_dir():
                continue
            for f in os.scandir(proj.path):
                if f.name.endswith(suffix) and f.is_file():
                    rel = f"{proj.name}/{f.name[:-len(suffix)]}"
                    mtime = int(f.stat().st_mtime)
                    if rel not in found or mtime >= found[rel][1]:
                        found[rel] = (f.path, mtime)
    todo = []
    for rel, (path, mtime) in found.items():
        state = ix.file_state(src["name"], rel)
        if full or not state or state[1] != str(mtime):
            todo.append((first_ts(path) or mtime, rel, path, mtime))
    # oldest chat first, so a pasted block's first occurrence is the original, not the copy
    for n, (_, rel, path, mtime) in enumerate(sorted(todo), 1):
        fid = ix.file_begin(src["name"], rel, str(mtime))
        read_claude(ix, src, path, os.path.basename(rel), fid)
        if n % 2000 == 0:
            print(f"    {src['name']}: {n}/{len(todo)} files, {ix.added} units", file=sys.stderr, flush=True)
    return len(todo), len(found)


def first_ts(path, scan=40):
    """Time of the first timestamped line of a JSONL chat (its start), or None."""
    try:
        for i, line in enumerate(open_text(path)):
            m = re.search(r'"timestamp":\s*"([^"]+)"', line)
            if m:
                return parse_ts(m.group(1))
            if i >= scan:
                break
    except (OSError, ValueError, subprocess.CalledProcessError):
        pass
    return None


def read_claude(ix, src, path, session, fid):
    msgs, title, first_user, cwd = [], None, None, None
    try:
        lines = open_text(path)
        for line in lines:
            head = line[:40].replace(" ", "")
            if head.startswith('{"type":"') and not head.startswith(('{"type":"user"', '{"type":"assistant"')):
                # metadata rows; only titles matter. A message may also put "type" first
                if head.startswith(('{"type":"ai-title"', '{"type":"summary"')):
                    d = json.loads(line)
                    title = d.get("aiTitle") or d.get("summary") or title
                continue
            if '"text"' not in line and '"content":"' not in line and '"content": "' not in line:
                continue
            if '"tool_use_id"' in line:          # tool results
                continue
            d = json.loads(line)
            kind = d.get("type")
            if kind not in ("user", "assistant") or d.get("isMeta") or d.get("isSidechain") \
                    or d.get("isCompactSummary") or d.get("toolUseResult"):
                continue
            cwd = cwd or d.get("cwd")
            if kind == "user" and d.get("entrypoint") == "sdk-cli":
                kind = "relay"                   # a print-mode run: the prompt is a program's, not the owner's
            content = (d.get("message") or {}).get("content")
            if isinstance(content, str):
                text = content
            elif isinstance(content, list):
                text = "\n\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
            else:
                continue
            text = clean_chat(text)
            ts = parse_ts(d.get("timestamp"))
            if text and ts:
                msgs.append((ts, kind, text))
                if kind in ("user", "relay") and first_user is None:
                    first_user = text
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"  ! {path}: {exc}", file=sys.stderr)
    if not msgs:
        return
    title = title or (first_user or msgs[0][2]).strip().splitlines()[0][:100]
    did = ix.doc(session, src["name"], project=cwd or src.get("project"), title=title, link=path)
    auto = None
    for ts, role, text in msgs:
        share = ix.add(src, did, ts, text, role=role, file_id=fid)
        if auto is None and role in ("user", "relay"):
            auto = share >= AUTO_SHARE
    ix.db.execute("UPDATE docs SET weight=? WHERE id=?", (AUTO_WEIGHT if auto else 1.0, did))


def index_codex(ix, src, full):
    root = expand(src.get("path"), ix.hq)
    found = []
    for base, _, names in os.walk(root or ""):
        for n in names:
            if n.endswith(".jsonl"):
                p = os.path.join(base, n)
                found.append((n, os.path.getmtime(p), p))   # rollout-<start time>-… sorts by start
    changed = 0
    for _, mtime, path in sorted(found):
        rel = os.path.relpath(path, root)
        stamp = str(int(mtime))
        state = ix.file_state(src["name"], rel)
        if state and state[1] == stamp and not full:
            continue
        fid = ix.file_begin(src["name"], rel, stamp)
        read_codex(ix, src, path, fid)
        changed += 1
    return changed, len(found)


def read_codex(ix, src, path, fid):
    session, cwd, msgs, programmatic = None, None, [], False
    try:
        for line in open_text(path):
            if '"session_meta"' in line[:160]:
                p = json.loads(line).get("payload") or {}
                session, cwd = session or p.get("id"), cwd or p.get("cwd")
                programmatic = programmatic or p.get("originator") == "codex_exec"   # `codex exec`: a program's prompt
                continue
            if '"response_item"' not in line or '"message"' not in line:
                continue
            d = json.loads(line)
            p = d.get("payload") or {}
            if p.get("type") != "message" or p.get("role") not in ("user", "assistant"):
                continue
            text = "\n\n".join(b.get("text", "") for b in p.get("content") or []
                               if isinstance(b, dict) and b.get("type") in ("input_text", "output_text"))
            if text.lstrip().startswith(("<", "# AGENTS.md")):   # injected instructions / environment
                continue
            text = clean_chat(text)
            ts = parse_ts(d.get("timestamp"))
            if text and ts:
                msgs.append((ts, p["role"], text))
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"  ! {path}: {exc}", file=sys.stderr)
    if not msgs:
        return
    if programmatic:
        msgs = [(ts, "relay" if role == "user" else role, text) for ts, role, text in msgs]
    session = session or os.path.basename(path)[:-6]
    first = next((t for _, r, t in msgs if r in ("user", "relay")), msgs[0][2])
    did = ix.doc(session, src["name"], project=cwd or src.get("project"),
                 title=first.strip().splitlines()[0][:100], link=path)
    auto = None
    for ts, role, text in msgs:
        share = ix.add(src, did, ts, text, role=role, file_id=fid)
        if auto is None and role in ("user", "relay"):
            auto = share >= AUTO_SHARE
    ix.db.execute("UPDATE docs SET weight=? WHERE id=?", (AUTO_WEIGHT if auto else 1.0, did))


def git_out(repo, *args):
    try:
        p = subprocess.run(["git", "-C", repo, *args], capture_output=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return p.stdout.decode("utf-8", "replace") if p.returncode == 0 else None


def blame_times(repo, rel):
    """Commit time of every line (uncommitted lines → None), or None when not in git."""
    out = git_out(repo, "blame", "--line-porcelain", "--", rel)
    if out is None:
        return None
    times, cur = [], None
    for line in out.split("\n"):
        if line.startswith("author-time "):
            cur = int(line[12:])
        elif line.startswith("committer ") and line.endswith("Not Committed Yet"):
            cur = None
        elif line.startswith("\t"):
            times.append(cur)
    return times


def markdown_roots(ix, src):
    """(key prefix, folder to list, path prefix inside the key, project)."""
    if src.get("registry"):
        for p in ix.registry:
            if p.get("host") not in (None, "local"):
                continue
            root = os.path.expanduser(p["path"])
            for d in src.get("dirs") or ["wiki"]:
                if os.path.isdir(os.path.join(root, d)):
                    yield f"{p.get('slug')}/", root, d.strip("/") + "/", p.get("slug")
    else:
        root = expand(src.get("path"), ix.hq)
        if root and os.path.isdir(root):
            yield src.get("prefix") or "", root, "", src.get("project")


def list_markdown(root, under, exclude):
    out = git_out(root, "ls-files", "-z", "--", under or ".")
    if out is not None and out:
        rels = [r for r in out.split("\0") if r]
    else:
        rels = []
        for base, dirs, names in os.walk(os.path.join(root, under), followlinks=False):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            rels += [os.path.relpath(os.path.join(base, n), root) for n in names]
    return sorted(r for r in rels if r.endswith((".md", ".markdown"))
                  and not any(r.startswith(x) for x in exclude) and os.path.isfile(os.path.join(root, r)))


def md_sections(lines):
    """(first line index, last+1, text) per section: a heading starts one; long ones split at blank lines."""
    out, start, size = [], 0, 0
    for i, line in enumerate(lines):
        heading = re.match(r"#{1,6}\s", line)
        if i > start and (heading or (size > CHUNK and not line.strip())):
            out.append((start, i))
            start, size = i, 0
        size += len(line)
    if start < len(lines):
        out.append((start, len(lines)))
    return [(a, b, "".join(lines[a:b])) for a, b in out if "".join(lines[a:b]).strip()]


def index_markdown(ix, src, full):
    exclude = tuple(src.get("exclude") or ())
    changed = seen = 0
    for prefix, root, under, project in markdown_roots(ix, src):
        for rel in list_markdown(root, under, exclude):
            seen += 1
            path = os.path.join(root, rel)
            st = os.stat(path)
            stamp = f"{st.st_mtime_ns}:{st.st_size}"
            key = prefix + rel
            state = ix.file_state(src["name"], key)
            if state and state[1] == stamp and not full:
                continue
            fid = ix.file_begin(src["name"], key, stamp)
            with open(path, encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            times = blame_times(root, rel)
            now = time.time()
            heading = next((ln.strip("# \n") for ln in lines if ln.startswith("#")), None)
            did = ix.doc(key, src["name"], project=project, title=heading or os.path.basename(rel), link=path)
            for a, b, text in md_sections(lines):
                stamps = [t for t in (times or [])[a:b] if t]
                ts = min(stamps) if stamps else (st.st_mtime if times is None else now)
                ix.add(src, did, ts, text, role="note", kind=src.get("type") or "knowledge", file_id=fid)
            changed += 1
    return changed, seen


def index_command(ix, src, full):
    name = src["name"]
    since = "0" if full else (ix.db.execute("SELECT v FROM meta WHERE k=?", (f"since:{name}",)).fetchone() or ["0"])[0]
    started = time.time()
    env = dict(os.environ, BRAIN_SINCE=str(since))
    proc = subprocess.Popen(shlex.split(src["run"]), cwd=ix.hq, stdout=subprocess.PIPE, env=env,
                            text=True, encoding="utf-8", errors="replace")
    changed = seen = 0
    for line in proc.stdout:
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            print(f"  ! {name}: not JSON: {line[:80]}", file=sys.stderr)
            continue
        if not row.get("id"):
            continue
        seen += 1
        msg = h64(f"{name}\0{row['id']}")
        chash = h64(json.dumps([row.get(k) for k in ("text", "ts", "doc", "kind", "role", "title")],
                               ensure_ascii=False, default=str))
        old = ix.db.execute("SELECT chash FROM units WHERE msg=? LIMIT 1", (msg,)).fetchone()
        if old and old[0] == chash and not full:
            continue
        if old:
            ix.drop("msg", msg)
        did = ix.doc(str(row.get("doc") or row["id"]), row.get("source") or name, project=row.get("project"),
                     title=row.get("title"), link=row.get("link"), fill_only=True)
        for a in row.get("aliases") or []:
            ix.alias(a, did, name)
        ix.add(src, did, parse_ts(row.get("ts")), row.get("text") or "", role=row.get("role") or "",
               kind=row.get("kind"), msg=msg, chash=chash)
        changed += 1
    if proc.wait() != 0:
        raise RuntimeError(f"`{src['run']}` exited {proc.returncode}")
    ix.db.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (f"since:{name}", str(started)))
    return changed, seen


KINDS = {"claude-transcripts": index_claude, "codex-sessions": index_codex, "markdown": index_markdown,
         "command": index_command}


def cmd_index(args):
    sources, path = load_config(args.hq)
    if not sources:
        print("hq.yaml has no `brain:` sources — nothing to index", file=sys.stderr)
        return 3
    db = connect(path, write=True)
    ix = Indexer(db, args.hq, read_registry(args.hq))
    failed, t0 = 0, time.time()
    for src in sources:
        if args.source and src["name"] != args.source:
            continue
        fn = KINDS.get(src["kind"])
        if not fn:
            print(f"  ✗ {src['name']}: unknown kind {src['kind']!r}", file=sys.stderr)
            failed += 1
            continue
        t, before = time.time(), ix.added
        try:
            changed, seen = fn(ix, src, args.full)
            db.commit()
            print(f"  ✓ {src['name']}: {seen} seen, {changed} (re)indexed, +{ix.added - before} units, "
                  f"{time.time() - t:.1f}s")
        except Exception as exc:  # one broken source must not stop the others
            db.commit()
            print(f"  ✗ {src['name']}: {exc}", file=sys.stderr)
            failed += 1
    if ix.added > 50000 or args.full:
        db.execute("INSERT INTO fts(fts) VALUES ('optimize')")
    relinked = relink_atoms(db)
    if relinked:
        print(f"  ✓ atoms: {relinked} relinked to their re-indexed messages")
    db.execute("INSERT OR REPLACE INTO meta VALUES ('indexed_at', ?)", (str(time.time()),))
    db.execute("INSERT OR REPLACE INTO meta VALUES ('units', (SELECT count(*) FROM units))")
    db.commit()
    db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    db.close()
    print(f"index: {path} · {os.path.getsize(path) / 1048576:.0f} MB · {time.time() - t0:.0f}s")
    return 2 if failed else 0


def relink_atoms(db):
    """A re-read chat gets new unit ids; each atom finds its message again by its verbatim quote (same
    chat, closest in time). Returns how many were relinked; one never found keeps unit NULL."""
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='atoms'").fetchone():
        return 0
    lost = db.execute("SELECT a.id, a.quote, a.ts, COALESCE(d.canon, d.id) FROM atoms a JOIN docs d ON d.id = a.doc "
                      "WHERE a.unit IS NOT NULL AND NOT EXISTS (SELECT 1 FROM units u WHERE u.id = a.unit)").fetchall()
    fixed = 0
    for aid, quote, ts, canon in lost:
        found = None
        for uid, uts, blob in db.execute(
                "SELECT u.id, u.ts, u.text FROM units u JOIN docs d ON d.id = u.doc WHERE COALESCE(d.canon, d.id) = ? "
                "ORDER BY abs(u.ts - ?)", (canon, ts)):
            if quote_in(quote or "", zlib.decompress(blob).decode("utf-8", "replace")):
                found = uid
                break
        db.execute("UPDATE atoms SET unit=? WHERE id=?", (found, aid))
        fixed += found is not None
    return fixed


# ---------------------------------------------------------------- search

def query_terms(db, text, atoms=False):
    ids = extract_ids(text)
    id_numbers = {p for i in ids for p in i.split("_") if p.isdigit()}   # «!19» means MR 19, not any 19
    stems = []
    for t in words(text):
        if t in STOP or t in FILLER or (len(t) < 2 and not t.isdigit()) or t in id_numbers:
            continue
        s = stem(t)
        if s not in stems:
            stems.append(s)
    variants = []
    for s in stems:
        if re.search(r"[а-яё]", s) and len(s) >= 4:
            v = stem_en(translit(s))
            if v not in stems and v not in variants:
                variants.append(v)
    terms = stems + variants + ids
    if not terms:
        return [], []
    df = dict(db.execute(f"SELECT term, doc FROM fts_v WHERE term IN ({','.join('?' * len(terms))})", terms))
    if atoms:   # a word only an atom has (a claim's own wording) is still a search term
        for term, n in db.execute(f"SELECT term, doc FROM atoms_v WHERE term IN ({','.join('?' * len(terms))})", terms):
            df.setdefault(term, n)
    total = (db.execute("SELECT v FROM meta WHERE k='units'").fetchone() or [None])[0]
    total = int(total) if total else db.execute("SELECT count(*) FROM units").fetchone()[0]
    keep = [s for s in stems + variants if df.get(s)]
    rare = [s for s in keep if df[s] <= COMMON_DF * total]
    if rare or [i for i in ids if df.get(i)]:
        keep = rare
    return keep, [i for i in ids if df.get(i)]


def fts_quote(term):
    return '"' + term.replace('"', '""') + '"'


def decay(age_days, kind):
    half = HALF_LIFE.get(kind, 60.0)
    if not half or age_days <= 0:
        return 1.0
    return 0.5 ** (age_days / half)


def snippet(text, stems, ids, width=220):
    low = text.lower()
    pos = None
    for m in re.finditer(r"[^\W_]+", low):
        if stem(m.group(0)) in stems:
            pos = m.start()
            break
    if pos is None:
        for i in ids:
            j = re.sub(r"_", "", low).find(i.replace("_", ""))
            if j >= 0:
                pos = 0
                break
    pos = pos or 0
    a = max(0, pos - 70)
    s = re.sub(r"\s+", " ", text[a:a + width]).strip()
    return ("…" if a else "") + s + ("…" if a + width < len(text) else "")


def unit_text(db, uid):
    return zlib.decompress(db.execute("SELECT text FROM units WHERE id=?", (uid,)).fetchone()[0]).decode()


class OwnerMarkers(list):
    """Markers of text the owner did not type, plus `sources` — the sources he types into (None = all)."""
    sources = None


def owner_markers(hq):
    """Markers of messages the owner did not type: relayed (`brain_relay_markers:`), automation prompts
    (`brain_hint_skip:`, `brain_atoms_skip:`); `brain_owner_sources:` — the only sources where he types."""
    cfg = read_yaml_subset(os.path.join(hq, "hq.yaml"))
    out = OwnerMarkers(str(x) for k in ("brain_relay_markers", "brain_hint_skip", "brain_atoms_skip")
                       for x in as_list(cfg.get(k)))
    out.sources = {str(x) for x in as_list(cfg.get("brain_owner_sources"))} or None
    return out


def has_atoms(db):
    return bool(db.execute("SELECT 1 FROM sqlite_master WHERE name='atoms_v'").fetchone()) and \
        bool(db.execute("SELECT 1 FROM atoms LIMIT 1").fetchone())


def search(db, text, before=None, project=None, limit=8, now=None, atoms=True, role=None, since=None, exclude=(),
           owner_skip=None):
    """Top documents for `text`: list of dicts (key, aliases, source, project, title, link, ts, score, snippet).

    With atoms in the index, atom hits rank alongside raw units: each counts for its source chat (its
    canonical doc), and a hit whose best match is an atom carries it under `atom`. `role` keeps only units
    of that role (user = the owner's own words; atoms are then left out), `since` only what was written
    after it, `exclude` drops these canonical doc ids. `owner_skip` (a list of markers) keeps only messages
    the owner typed himself — no relayed handoffs, automation prompts, diffs (see typed_by_owner)."""
    atoms = atoms and not role and has_atoms(db)
    stems, ids = query_terms(db, text, atoms)
    if not stems and not ids:
        return []
    parts = []
    if stems:
        parts.append("stem : (" + " OR ".join(fts_quote(s) for s in stems) + ")")
    if ids:
        parts.append("ids : (" + " OR ".join(fts_quote(i) for i in ids) + ")")
    sql = f"SELECT rowid, bm25(fts, {COLS[0]}, {COLS[1]}) AS r FROM fts WHERE fts MATCH ?"
    params = [" OR ".join(parts)]
    if before is not None:
        sql += " AND rowid < ?"
        params.append(rowid_floor(before))
    pool = POOL * (5 if project or role or since else 1)
    sql += " ORDER BY r LIMIT ?"
    params.append(pool)
    cands = db.execute(sql, params).fetchall()
    acands = []
    if atoms:
        asql = sql.replace("bm25(fts,", "bm25(atoms_fts,").replace("FROM fts WHERE fts MATCH", "FROM atoms_fts WHERE atoms_fts MATCH")
        acands = db.execute(asql, params[:-1] + [POOL // 4]).fetchall()
    if not cands and not acands:
        return []
    raw = dict(cands)
    if not raw and not acands:
        return []
    ref = before if before is not None else (now or time.time())
    rows = db.execute(
        "SELECT u.id, u.ts, u.kind, u.weight, u.dup, COALESCE(d.canon, d.id), d.weight, u.role, d.canon, d.source FROM units u "
        f"JOIN docs d ON d.id = u.doc WHERE u.id IN ({','.join('?' * len(raw))})", list(raw)).fetchall() if raw else []
    by_doc = {}
    owner_sources = getattr(owner_skip, "sources", None)
    for uid, ts, kind, w, dup, canon, dw, urole, alias, usrc in rows:
        if (role and urole != role) or (since is not None and ts < since) or canon in exclude:
            continue
        if owner_skip is not None and (alias or (dw or 1.0) < 1.0 or (owner_sources and usrc not in owner_sources)
                                       or not typed_by_owner(unit_text(db, uid), owner_skip)):
            continue                     # an alias doc is a second copy; its main source knows who typed
        score = -raw[uid] * (w or 1.0) * (dw or 1.0) * TYPE_WEIGHT.get(kind, 1.0) * decay((ref - ts) / 86400, kind)
        by_doc.setdefault(canon, []).append((score, uid, ts, dup))
    if acands:
        araw = dict(acands)
        for aid, ts, kind, conf, canon, sup in db.execute(
                "SELECT a.id, a.ts, a.kind, a.confidence, COALESCE(d.canon, d.id), a.superseded_by FROM atoms a "
                f"JOIN docs d ON d.id = a.doc WHERE a.id IN ({','.join('?' * len(araw))})", list(araw)):
            w = ATOM_WEIGHT * ATOM_KIND_WEIGHT.get(kind, 1.0) * (0.3 if sup else 1.0) * (0.5 + 0.5 * (conf or 0.5))
            half = ATOM_HALF_LIFE.get(kind)
            age = (ref - ts) / 86400
            score = -araw[aid] * w * (0.5 ** (age / half) if half and age > 0 else 1.0)
            if (since is not None and ts < since) or canon in exclude:
                continue
            by_doc.setdefault(canon, []).append((score, -aid, ts, None))   # negative id = an atom
    if not by_doc:
        return []
    docs = {r[0]: r for r in db.execute(
        f"SELECT id, key, source, project, title, link FROM docs WHERE id IN ({','.join('?' * len(by_doc))})",
        list(by_doc))}
    ranked = []
    for canon, units in by_doc.items():
        if project and docs[canon][3] != project:
            continue
        units.sort(reverse=True)
        score = units[0][0] + 0.2 * sum(u[0] for u in units[1:3])
        ranked.append((score, canon, units))
    ranked.sort(reverse=True)
    out = []
    stemset = set(stems)
    for score, canon, units in ranked[:limit]:
        _, key, source, proj, title, link = docs[canon]
        best = units[0]
        atom = None
        if best[1] < 0:
            kind, claim, quote, unit = db.execute("SELECT kind, claim, quote, unit FROM atoms WHERE id=?",
                                                  (-best[1],)).fetchone()
            atom = {"id": -best[1], "kind": kind, "claim": claim, "quote": quote, "unit": unit}
            body = f"[{kind}] {claim} — «{quote}»"
        else:
            body = zlib.decompress(db.execute("SELECT text FROM units WHERE id=?", (best[1],)).fetchone()[0]).decode()
        aliases = [r[0] for r in db.execute("SELECT key FROM docs WHERE canon=?", (canon,))]
        dup = db.execute("SELECT COALESCE(c.key, d.key) FROM docs d LEFT JOIN docs c ON c.id = d.canon "
                         "WHERE d.id=?", (best[3],)).fetchone() if best[3] else None
        out.append({"rank": len(out) + 1, "key": key, "aliases": aliases, "source": source, "project": proj,
                    "title": title, "link": link, "ts": iso(best[2]), "score": round(score, 4),
                    "units": len(units), "repeat_of": dup[0] if dup else None,
                    "snippet": body if atom else snippet(body, stemset, ids), "atom": atom})
    return out


def cmd_search(args):
    _, path = load_config(args.hq)
    try:
        db = connect(path)
    except FileNotFoundError:
        print(f"no index at {path} — run `hq_brain.py index` first", file=sys.stderr)
        return 3
    before = parse_ts(args.before) if args.before else None
    t = time.perf_counter()
    hits = search(db, args.text, before=before, project=args.project, limit=args.limit, atoms=not args.no_atoms,
                  role=args.role, since=parse_ts(args.since) if args.since else None,
                  owner_skip=owner_markers(args.hq) if args.role == "user" else None)
    ms = (time.perf_counter() - t) * 1000
    spread = {"chats": len(hits), "days": len({(h["ts"] or "")[:10] for h in hits}),
              "projects": len({h["project"] for h in hits if h["project"]})}
    if args.json:
        print(json.dumps({"query": args.text, "before": args.before, "ms": round(ms, 1), "spread": spread, "hits": hits},
                         ensure_ascii=False, indent=1))
        return 0
    for h in hits:
        print(f"{h['rank']}. {(h['ts'] or '')[:16]} [{h['source']}] {h['project'] or '-'} — {h['title']}")
        print(f"   {h['key']}" + (f" (also {', '.join(h['aliases'][:3])})" if h["aliases"] else "")
              + (f" · repeat of {h['repeat_of']}" if h["repeat_of"] else ""))
        print(f"   {h['snippet']}")
    print(f"({len(hits)} hits over {spread['days']} days, {spread['projects']} projects · {ms:.0f} ms)")
    return 0


# ---------------------------------------------------------------- atoms (layer 3: model distills a chat)

ATOM_KINDS = ("decision", "fact", "fix", "preference", "question", "person", "correction")
ATOM_WEIGHT = 1.5                       # an atom is a distilled claim: it outranks a raw message of equal bm25
ATOM_KIND_WEIGHT = {"decision": 1.2, "correction": 1.2, "fix": 1.1, "preference": 1.1}
ATOM_HALF_LIFE = {"question": 60.0, "fix": 180.0, "fact": 365.0}   # the rest never decay, they get superseded
ATOM_BUDGET = 12000                     # dialogue chars sent per chat
ATOM_MAX = 8                            # atoms kept per chat
MSG_CAP = {"user": 700, "assistant": 900}
MIN_QUOTE = 12

MASKS = [
    (re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"), "<email>"),
    (re.compile(r"(?<![\w+])\+\d{1,3}[\s().-]*\d[\d\s().-]{6,}\d"), "<phone>"),
    (re.compile(r"\b8[\s(-]*9\d{2}[\s)-]*\d{3}[\s-]*\d{2}[\s-]*\d{2}\b"), "<phone>"),
    (re.compile(r"\b(?:sk-[A-Za-z0-9_-]{16,}|xox[abprse]-[A-Za-z0-9-]{10,}|gh[pousr]_[A-Za-z0-9]{20,}|"
                r"glpat-[A-Za-z0-9_-]{16,}|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{30,}|"
                r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,})"), "<token>"),
    (re.compile(r"(?i)\b((?:api[_-]?key|access[_-]?token|token|secret|password|passwd|pwd)\s*[:=]\s*)[\"']?[^\s\"']{6,}"),
     r"\1<secret>"),
    (re.compile(r"\b(?=[A-Za-z0-9+/_-]*[A-Z])(?=[A-Za-z0-9+/_-]*[a-z])(?=[A-Za-z0-9+/_-]*\d)[A-Za-z0-9+/_-]{40,}={0,2}"),
     "<token>"),
]

PROMPT = """Extract knowledge atoms from this chat between the owner, %(owner)s (user), and an AI agent (assistant).
Return ONLY JSON: {"atoms":[{"kind":"...","claim":"...","quote":"...","msg":N,"entities":["..."],"confidence":0.0}]}
kind — one of:
  decision: something decided, chosen or approved;
  fact: a lasting fact about a system, project or setup;
  fix: an error and its fix ("X failed because Y; fixed by Z");
  preference: how the owner wants things done;
  question: a question still open at the end of the chat;
  person: a fact about a named person (role, responsibility, what they asked for);
  correction: the owner corrected the agent in his own words (what was wrong, what is right).
Who wrote it: a "user" message is the owner's own words only when he typed or dictated it. A "relayed" message,
and a "user" message that reads like a brief ("Continue from …", "Do: 1) … 2) …", a quoted message with a sender),
was written by another agent, a script or another person. Name the real author in the claim: the owner by name,
the person by name, "the agent", "a task from another chat". preference and correction come only from the owner's
own words; an agent correcting itself is a fix or a fact, never a correction.
Keep: what the agent reports it did and checked (fixed, shipped, merged, measured — with ids, numbers and the
reason) as fix or fact; who asked for what or owns what as person; decisions and open questions.
Rules: 0-%(max)d atoms, only knowledge worth finding weeks later — skip greetings, "working on it" updates, tool
noise, routine status moves (a task kept in a status, the next session started), anything still being guessed. claim: one self-contained sentence in %(lang)s, with the names, ids and dates it
needs to be understood without the chat. It says only what message [msg] itself supports: do not join messages
from different turns or days into one claim, and do not add details the message does not state. quote: 20-200
characters copied VERBATIM from message [msg] (same words, same order, no "..."), the part that proves the claim.
entities: task ids, projects, people, systems, files named. confidence: 0-1, how sure the claim is right and
final. No atoms → {"atoms":[]}.

CHAT (project: %(project)s):
%(chat)s"""


def mask(text):
    for rx, rep in MASKS:
        text = rx.sub(rep, text)
    return text


def norm_quote(text):
    text = text.lower().replace("ё", "е")
    text = re.sub(r"[«»“”„\"'`’‘]", "", text)
    text = re.sub(r"[—–‑-]", "-", text)
    text = re.sub(r"[*_#>|]", " ", text)
    return " ".join(text.split())


def quote_in(quote, text):
    """True when the quote (or each part of it around an ellipsis) appears in text, punctuation-tolerant."""
    parts = [norm_quote(p) for p in re.split(r"\.\.\.|…", quote)]
    parts = [p for p in parts if len(p) >= 8] or [norm_quote(quote)]
    hay = norm_quote(text)
    return all(p and p in hay for p in parts)


def as_list(value):
    return value if isinstance(value, list) else [value] if value else []


def atoms_config(hq):
    cfg = read_yaml_subset(os.path.join(hq, "hq.yaml"))
    skip = cfg.get("brain_atoms_skip") or []
    return {"model": cfg.get("brain_model"), "skip": [str(x).lower() for x in (skip if isinstance(skip, list) else [skip])],
            "budget": int(cfg.get("brain_atoms_budget") or ATOM_BUDGET), "lang": cfg.get("brain_language"),
            "owner": cfg.get("brain_owner"), "relay": [str(x) for x in as_list(cfg.get("brain_relay_markers"))],
            "verify": str(cfg.get("brain_verify", "true")).lower() not in ("false", "no", "0", "off"),
            "verify_model": cfg.get("brain_verify_model")}


def chat_candidates(db, sources, skip, redo=False, min_turns=2):
    """Canonical chat docs worth distilling, newest first: (canon id, title, project)."""
    marks = ",".join("?" * len(sources))
    rows = db.execute(f"""
        SELECT c.id, c.title, c.weight, max(u.ts), sum(u.role IN ('user','relay')), sum(u.role='assistant'), c.project
        FROM units u JOIN docs d ON d.id = u.doc JOIN docs c ON c.id = COALESCE(d.canon, d.id)
        WHERE c.source IN ({marks}) AND u.dup IS NULL GROUP BY c.id ORDER BY max(u.ts) DESC""", sources).fetchall()
    done = {r[0] for r in db.execute("SELECT doc FROM atom_runs WHERE status='ok'")} if not redo else set()
    out = []
    for cid, title, weight, last, n_user, n_asst, project in rows:
        if cid in done or (weight or 1.0) < 1.0 or (n_user or 0) < min_turns or (n_asst or 0) < min_turns:
            continue
        if skip:
            first = db.execute("SELECT u.text FROM units u JOIN docs d ON d.id = u.doc WHERE COALESCE(d.canon, d.id)=? "
                               "AND u.role IN ('user','relay') ORDER BY u.id LIMIT 1", (cid,)).fetchone()
            head = ((title or "") + "\n" + (zlib.decompress(first[0]).decode()[:300] if first else "")).lower()
            if any(p in head for p in skip):
                continue
        out.append((cid, title, project))
    return out


def chat_dialogue(db, cid, budget=ATOM_BUDGET):
    """The chat's messages (masked, capped) within budget: list of dicts n, role, ts, unit, text (full masked)."""
    rows = db.execute("SELECT u.id, u.ts, u.role, u.text FROM units u JOIN docs d ON d.id = u.doc "
                      "WHERE COALESCE(d.canon, d.id)=? AND u.dup IS NULL AND u.role IN ('user','assistant','relay') "
                      "ORDER BY u.ts, u.id", (cid,)).fetchall()
    msgs, seen = [], set()
    for uid, ts, role, blob in rows:
        text = zlib.decompress(blob).decode("utf-8", "replace")
        if msgs and msgs[-1]["role"] == role and msgs[-1]["ts"] == ts:      # chunks of one message
            msgs[-1]["text"] += "\n\n" + text
            continue
        msgs.append({"role": role, "ts": ts, "unit": uid, "text": text})
    out = []
    for m in msgs:
        key = " ".join(words(m["text"]))[:400]
        if not key or key in seen:                                        # same message via two sources
            continue
        seen.add(key)
        m["text"] = mask(m["text"].strip())
        out.append(m)
    for m in out:
        cap = MSG_CAP.get(m["role"], 800)
        m["shown"] = m["text"] if len(m["text"]) <= cap else m["text"][:cap].rstrip() + " […]"
    # owner turns first, then the agent's from the end (the outcome), until the budget is spent
    order = [i for i, m in enumerate(out) if m["role"] in ("user", "relay")] + \
            [i for i in range(len(out) - 1, -1, -1) if out[i]["role"] not in ("user", "relay")]
    keep, used = set(), 0
    for i in order:
        cost = len(out[i]["shown"]) + 24
        if used + cost > budget:
            continue
        keep.add(i)
        used += cost
    picked = [m for i, m in enumerate(out) if i in keep]
    for n, m in enumerate(picked, 1):
        m["n"] = n
    return picked


def render_chat(msgs, relay=()):
    """Numbered dialogue; a user message that starts with a relay marker is labelled relayed (not the owner's words)."""
    lines = []
    for m in msgs:
        day = dt.datetime.fromtimestamp(m["ts"]).strftime("%Y-%m-%d")
        role = "relayed" if m["role"] == "relay" or (m["role"] == "user" and m["text"].lstrip().startswith(tuple(relay))) \
            else m["role"]
        lines.append(f"[{m['n']}] {role} {day}: {m['shown']}")
    return "\n\n".join(lines)


def run_model(cmd, prompt, timeout=600):
    """Run the configured model command with the prompt on stdin, in an empty temp dir.
    Returns (answer text, usage dict, seconds, error or None)."""
    t = time.time()
    with tempfile.TemporaryDirectory(prefix="brain-atoms-") as tmp:
        try:
            p = subprocess.run(shlex.split(cmd), input=prompt, capture_output=True, text=True, timeout=timeout,
                               cwd=tmp, encoding="utf-8", errors="replace")
        except (OSError, subprocess.TimeoutExpired) as exc:
            return "", {}, time.time() - t, f"{type(exc).__name__}: {exc}"[:300]
    secs = time.time() - t
    out, usage, answer = p.stdout or "", {}, None
    events = []
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                events.append(json.loads(line))
            except ValueError:
                pass
    for ev in events:
        item = ev.get("item") or {}
        if item.get("type") == "agent_message" and item.get("text"):
            answer = item["text"]
        if isinstance(ev.get("usage"), dict):
            usage = ev["usage"]
        if isinstance(ev.get("result"), str):
            answer = ev["result"]
    if answer is None:
        answer = out
    err = None if p.returncode == 0 else f"exit {p.returncode}: {(p.stderr or out).strip()[-300:]}"
    return answer, usage, secs, err


def parse_atoms(answer):
    s = answer.strip()
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s)
    a = s.find("{")
    if a < 0:
        raise ValueError("no JSON object in the answer")
    data, _ = json.JSONDecoder().raw_decode(s[a:])        # the first object; models sometimes repeat it
    items = data.get("atoms") if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise ValueError("`atoms` is not a list")
    return [x for x in items if isinstance(x, dict)]


CLAIM_ID_RE = re.compile(r"\b[A-Z][A-Z0-9]+-\d+\b|![0-9]+\b|#\d{3,}\b|\b(?=[0-9a-f]*\d)(?=[0-9a-f]*[a-f])[0-9a-f]{7,40}\b"
                         r"|\b\d{3,}\b")
OWNER_ONLY = ("preference", "correction")


def digits_joined(text):
    """«2 801» and «2801» compare equal; so do «4,368» and «4368»."""
    return re.sub(r"(?<=\d)[\s\u00a0\u202f,](?=\d{3}\b)", "", text)


def unsupported_ids(claim, support, ts):
    """Ids, hashes and 3+ digit numbers in the claim that none of the support texts contain.
    The message's own year is allowed: claims are dated, messages rarely say the year."""
    year = dt.datetime.fromtimestamp(ts).strftime("%Y") if ts else None
    hay = digits_joined(" ".join(support)).lower()
    return [t for t in dict.fromkeys(CLAIM_ID_RE.findall(digits_joined(claim)))
            if t != year and t.lower() not in hay]


def support_texts(msgs, home):
    """What a claim may draw on: its message, the owner's message before it, the chat's first message."""
    i = msgs.index(home)
    before = next((m for m in reversed(msgs[:i]) if m["role"] == "user"), None)
    first = next((m for m in msgs if m["role"] == "user"), None)
    return [m["text"] for m in (home, before, first) if m]


def is_owner_words(m, relay=()):
    return m["role"] == "user" and not m["text"].lstrip().startswith(tuple(relay))


def check_atoms(items, msgs, relay=()):
    """Keep atoms with a known kind, a claim and a quote found in the input; relink a quote to the
    message it is really in. Drop a claim whose ids or numbers its message does not contain, and a
    preference or correction that is not the owner's own words. Returns (kept, dropped count)."""
    by_n = {m["n"]: m for m in msgs}
    kept, dropped, claims = [], 0, set()
    for it in items:
        kind = str(it.get("kind") or "").strip().lower()
        claim = " ".join(str(it.get("claim") or "").split())
        quote = " ".join(str(it.get("quote") or "").split())
        if kind not in ATOM_KINDS or not claim or len(norm_quote(quote)) < MIN_QUOTE:
            dropped += 1
            continue
        try:
            n = int(it.get("msg"))
        except (TypeError, ValueError):
            n = None
        home = by_n.get(n) if n is not None and by_n.get(n) and quote_in(quote, by_n[n]["text"]) else \
            next((m for m in msgs if quote_in(quote, m["text"])), None)
        if home is None or norm_quote(claim) in claims or \
                unsupported_ids(claim, support_texts(msgs, home), home["ts"]) or \
                (kind in OWNER_ONLY and not is_owner_words(home, relay)):
            dropped += 1
            continue
        claims.add(norm_quote(claim))
        ents = it.get("entities") or []
        ents = [str(e)[:80] for e in (ents if isinstance(ents, list) else [ents]) if e][:12]
        try:
            conf = max(0.0, min(1.0, float(it.get("confidence"))))
        except (TypeError, ValueError):
            conf = 0.5
        kept.append({"kind": kind, "claim": claim[:600], "quote": quote[:400], "entities": ents,
                     "confidence": conf, "unit": home["unit"], "ts": home["ts"], "home": home})
    return kept[:ATOM_MAX], dropped + max(0, len(kept) - ATOM_MAX)


def store_atoms(db, cid, atoms, model):
    for aid in [r[0] for r in db.execute("SELECT id FROM atoms WHERE doc=?", (cid,))]:
        db.execute("DELETE FROM atoms_fts WHERE rowid=?", (aid,))
        db.execute("DELETE FROM atom_questions WHERE atom=?", (aid,))
    db.execute("DELETE FROM atoms WHERE doc=?", (cid,))
    now = time.time()
    for a in atoms:
        base = rowid_floor(a["ts"])
        top = db.execute("SELECT max(id) FROM atoms WHERE id >= ? AND id < ?", (base, base + SLOTS)).fetchone()[0]
        aid = base if top is None else top + 1
        body = " ".join([a["claim"], a["quote"], *a["entities"]])
        db.execute("INSERT INTO atoms(id, doc, unit, kind, claim, quote, entities, ts, confidence, superseded_by, "
                   "model, made) VALUES (?,?,?,?,?,?,?,?,?,NULL,?,?)",
                   (aid, cid, a["unit"], a["kind"], a["claim"], a["quote"], json.dumps(a["entities"], ensure_ascii=False),
                    a["ts"], a["confidence"], model, now))
        db.execute("INSERT INTO atoms_fts(rowid, stem, ids) VALUES (?,?,?)",
                   (aid, stem_text(body), " ".join(extract_ids(body))))


VERIFY_PROMPT = """You check knowledge atoms extracted from a chat between the owner, %(owner)s, and an AI agent.
Each atom comes with the message its quote is from and the owner's message before it. Judge only from that text.
An atom passes only when ALL hold:
1. Every statement in the claim is stated in the shown text: no detail, number, id, date, cause or name added
   from elsewhere or guessed.
2. The author is right: the owner only for his own words; an agent's report is "the agent"; a relayed message
   or a task brief belongs to whoever wrote it.
3. The kind fits: decision = decided or approved; fact = a lasting fact; fix = an error and its fix; preference =
   a standing wish the owner states himself (not a one-off doubt, not a task's constraint); question = still open;
   person = a fact about a named person; correction = the owner corrected the agent.
4. It is worth finding weeks later, not a routine status line ("closing this session", "task stays in progress").
These slips fail an atom, look for each one:
- tense: the text says it is being done or planned ("restoring", "will ship"), the claim says it was done;
- an added purpose, cause or consequence ("in order to", "because", "so that") the text does not state;
- a name, tag, id or term taken from elsewhere (a task title, a later message) instead of this text;
- scope stretched: what the text says of one field, case or step, the claim says of several or of all;
- a contrast or alternative ("instead of X") the text does not mention;
- a wrong category word for a thing (a project called a channel, a finding called a fix).
- an agent's own plan, intention or condition ("I'll run it when he merges") called a decision: a decision is
  what the owner decided or approved, or what was settled, not what the agent means to do next;
- negation or state flipped: "there was no X, I made it" read as "X already existed", "not done" as "done";
- an object filled in: the text asks to "make it for me too" without naming what, the claim names it;
- a relay taken for the author: "a message from thread X", passed on by a tool, is written by the chat X;
- a new feature or capability called a fix (a fix needs an error that it corrects);
- an instruction or condition the owner set called a person fact (that is a decision);
- a question called open while the chat's end (shown for questions) answers or settles it.
Fix a claim only by cutting or correcting what the text does not support, in %(lang)s, or fix the kind. If it
cannot be made exact, or you are unsure, keep=false.
Return ONLY JSON: {"verdicts":[{"i":N,"keep":true,"claim":null,"kind":null,"why":"short"}]} — one verdict per atom;
claim/kind null when unchanged.

ATOMS:
%(atoms)s"""


def render_for_check(atoms, msgs, relay=()):
    blocks = []
    for i, a in enumerate(atoms, 1):
        home = a["home"]
        t, q = home["text"], norm_quote(a["quote"])[:30]
        pos = norm_quote(t).find(q)
        start = max(0, int(pos * len(t) / max(1, len(norm_quote(t)))) - 700) if pos >= 0 else 0
        role = home["role"] if is_owner_words(home, relay) or home["role"] != "user" else "relayed"
        before = next((m for m in reversed(msgs[:msgs.index(home)]) if m["role"] == "user"), None)
        day = dt.datetime.fromtimestamp(home["ts"]).strftime("%Y-%m-%d")
        lines = [f"[{i}] {a['kind']}: {a['claim']}", f"  quote: {a['quote']}",
                 f"  message ({role}, {day}): {t[start:start + 1600]}"]
        if before and before is not home:
            lines.append(f"  owner's message before it: {before['text'][:600]}")
        if a["kind"] == "question":
            tail = [m for m in msgs[msgs.index(home) + 1:]][-2:]
            for m in tail:
                lines.append(f"  chat's end ({m['role']}): {m['text'][:700]}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def verify_atoms(cmd, atoms, msgs, conf):
    """A second model call judges each atom against its message; fail closed. Returns (kept, dropped, rec)."""
    if not atoms:
        return [], 0, {}
    prompt = VERIFY_PROMPT % {"owner": conf.get("owner") or "the owner", "lang": conf.get("lang") or "the chat's language",
                              "atoms": render_for_check(atoms, msgs, conf.get("relay") or ())}
    answer, usage, secs, err = run_model(conf.get("verify_model") or cmd, prompt)
    rec = {"chars": len(prompt), "secs": secs, "usage": usage, "error": err}
    if err:
        return [], len(atoms), rec
    try:
        s = re.sub(r"^```(?:json)?\s*|\s*```$", "", answer.strip())
        data, _ = json.JSONDecoder().raw_decode(s[s.find("{"):])
        verdicts = {int(v["i"]): v for v in data.get("verdicts") or [] if isinstance(v, dict) and "i" in v}
    except (ValueError, TypeError, KeyError):
        rec["error"] = f"bad verdicts: {answer.strip()[:200]}"
        return [], len(atoms), rec
    kept = []
    for i, a in enumerate(atoms, 1):
        v = verdicts.get(i)
        if not v or v.get("keep") is not True:
            continue
        claim = " ".join(str(v.get("claim") or a["claim"]).split())[:600]
        kind = str(v.get("kind") or a["kind"]).strip().lower()
        if kind not in ATOM_KINDS or unsupported_ids(claim, support_texts(msgs, a["home"]), a["ts"]) or \
                (kind in OWNER_ONLY and not is_owner_words(a["home"], conf.get("relay") or ())):
            continue
        kept.append({**a, "claim": claim, "kind": kind})
    return kept, len(atoms) - len(kept), rec


def distill(cmd, msgs, project=None, conf=None):
    """One model call over one chat → (kept atoms, dropped, run record)."""
    conf = conf or {}
    prompt = PROMPT % {"max": ATOM_MAX, "chat": render_chat(msgs, conf.get("relay") or ()), "project": project or "-",
                       "lang": conf.get("lang") or "the chat's language", "owner": conf.get("owner") or "the owner"}
    answer, usage, secs, err = run_model(cmd, prompt)
    rec = {"calls": 1, "chars": len(prompt), "secs": secs, "in": usage.get("input_tokens"),
           "cached": usage.get("cached_input_tokens") or usage.get("cache_read_input_tokens"),
           "out": usage.get("output_tokens"), "error": err}
    if err:
        return [], 0, rec
    try:
        items = parse_atoms(answer)
    except ValueError as exc:
        rec["error"] = f"bad answer: {exc}: {answer.strip()[:200]}"
        return [], 0, rec
    kept, dropped = check_atoms(items, msgs, conf.get("relay") or ())
    if conf.get("verify", True):
        kept, vdropped, vrec = verify_atoms(cmd, kept, msgs, conf)
        dropped += vdropped
        if vrec:
            rec["calls"] += 1
            rec["chars"] += vrec["chars"]
            rec["secs"] += vrec["secs"]
            for k, src in (("in", "input_tokens"), ("out", "output_tokens")):
                rec[k] = (rec[k] or 0) + (vrec["usage"].get(src) or 0)
            vc = vrec["usage"].get("cached_input_tokens") or vrec["usage"].get("cache_read_input_tokens") or 0
            rec["cached"] = (rec["cached"] or 0) + vc
            if vrec["error"]:
                rec["error"] = f"verify: {vrec['error']}"
    return kept, dropped, rec


def cmd_atoms(args):
    sources, path = load_config(args.hq)
    conf = atoms_config(args.hq)
    chat_sources = args.source or [s["name"] for s in sources if s.get("group") == "chat"]
    if not conf["model"] and not args.dry_run:
        print("hq.yaml has no `brain_model:` — the command that reads a prompt on stdin and answers", file=sys.stderr)
        return 3
    if not os.path.exists(path):
        print(f"no index at {path} — run `hq_brain.py index` first", file=sys.stderr)
        return 3
    db = connect(path, write=True)
    if args.doc:
        # Why: re-distilling a fixed sample (after a prompt change) must hit the same chats, skip list or not.
        wanted = set(args.doc)
        todo = [c for c in chat_candidates(db, chat_sources, [], redo=True, min_turns=0) if c[0] in wanted]
    else:
        todo = chat_candidates(db, chat_sources, conf["skip"], redo=args.redo)[:args.limit]
    jobs = []
    for cid, title, project in todo:
        msgs = chat_dialogue(db, cid, conf["budget"])
        if msgs:
            jobs.append((cid, title, msgs, project))
    if args.dry_run:
        for cid, title, msgs, _ in jobs:
            print(f"  {cid} · {len(msgs)} msgs · {len(render_chat(msgs, conf['relay']))} chars · {(title or '')[:70]}")
        print(f"{len(jobs)} chats would be distilled")
        return 0
    model = conf["model"]
    t0, totals = time.time(), {"chats": 0, "kept": 0, "dropped": 0, "calls": 0, "errors": 0, "in": 0, "cached": 0, "out": 0}
    with ThreadPoolExecutor(max_workers=max(1, min(args.jobs, 3))) as pool:
        futs = {pool.submit(distill, model, msgs, project, conf): (cid, title) for cid, title, msgs, project in jobs}
        for n, fut in enumerate(as_completed(futs), 1):
            cid, title = futs[fut]
            kept, dropped, rec = fut.result()
            if not rec["error"]:
                store_atoms(db, cid, kept, model)
            db.execute("INSERT OR REPLACE INTO atom_runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (cid, time.time(), model, "error" if rec["error"] else "ok", rec["calls"], rec["chars"],
                        rec["in"], rec["cached"], rec["out"], rec["secs"], len(kept), dropped, rec["error"]))
            db.commit()
            totals["chats"] += 1
            totals["calls"] += rec["calls"]
            totals["errors"] += bool(rec["error"])
            totals["kept"] += len(kept)
            totals["dropped"] += dropped
            for k in ("in", "cached", "out"):
                totals[k] += rec[k] or 0
            state = f"error: {rec['error'][:120]}" if rec["error"] else f"{len(kept)} atoms, {dropped} dropped"
            print(f"  [{n}/{len(jobs)}] {rec['secs']:.0f}s · {state} · {(title or '')[:60]}", flush=True)
    db.commit()
    try:
        db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except sqlite3.OperationalError:   # a reader holds the WAL; the next writer checkpoints
        pass
    db.close()
    wall = time.time() - t0
    print(f"atoms: {totals['chats']} chats, {totals['kept']} kept, {totals['dropped']} dropped by the checks, "
          f"{totals['errors']} errors · {totals['calls']} model calls · tokens in {totals['in']} (cached {totals['cached']}) out {totals['out']} · "
          f"{wall:.0f}s wall")
    return 2 if totals["errors"] else 0


# ---------------------------------------------------------------- questions

QUESTIONS_PROMPT = """These are knowledge atoms from one chat of the owner, %(owner)s, with an AI agent. Weeks later he
will look for them by typing a short question or a few words. For each atom write 2-3 such searches in %(lang)s,
the way he would type them: plain words and synonyms the claim does not use, the task id or name when there is
one, no new facts. Return ONLY JSON: {"questions":[{"i":N,"q":["...","..."]}]}

ATOMS:
%(atoms)s"""


def atom_body(claim, quote, entities_json, questions=()):
    try:
        ents = json.loads(entities_json or "[]")
    except ValueError:
        ents = []
    return " ".join([claim or "", quote or "", *[str(e) for e in ents], *questions])


def ask_questions(cmd, atoms, conf):
    """One model call for one chat's atoms → ({atom id: [questions]}, rec)."""
    listing = "\n".join(f"[{i}] {kind}: {claim}" for i, (_, kind, claim) in enumerate(atoms, 1))
    prompt = QUESTIONS_PROMPT % {"owner": conf.get("owner") or "the owner",
                                 "lang": conf.get("lang") or "the chats' language", "atoms": listing}
    answer, usage, secs, err = run_model(cmd, prompt)
    rec = {"usage": usage, "secs": secs, "error": err}
    if err:
        return {}, rec
    try:
        s = re.sub(r"^```(?:json)?\s*|\s*```$", "", answer.strip())
        data, _ = json.JSONDecoder().raw_decode(s[s.find("{"):])
        out = {}
        for item in data.get("questions") or []:
            i = int(item.get("i"))
            qs = [" ".join(str(q).split())[:160] for q in (item.get("q") or []) if str(q).strip()][:3]
            if 1 <= i <= len(atoms) and qs:
                out[atoms[i - 1][0]] = qs
        return out, rec
    except (ValueError, TypeError, AttributeError):
        rec["error"] = f"bad answer: {answer.strip()[:200]}"
        return {}, rec


def cmd_questions(args):
    """Doc2query: each atom gets the searches the owner would type, indexed with it."""
    _, path = load_config(args.hq)
    conf = atoms_config(args.hq)
    if not conf["model"]:
        print("hq.yaml has no `brain_model:`", file=sys.stderr)
        return 3
    db = connect(path, write=True)
    where = "" if args.redo else "AND a.id NOT IN (SELECT atom FROM atom_questions)"
    rows = db.execute(f"SELECT a.doc, a.id, a.kind, a.claim FROM atoms a WHERE 1=1 {where} ORDER BY a.doc, a.id").fetchall()
    by_doc = {}
    for doc, aid, kind, claim in rows:
        if not args.doc or doc in args.doc:
            by_doc.setdefault(doc, []).append((aid, kind, claim))
    jobs = list(by_doc.items())[:args.limit]
    t0, done, errors, tok = time.time(), 0, 0, {"in": 0, "cached": 0, "out": 0}
    with ThreadPoolExecutor(max_workers=max(1, min(args.jobs, 3))) as pool:
        futs = {pool.submit(ask_questions, conf["model"], atoms, conf): doc for doc, atoms in jobs}
        for fut in as_completed(futs):
            got, rec = fut.result()
            errors += bool(rec["error"])
            u = rec["usage"] or {}
            tok["in"] += u.get("input_tokens") or 0
            tok["out"] += u.get("output_tokens") or 0
            tok["cached"] += u.get("cached_input_tokens") or u.get("cache_read_input_tokens") or 0
            for aid, qs in got.items():
                claim, quote, ents = db.execute("SELECT claim, quote, entities FROM atoms WHERE id=?", (aid,)).fetchone()
                body = atom_body(claim, quote, ents, qs)
                db.execute("DELETE FROM atoms_fts WHERE rowid=?", (aid,))
                db.execute("INSERT INTO atoms_fts(rowid, stem, ids) VALUES (?,?,?)",
                           (aid, stem_text(body), " ".join(extract_ids(body))))
                db.execute("INSERT OR REPLACE INTO atom_questions VALUES (?,?,?,?)",
                           (aid, json.dumps(qs, ensure_ascii=False), conf["model"], time.time()))
                done += 1
            db.commit()
    db.close()
    print(f"questions: {len(jobs)} chats, {done} atoms got questions, {errors} errors · tokens in {tok['in']} "
          f"(cached {tok['cached']}) out {tok['out']} · {time.time() - t0:.0f}s wall")
    return 2 if errors else 0


# ---------------------------------------------------------------- push: a brief at chat start, a hint per message

BRIEF = (("decision", 21, 8), ("correction", 90, 5), ("preference", 90, 4), ("question", 21, 5), ("fix", 14, 4))
BRIEF_TITLE = {"decision": "Decisions", "correction": "The owner corrected the agent", "preference": "The owner's preferences",
               "question": "Open questions", "fix": "Error → fix"}
BRIEF_MIN_CHARS = 40    # «the owner chose option 1» says nothing without its chat
HINT_LIMIT = 3
HINT_TEXT = 2000        # chars of a message the hint reads (a long paste says what it is about early on)
HINT_COMMON = 0.02      # a word in more atoms than this share does not count towards a match
HINT_MIN_MATCH = 2      # distinct rare words of the message an atom must contain (an id counts as two)…
HINT_SHARE = 0.3        # …and this share of the message's rare words, so a long paste needs more…
HINT_MAX_NEED = 5       # …but never more than this
HINT_RAW_RARE = 0.001   # a lowercase word in fewer than this share of raw units is specific (a term, not «коротко»)…
HINT_SPECIFIC = 2       # …and an atom must share this many specific words, or one id with a digit (DEV-123, !19)
HINT_PROJECT = 1.5      # an atom of the chat's own project weighs this much more
HINT_MIN_SCORE = 25.0   # below it only a shared specific id keeps an atom; `brain_hint_min_score:` (bm25 scale:
                        # set on ~1k atoms and 439 messages judged by a second model — re-check as atoms grow)


def session_docs(db, sessions):
    """Canonical doc ids of the given session keys (a chat's atoms are never pushed back into it)."""
    keys = [s for s in sessions if s]
    if not keys:
        return set()
    return {r[0] for r in db.execute(
        f"SELECT COALESCE(canon, id) FROM docs WHERE key IN ({','.join('?' * len(keys))})", keys)}


def day(ts):
    return dt.datetime.fromtimestamp(ts).strftime("%d.%m")


def brief(db, names, sessions=(), now=None, max_lines=24):
    """What a new chat of this project should know: recent decisions, the owner's corrections and
    preferences, open questions, error→fix — newest first, one line each, no repeats."""
    if not has_atoms(db) or not names:
        return []
    now = now or time.time()
    skip = session_docs(db, sessions)
    names = sorted(names)
    out, seen = [], set()
    for kind, days, cap in BRIEF:
        rows = db.execute(
            "SELECT a.claim, a.ts, COALESCE(d.canon, d.id), d.title FROM atoms a JOIN docs d ON d.id = a.doc "
            f"WHERE a.kind = ? AND a.superseded_by IS NULL AND a.ts >= ? AND d.project IN ({','.join('?' * len(names))}) "
            "ORDER BY a.ts DESC", [kind, now - days * 86400, *names]).fetchall()
        lines = []
        for claim, ts, canon, title in rows:
            norm = re.sub(r"\W+", " ", claim.lower())[:80]
            if canon in skip or norm in seen or len(claim) < BRIEF_MIN_CHARS:
                continue
            seen.add(norm)
            lines.append({"kind": kind, "date": day(ts), "claim": claim, "chat": title})
            if len(lines) >= cap:
                break
        out.extend(lines)
    return out[:max_lines]


DATE_ID_RE = re.compile(r"^\d{1,4}(_\d{1,2}){1,2}(_\d{2,4})?$")   # 04.11, 2026-11-04: dates, not ids
PLACEHOLDER_RE = re.compile(r"\[(?:Image|Pasted text)[^\]]*\]|/(?:Users|home)/[^/\s]+/", re.I)


def hint_terms(db, text):
    """The message's words and ids that can tie it to an atom: {term: atom df} for stems and for ids
    (those in more than HINT_COMMON of atoms dropped), and the specific ones among them — ids, names
    written with a capital letter mid-sentence, and words rare in the raw chats too."""
    text = PLACEHOLDER_RE.sub(" ", text)
    ids = [i for i in extract_ids(text) if not DATE_ID_RE.match(i)]
    id_numbers = {p for i in ids for p in i.split("_") if p.isdigit()}
    stems, caps = [], set()
    for m in re.finditer(r"[^\W\d_][\w-]*", text):
        t = m.group(0).lower()
        if t in STOP or t in FILLER or t in id_numbers:
            continue
        s = stem(t)
        prev = text[:m.start()]
        before, after = prev.rstrip(), text[m.end():m.end() + 1]
        starts = not before or before[-1] in ".!?:;…\"«(/~-—•*>" or "\n" in prev[len(before):]
        cap = m.group(0)[0].isupper() and not starts and after != "/"
        if len(s) < (3 if cap else 4):       # «пол», «мен», «эт» match anything; a capitalised «Ann» is a name
            continue
        if s not in stems:
            stems.append(s)
        if cap:
            caps.add(s)
    terms = stems + ids
    if not terms:
        return {}, {}, set(), set()
    marks = ",".join("?" * len(terms))
    df = dict(db.execute(f"SELECT term, doc FROM atoms_v WHERE term IN ({marks})", terms))
    raw = dict(db.execute(f"SELECT term, doc FROM fts_v WHERE term IN ({marks})", terms))
    total = db.execute("SELECT count(*) FROM atoms").fetchone()[0] or 1
    units = int((db.execute("SELECT v FROM meta WHERE k='units'").fetchone() or [0])[0] or 0) or 1
    cap = max(2, HINT_COMMON * total)
    rare = {s: df[s] for s in stems if df.get(s) and df[s] <= cap}
    rare_ids = {i: df[i] for i in ids if df.get(i) and df[i] <= cap and "https" not in i}
    rare_raw = HINT_RAW_RARE * units
    strong = {i for i in rare_ids if re.search(r"\d", i)}
    specific = {i for i in rare_ids if raw.get(i, 0) <= rare_raw} | {s for s in rare if s in caps or raw.get(s, 0) <= rare_raw}
    return rare, rare_ids, specific | strong, strong


def typed_by_owner(text, skip=()):
    """False for what the owner did not type: a message starting with a configured marker (an injected
    notice, a relayed reply), one that contains a skip phrase (an automation's prompt), a diff, or code."""
    head = text.lstrip()[:400].lower()
    if any(head.startswith(m.lower()) for m in skip) or any(m.lower() in text[:HINT_TEXT].lower() for m in skip):
        return False
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return False
    code = sum(1 for ln in lines if re.match(
        r"\s*(\+|@@|-{3}|-(?=\S|\s{2})|[{}()\[\];]+$|(def|class|import|from|return|const|let|function|export)\b)", ln))
    return "=== DIFF" not in text and code / len(lines) < 0.3


def recalls(text, cues=()):
    """True when the message points back at something said before («мы же решили», "didn't we") —
    `brain_recall_cues:` in hq.yaml, regular expressions in the owner's language."""
    return any(re.search(c, text[:HINT_TEXT], re.I) for c in cues)


def hint(db, text, names=(), sessions=(), before=None, limit=HINT_LIMIT, now=None, cues=(), skip_atoms=(),
         min_score=HINT_MIN_SCORE):
    """Atoms worth putting in front of the agent for this message: at most `limit`, one per chat. An
    atom must share HINT_MIN_MATCH rare words (more for a long message) of which HINT_SPECIFIC are
    specific, or one id with a digit. When the message recalls the past (`cues`), one specific word is
    enough — that is when the owner needs the memory most. Empty when nothing is close enough."""
    if not text or not has_atoms(db):
        return []
    rare, ids, specific, strong = hint_terms(db, text[:HINT_TEXT])
    weight = len(rare) + 2 * len(ids)
    recall = recalls(text, cues)
    need = HINT_MIN_MATCH if recall else max(HINT_MIN_MATCH, min(HINT_MAX_NEED, math.ceil(HINT_SHARE * weight)))
    need_specific = 1 if recall else HINT_SPECIFIC
    if weight < need or not specific:
        return []
    parts = []
    if rare:
        parts.append("stem : (" + " OR ".join(fts_quote(s) for s in rare) + ")")
    if ids:
        parts.append("ids : (" + " OR ".join(fts_quote(i) for i in ids) + ")")
    sql = f"SELECT rowid, bm25(atoms_fts, {COLS[0]}, {COLS[1]}) FROM atoms_fts WHERE atoms_fts MATCH ?"
    params = [" OR ".join(parts)]
    if before is not None:
        sql += " AND rowid < ?"
        params.append(rowid_floor(before))
    cands = dict(db.execute(sql + " ORDER BY 2 LIMIT 40", params).fetchall())
    if not cands:
        return []
    skip = session_docs(db, sessions)
    ref = before if before is not None else (now or time.time())
    scored = []
    for aid, kind, claim, quote, ents, ts, conf, sup, canon, proj, title, qs in db.execute(
            "SELECT a.id, a.kind, a.claim, a.quote, a.entities, a.ts, a.confidence, a.superseded_by, "
            "COALESCE(d.canon, d.id), d.project, d.title, q.questions FROM atoms a JOIN docs d ON d.id = a.doc "
            "LEFT JOIN atom_questions q ON q.atom = a.id "
            f"WHERE a.id IN ({','.join('?' * len(cands))})", list(cands)):
        if sup or canon in skip or aid in skip_atoms or (before is not None and ts >= before):
            continue
        body = atom_body(claim, quote, ents, json.loads(qs) if qs else ())
        have = set(stem_text(body).split())
        matched = [s for s in rare if s in have]
        id_hit = [i for i in ids if i in set(extract_ids(body))]
        if len(matched) + 2 * len(id_hit) < need:
            continue
        if not strong.intersection(id_hit) and len(specific.intersection(matched + id_hit)) < need_specific:
            continue                  # everyday words in common are a coincidence, not a memory
        half = ATOM_HALF_LIFE.get(kind)
        age = (ref - ts) / 86400
        score = -cands[aid] * ATOM_KIND_WEIGHT.get(kind, 1.0) * (0.5 + 0.5 * (conf or 0.5)) \
            * (0.5 ** (age / half) if half and age > 0 else 1.0) * (HINT_PROJECT if proj in names else 1.0)
        if score < min_score and not specific.intersection(id_hit):
            continue                  # a weak match is noise unless the same task, MR or field is named
        scored.append((score, {"id": aid, "kind": kind, "date": day(ts), "ts": iso(ts), "claim": claim, "chat": title,
                               "project": proj, "doc": canon, "matched": matched + id_hit, "score": round(score, 3)}))
    scored.sort(key=lambda x: -x[0])
    out, docs = [], set()
    for _, h in scored:
        if h["doc"] in docs:          # one atom per chat: three from one chat are one memory, not three
            continue
        docs.add(h["doc"])
        out.append(h)
        if len(out) >= limit:
            break
    return out


def push_scope(args):
    """(db, project names, sessions) for brief/hint — the project from --project or from --cwd."""
    _, path = load_config(args.hq)
    db = connect(path)
    slug = args.project or (project_of_path(read_registry(args.hq), worktree_roots(args.hq), args.cwd)
                            if args.cwd else None)
    return db, slug, (project_names(args.hq, slug) if slug else set()), args.session or []


def cmd_brief(args):
    try:
        db, slug, names, sessions = push_scope(args)
    except FileNotFoundError:
        return 3
    lines = brief(db, names, sessions, now=parse_ts(args.now) if args.now else None, max_lines=args.lines)
    if args.json:
        print(json.dumps({"project": slug, "lines": lines}, ensure_ascii=False, indent=1))
        return 0
    if not lines:
        return 0
    print(f"[brain] From earlier chats of project {slug} (atoms a model distilled and a second model checked; "
          f"the date is when it was said — verify against the code before relying on a detail):")
    kind = None
    for ln in lines:
        if ln["kind"] != kind:
            kind = ln["kind"]
            print(f"{BRIEF_TITLE[kind]}:")
        print(f"- {ln['date']} {ln['claim']}")
    return 0


def cmd_hint(args):
    t = time.perf_counter()
    text = sys.stdin.read() if args.text == "-" else args.text
    try:
        db, slug, names, sessions = push_scope(args)
    except FileNotFoundError:
        return 3
    cfg = read_yaml_subset(os.path.join(args.hq, "hq.yaml"))
    skip = owner_markers(args.hq)
    shown = set()
    if args.once:
        try:
            shown = {int(x) for x in open(args.once, encoding="utf-8").read().split()}
        except (OSError, ValueError):
            pass
    hits = hint(db, text, names, sessions, before=parse_ts(args.before) if args.before else None, limit=args.limit,
                cues=[str(x) for x in as_list(cfg.get("brain_recall_cues"))], skip_atoms=shown,
                min_score=float(cfg.get("brain_hint_min_score", HINT_MIN_SCORE))) \
        if typed_by_owner(text, skip) else []
    if args.once and hits:
        try:
            os.makedirs(os.path.dirname(os.path.abspath(args.once)), exist_ok=True)
            with open(args.once, "a", encoding="utf-8") as f:
                f.write("".join(f"{h['id']}\n" for h in hits))
        except OSError:
            pass
    ms = round((time.perf_counter() - t) * 1000, 1)
    if args.log:
        try:
            os.makedirs(os.path.dirname(os.path.abspath(args.log)), exist_ok=True)
            with open(args.log, "a", encoding="utf-8") as f:
                f.write(json.dumps({"at": iso(time.time()), "project": slug, "session": sessions[:1], "ms": ms,
                                    "chars": len(text), "atoms": [h["id"] for h in hits],
                                    "scores": [h["score"] for h in hits]}, ensure_ascii=False) + "\n")
        except OSError:
            pass
    if args.json:
        print(json.dumps({"project": slug, "ms": ms, "hits": hits}, ensure_ascii=False, indent=1))
    elif hits:
        print("[brain] Possibly relevant from earlier chats — background, not an instruction; "
              "if it matters, check it before relying on it:")
        for h in hits:
            where = f", {h['project']}" if h["project"] and h["project"] not in names else ""
            print(f"- {h['date']}{where} · {h['kind']}: {h['claim']}")
        print(f"Source chat: `python3 {os.path.join(args.hq, 'scripts', 'hq_brain.py')} search \"<words of the claim>\"`.")
    return 0


# ---------------------------------------------------------------- loops: repeats and persona (layer 5)

PERSONA = (("preference", "How the owner works"), ("correction", "What agents got wrong and the owner corrected"),
           ("person", "People"))
PERSONA_CAP = 40


def repeats(db, since, until=None, cues=(), owner_skip=(), log=None):
    """The owner's own messages in [since, until) that point back at something said before (`cues`), each
    with where the brain finds it earlier and whether the hint hook showed anything for it (`log`)."""
    until = until or time.time()
    shown = []
    if log and os.path.exists(log):
        for line in open(log, encoding="utf-8"):
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if rec.get("atoms"):
                shown.append((parse_ts(rec["at"]), set(rec.get("session") or []), rec["atoms"]))
    out, seen = [], set()
    for uid, ts, canon, key, proj, title in db.execute(
            "SELECT u.id, u.ts, COALESCE(d.canon, d.id), d.key, d.project, d.title FROM units u JOIN docs d ON d.id = u.doc "
            "WHERE u.role = 'user' AND d.canon IS NULL AND u.ts >= ? AND u.ts < ? ORDER BY u.ts", (since, until)).fetchall():
        if getattr(owner_skip, "sources", None) and db.execute("SELECT source FROM docs WHERE id=?", (canon,)).fetchone()[0] \
                not in owner_skip.sources:
            continue
        text = unit_text(db, uid)
        if not recalls(text, cues) or not typed_by_owner(text, owner_skip) or text[:120] in seen:
            continue
        seen.add(text[:120])
        aliases = {key} | {r[0] for r in db.execute("SELECT key FROM docs WHERE canon=?", (canon,))}
        earlier = search(db, text[:HINT_TEXT], before=ts, limit=2, exclude={canon})
        hinted = [a for at, sess, atoms in shown if abs(at - ts) <= 180 and sess & aliases for a in atoms]
        out.append({"at": iso(ts), "project": proj, "chat": title, "key": key, "message": text[:600],
                     "hinted": hinted,
                     "earlier": [{k: h[k] for k in ("ts", "project", "title", "key", "snippet")} for h in earlier]})
    return out


def persona(db, cap=PERSONA_CAP):
    """The owner as the atoms know him: preferences, corrections, people — newest first, near-repeats once."""
    sections = []
    for kind, heading in PERSONA:
        lines, seen = [], []
        for claim, ts, proj in db.execute(
                "SELECT a.claim, a.ts, d.project FROM atoms a JOIN docs d ON d.id = a.doc "
                "WHERE a.kind = ? AND a.superseded_by IS NULL ORDER BY a.ts DESC", (kind,)):
            stems = set(stem_text(claim).split())
            if any(len(stems & s) >= 0.6 * max(1, min(len(stems), len(s))) for s in seen):
                continue                  # the same thing said again in another chat
            seen.append(stems)
            lines.append({"claim": claim, "date": dt.datetime.fromtimestamp(ts).strftime("%d.%m.%Y"), "project": proj})
            if len(lines) >= cap:
                break
        sections.append({"kind": kind, "heading": heading, "lines": lines})
    return sections


def cmd_repeats(args):
    _, path = load_config(args.hq)
    try:
        db = connect(path)
    except FileNotFoundError:
        return 3
    cfg = read_yaml_subset(os.path.join(args.hq, "hq.yaml"))
    since = parse_ts(args.since) if args.since else time.time() - 86400
    rows = repeats(db, since, parse_ts(args.until) if args.until else None,
                   cues=[str(x) for x in as_list(cfg.get("brain_repeat_cues") or cfg.get("brain_recall_cues"))],
                   owner_skip=owner_markers(args.hq),
                   log=args.log)
    if args.json:
        print(json.dumps({"since": iso(since), "count": len(rows), "hinted": sum(bool(r["hinted"]) for r in rows),
                          "repeats": rows}, ensure_ascii=False, indent=1))
        return 0
    for r in rows:
        print(f"{r['at'][:16]} {r['project'] or '-'}{' · hint shown' if r['hinted'] else ''}: {r['message'][:200]}")
        for e in r["earlier"]:
            print(f"   earlier {(e['ts'] or '')[:10]} {e['project'] or '-'} — {e['title']}: {e['snippet'][:160]}")
    print(f"({len(rows)} messages recalling the past, a hint on {sum(bool(r['hinted']) for r in rows)})")
    return 0


def cmd_persona(args):
    _, path = load_config(args.hq)
    try:
        db = connect(path)
    except FileNotFoundError:
        return 3
    owner = read_yaml_subset(os.path.join(args.hq, "hq.yaml")).get("brain_owner") or "the owner"
    sections = persona(db, args.limit)
    if args.json:
        print(json.dumps(sections, ensure_ascii=False, indent=1))
        return 0
    n = db.execute("SELECT count(*) FROM atoms").fetchone()[0]
    out = [f"# {owner}: portrait from the brain", "",
           f"Built by `hq_brain.py persona` from {n} atoms (claims distilled from chats and "
           "checked by a second model). Generated whole — do not edit; the date is when it was said.", ""]
    for sec in sections:
        if sec["lines"]:
            out += [f"## {sec['heading']}", ""] + [f"- {ln['claim']} ({ln['date']}, {ln['project'] or '-'})"
                                                   for ln in sec["lines"]] + [""]
    text = "\n".join(out)
    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"persona: {args.out} · {sum(len(s['lines']) for s in sections)} lines")
    else:
        print(text)
    return 0


# ---------------------------------------------------------------- eval

def source_keys(src):
    """Keys a gold source may match: each token of `ref`, with/without its `label:`, `#…` and `:line` cut."""
    keys, paths = set(), set()
    for tok in str(src.get("ref") or "").split(" (")[0].split():
        if tok.startswith("@") or tok.startswith("("):
            continue
        tok = tok.split("#")[0]
        tok = re.sub(r":\d+$", "", tok)
        keys.add(tok)
        if ":" in tok:
            keys.add(tok.split(":", 1)[1])
        if "/" in tok:
            paths.add(tok)
    for k in src.get("keys") or []:
        keys.add(str(k))
    return keys, paths


def doc_keys(hit):
    out = set()
    for k in [hit["key"], *hit["aliases"]]:
        out.add(k)
        if ":" in k:
            out.add(k.split(":", 1)[1])
    return out


def matches(src, hit):
    keys, paths = source_keys(src)
    dk = doc_keys(hit)
    if keys & dk:
        return True
    return any(k == p or k.endswith("/" + p) for p in paths for k in dk)


def cmd_eval(args):
    _, path = load_config(args.hq)
    try:
        db = connect(path)
    except FileNotFoundError:
        print(f"no index at {path} — run `hq_brain.py index` first", file=sys.stderr)
        return 3
    with open(args.questions, encoding="utf-8") as f:
        data = parse_yaml(f.read())
    questions = data.get("questions") if isinstance(data, dict) else data
    fields = [("typed", "question"), ("keywords", "keywords")]
    rows, times = [], []
    totals = {f: {"strict": 0, "lenient": 0, "asked": 0} for f, _ in fields}
    for q in questions:
        asked = parse_ts(q.get("asked_at"))
        row = {"id": q.get("id"), "asked_at": q.get("asked_at")}
        for label, field in fields:
            text = q.get(field)
            if not text:
                row[label] = None
                continue
            t = time.perf_counter()
            hits = search(db, text, before=None if args.no_before else asked, limit=args.limit, atoms=not args.no_atoms)
            times.append((time.perf_counter() - t) * 1000)
            strict = lenient = None
            for h in hits:
                for s in q.get("sources") or []:
                    if not matches(s, h):
                        continue
                    lenient = lenient or h["rank"]
                    own = q.get("asked_in") and q["asked_in"] in doc_keys(h)
                    if not own or (asked and h["ts"] and parse_ts(h["ts"]) < asked):
                        strict = strict or h["rank"]
            totals[label]["asked"] += 1
            totals[label]["strict"] += bool(strict)
            totals[label]["lenient"] += bool(lenient)
            row[label] = {"strict": strict, "lenient": lenient, "top": [h["key"] for h in hits[:3]]}
        rows.append(row)
    times.sort()
    lat = {"median_ms": round(statistics.median(times), 1) if times else None,
           "p95_ms": round(times[min(len(times) - 1, math.ceil(0.95 * len(times)) - 1)], 1) if times else None,
           "queries": len(times)}
    result = {"before_filter": not args.no_before, "atoms": not args.no_atoms, "limit": args.limit, "totals": totals, "latency": lat,
              "questions": rows}
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=1))
        return 0
    n = len(rows)
    print(f"recall@{args.limit} over {n} questions ({'before asked_at' if not args.no_before else 'no date filter'}"
          f"{', atoms off' if args.no_atoms else ''})")
    for label, _ in fields:
        t = totals[label]
        print(f"  {label:9s} strict {t['strict']}/{t['asked']} · lenient {t['lenient']}/{t['asked']}")
    print(f"  latency median {lat['median_ms']} ms · p95 {lat['p95_ms']} ms · {lat['queries']} queries")
    for r in rows:
        cells = []
        for label, _ in fields:
            c = r.get(label)
            cells.append("-" if not c else (str(c["strict"]) if c["strict"] else (f"({c['lenient']})" if c["lenient"] else "—")))
        print(f"  {r['id']}: typed {cells[0]:>4} · keywords {cells[1]:>4}")
    return 0


def cmd_stats(args):
    _, path = load_config(args.hq)
    try:
        db = connect(path)
    except FileNotFoundError:
        print(f"no index at {path}", file=sys.stderr)
        return 3
    per = db.execute("SELECT d.source, count(DISTINCT u.doc), count(*) FROM units u JOIN docs d ON d.id=u.doc "
                     "GROUP BY 1 ORDER BY 3 DESC").fetchall()
    dups = db.execute("SELECT count(*) FROM units WHERE dup IS NOT NULL").fetchone()[0]
    out = {"index": path, "mb": round(os.path.getsize(path) / 1048576, 1),
           "sources": [{"source": s, "docs": d, "units": u} for s, d, u in per], "repeat_units": dups}
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='atom_runs'").fetchone():
        out["atoms"] = dict(db.execute("SELECT kind, count(*) FROM atoms GROUP BY 1 ORDER BY 2 DESC").fetchall())
        r = db.execute("SELECT count(*), sum(status='error'), sum(kept), sum(dropped), sum(in_tokens), "
                       "sum(cached_tokens), sum(out_tokens), sum(secs) FROM atom_runs").fetchone()
        out["atom_runs"] = dict(zip(("chats", "errors", "kept", "dropped", "in_tokens", "cached_tokens",
                                     "out_tokens", "model_secs"), r))
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=1))
    else:
        print(f"{path} · {out['mb']} MB · {dups} repeat units")
        for s in out["sources"]:
            print(f"  {s['source']}: {s['docs']} docs, {s['units']} units")
        if out.get("atom_runs"):
            print(f"  atoms: {out['atoms']} · runs {out['atom_runs']}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--hq", default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("index")
    p.add_argument("--full", action="store_true", help="re-read every source")
    p.add_argument("--source", help="only this source name")
    p = sub.add_parser("search")
    p.add_argument("text")
    p.add_argument("--before", help="only what was written before this ISO time")
    p.add_argument("--project")
    p.add_argument("--limit", type=int, default=8)
    p.add_argument("--json", action="store_true")
    p.add_argument("--no-atoms", action="store_true", help="raw units only")
    p.add_argument("--role", choices=["user", "assistant"], help="only units of this role (user: the owner's own words)")
    p.add_argument("--since", help="only what was written after this ISO time")
    p = sub.add_parser("atoms")
    p.add_argument("--limit", type=int, default=20, help="chats to distill this run")
    p.add_argument("--source", action="append", help="chat source name (repeat); default: group chat")
    p.add_argument("--jobs", type=int, default=1, help="parallel model calls, at most 3")
    p.add_argument("--dry-run", action="store_true", help="list the chats and prompt sizes, call nothing")
    p.add_argument("--redo", action="store_true", help="also chats already distilled")
    p.add_argument("--doc", type=int, action="append", help="only this chat id (repeat); implies --redo")
    p = sub.add_parser("questions")
    p.add_argument("--limit", type=int, default=1000, help="chats to cover this run")
    p.add_argument("--jobs", type=int, default=1, help="parallel model calls, at most 3")
    p.add_argument("--redo", action="store_true", help="also atoms that already have questions")
    p.add_argument("--doc", type=int, action="append", help="only this chat id (repeat)")
    p = sub.add_parser("eval")
    p.add_argument("questions")
    p.add_argument("--limit", type=int, default=8)
    p.add_argument("--no-before", action="store_true", help="do not hide what was written after the question")
    p.add_argument("--json", action="store_true")
    p.add_argument("--no-atoms", action="store_true", help="raw units only")
    p = sub.add_parser("stats")
    p.add_argument("--json", action="store_true")
    for name, help_ in (("brief", "a new chat's brief"), ("hint", "atoms for one message")):
        p = sub.add_parser(name, help=help_)
        if name == "hint":
            p.add_argument("text", help="the message, or - for stdin")
            p.add_argument("--before", help="only atoms said before this ISO time (eval)")
            p.add_argument("--limit", type=int, default=HINT_LIMIT)
            p.add_argument("--log", help="append one JSON line per call (ms, atom ids) to this file")
            p.add_argument("--once", help="a file of atom ids already shown in this chat: skipped, and new ones added")
        else:
            p.add_argument("--lines", type=int, default=24)
            p.add_argument("--now", help="count the recent window back from this ISO time (default: now)")
        p.add_argument("--project", help="registry slug")
        p.add_argument("--cwd", help="the chat's folder, when the slug is not known")
        p.add_argument("--session", action="append", help="this chat's session key (repeat); its own atoms are skipped")
        p.add_argument("--json", action="store_true")
    p = sub.add_parser("repeats", help="the owner's messages that recall the past, with where it was said before")
    p.add_argument("--since", help="ISO time (default: 24 hours ago)")
    p.add_argument("--until", help="ISO time (default: now)")
    p.add_argument("--log", help="the hint hook's log, to tell which of them got a hint")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("persona", help="the owner's portrait from atoms: preferences, corrections, people")
    p.add_argument("--out", help="write markdown here")
    p.add_argument("--limit", type=int, default=PERSONA_CAP, help="lines per section")
    p.add_argument("--json", action="store_true")
    for name in ("index", "search", "atoms", "questions", "eval", "stats", "brief", "hint", "repeats", "persona"):
        sub.choices[name].add_argument("--hq", default=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    args.hq = os.path.abspath(os.path.expanduser(args.hq))
    try:
        return {"index": cmd_index, "search": cmd_search, "atoms": cmd_atoms, "questions": cmd_questions, "eval": cmd_eval,
                "stats": cmd_stats, "brief": cmd_brief, "hint": cmd_hint, "repeats": cmd_repeats,
                "persona": cmd_persona}[args.cmd](args)
    except ValueError as exc:
        print(f"hq.yaml: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
