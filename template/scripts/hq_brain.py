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

Usage:
  python3 scripts/hq_brain.py index  [--hq DIR] [--full] [--source NAME]
  python3 scripts/hq_brain.py search "<text>" [--before ISO] [--project P] [--limit 8] [--json]
  python3 scripts/hq_brain.py eval   questions.yaml [--limit 8] [--no-before] [--json]
  python3 scripts/hq_brain.py stats  [--json]
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
import time
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hq_registry import read_yaml_subset  # noqa: E402  (same folder)
from hq_stem import stem, stem_en, translit, words  # noqa: E402

VERSION = "0.5.0"
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
        """A slug as is; a path → the registry slug of the longest matching project path, else its basename."""
        if not value:
            return None
        value = str(value)
        if not value.startswith(("/", "~")):
            return value
        path = os.path.expanduser(value)
        best = None
        for p in self.registry:
            root = os.path.expanduser(p["path"]).rstrip("/")
            if (path == root or path.startswith(root + "/")) and (best is None or len(root) > len(best[0])):
                best = (root, p.get("slug") or os.path.basename(root))
        return best[1] if best else os.path.basename(path.rstrip("/"))

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
            if head.startswith('{"type":"'):      # metadata rows; only titles matter
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
                if kind == "user" and first_user is None:
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
        if auto is None and role == "user":
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
    session, cwd, msgs = None, None, []
    try:
        for line in open_text(path):
            if '"session_meta"' in line[:160]:
                p = json.loads(line).get("payload") or {}
                session, cwd = session or p.get("id"), cwd or p.get("cwd")
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
    session = session or os.path.basename(path)[:-6]
    first = next((t for _, r, t in msgs if r == "user"), msgs[0][2])
    did = ix.doc(session, src["name"], project=cwd or src.get("project"),
                 title=first.strip().splitlines()[0][:100], link=path)
    auto = None
    for ts, role, text in msgs:
        share = ix.add(src, did, ts, text, role=role, file_id=fid)
        if auto is None and role == "user":
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
    db.execute("INSERT OR REPLACE INTO meta VALUES ('indexed_at', ?)", (str(time.time()),))
    db.execute("INSERT OR REPLACE INTO meta VALUES ('units', (SELECT count(*) FROM units))")
    db.commit()
    db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    db.close()
    print(f"index: {path} · {os.path.getsize(path) / 1048576:.0f} MB · {time.time() - t0:.0f}s")
    return 2 if failed else 0


# ---------------------------------------------------------------- search

def query_terms(db, text):
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


def search(db, text, before=None, project=None, limit=8, now=None):
    """Top documents for `text`: list of dicts (key, aliases, source, project, title, link, ts, score, snippet)."""
    stems, ids = query_terms(db, text)
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
    pool = POOL * (5 if project else 1)
    sql += " ORDER BY r LIMIT ?"
    params.append(pool)
    cands = db.execute(sql, params).fetchall()
    if not cands:
        return []
    raw = dict(cands)
    ref = before if before is not None else (now or time.time())
    rows = db.execute(
        "SELECT u.id, u.ts, u.kind, u.weight, u.dup, COALESCE(d.canon, d.id), d.weight FROM units u "
        f"JOIN docs d ON d.id = u.doc WHERE u.id IN ({','.join('?' * len(raw))})", list(raw)).fetchall()
    by_doc = {}
    for uid, ts, kind, w, dup, canon, dw in rows:
        score = -raw[uid] * (w or 1.0) * (dw or 1.0) * TYPE_WEIGHT.get(kind, 1.0) * decay((ref - ts) / 86400, kind)
        by_doc.setdefault(canon, []).append((score, uid, ts, dup))
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
        body = zlib.decompress(db.execute("SELECT text FROM units WHERE id=?", (best[1],)).fetchone()[0]).decode()
        aliases = [r[0] for r in db.execute("SELECT key FROM docs WHERE canon=?", (canon,))]
        dup = db.execute("SELECT COALESCE(c.key, d.key) FROM docs d LEFT JOIN docs c ON c.id = d.canon "
                         "WHERE d.id=?", (best[3],)).fetchone() if best[3] else None
        out.append({"rank": len(out) + 1, "key": key, "aliases": aliases, "source": source, "project": proj,
                    "title": title, "link": link, "ts": iso(best[2]), "score": round(score, 4),
                    "units": len(units), "repeat_of": dup[0] if dup else None,
                    "snippet": snippet(body, stemset, ids)})
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
    hits = search(db, args.text, before=before, project=args.project, limit=args.limit)
    ms = (time.perf_counter() - t) * 1000
    if args.json:
        print(json.dumps({"query": args.text, "before": args.before, "ms": round(ms, 1), "hits": hits},
                         ensure_ascii=False, indent=1))
        return 0
    for h in hits:
        print(f"{h['rank']}. {(h['ts'] or '')[:16]} [{h['source']}] {h['project'] or '-'} — {h['title']}")
        print(f"   {h['key']}" + (f" (also {', '.join(h['aliases'][:3])})" if h["aliases"] else "")
              + (f" · repeat of {h['repeat_of']}" if h["repeat_of"] else ""))
        print(f"   {h['snippet']}")
    print(f"({len(hits)} hits, {ms:.0f} ms)")
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
            hits = search(db, text, before=None if args.no_before else asked, limit=args.limit)
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
    result = {"before_filter": not args.no_before, "limit": args.limit, "totals": totals, "latency": lat,
              "questions": rows}
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=1))
        return 0
    n = len(rows)
    print(f"recall@{args.limit} over {n} questions ({'before asked_at' if not args.no_before else 'no date filter'})")
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
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=1))
    else:
        print(f"{path} · {out['mb']} MB · {dups} repeat units")
        for s in out["sources"]:
            print(f"  {s['source']}: {s['docs']} docs, {s['units']} units")
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
    p = sub.add_parser("eval")
    p.add_argument("questions")
    p.add_argument("--limit", type=int, default=8)
    p.add_argument("--no-before", action="store_true", help="do not hide what was written after the question")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("stats")
    p.add_argument("--json", action="store_true")
    for name in ("index", "search", "eval", "stats"):
        sub.choices[name].add_argument("--hq", default=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    args.hq = os.path.abspath(os.path.expanduser(args.hq))
    try:
        return {"index": cmd_index, "search": cmd_search, "eval": cmd_eval, "stats": cmd_stats}[args.cmd](args)
    except ValueError as exc:
        print(f"hq.yaml: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
