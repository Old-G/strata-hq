#!/usr/bin/env python3
"""Snowball stemmers for Russian and English in plain Python, plus the tokenizer the brain shares.

Russian follows the Snowball "russian" algorithm (perfective gerund, reflexive, adjectival, verb,
noun, derivational, superlative, tidy-up; ё → е). English follows Snowball "english" (Porter2).
Both are written from the published algorithm descriptions; no third-party code or data.

  stem("ожиданиях") == stem("ожидание") == "ожидан"
  stem("snippets") == "snippet"

Stdlib only. Used by hq_brain.py for both the index and the query, so a word form typed in a
question meets the forms written in the sources.
"""
import re
from functools import lru_cache

# ---------------------------------------------------------------- Russian

_RU_V = frozenset("аеиоуыэюя")


def _by_len(*words):
    return tuple(sorted(words, key=len, reverse=True))


_PG1 = _by_len("в", "вши", "вшись")                       # after а / я
_PG2 = _by_len("ив", "ивши", "ившись", "ыв", "ывши", "ывшись")
_ADJ = _by_len("ее", "ие", "ые", "ое", "ими", "ыми", "ей", "ий", "ый", "ой", "ем", "им", "ым", "ом",
               "его", "ого", "ему", "ому", "их", "ых", "ую", "юю", "ая", "яя", "ою", "ею")
_PART1 = _by_len("ем", "нн", "вш", "ющ", "щ")             # after а / я
_PART2 = _by_len("ивш", "ывш", "ующ")
_REFL = _by_len("ся", "сь")
_VERB1 = _by_len("ла", "на", "ете", "йте", "ли", "й", "л", "ем", "н", "ло", "но", "ет", "ют", "ны",
                 "ть", "ешь", "нно")                       # after а / я
_VERB2 = _by_len("ила", "ыла", "ена", "ейте", "уйте", "ите", "или", "ыли", "ей", "уй", "ил", "ыл", "им",
                 "ым", "ен", "ило", "ыло", "ено", "ят", "ует", "уют", "ит", "ыт", "ены", "ить", "ыть",
                 "ишь", "ую", "ю")
_NOUN = _by_len("а", "ев", "ов", "ие", "ье", "е", "иями", "ями", "ами", "еи", "ии", "и", "ией", "ей",
                "ой", "ий", "й", "иям", "ям", "ием", "ем", "ам", "ом", "о", "у", "ах", "иях", "ях", "ы",
                "ь", "ию", "ью", "ю", "ия", "ья", "я")
_DERIV = _by_len("ост", "ость")
_TIDY = _by_len("ейш", "ейше", "н", "ь")


def _suffix(w, endings, limit):
    """The longest of `endings` that ends `w` and starts at or after `limit`."""
    for e in endings:
        if w.endswith(e) and len(w) - len(e) >= limit:
            return e
    return None


def _ru_group(w, g1, g2, rv):
    """Snowball `among` over two groups: g1 endings must follow а/я (kept). None = no match."""
    s1, s2 = _suffix(w, g1, rv), _suffix(w, g2, rv)
    if s1 and (not s2 or len(s1) > len(s2)):
        i = len(w) - len(s1)
        return w[:i] if i - 1 >= rv and w[i - 1] in "ая" else None
    if s2:
        return w[:len(w) - len(s2)]
    return None


def _ru_regions(w):
    rv = next((i + 1 for i, c in enumerate(w) if c in _RU_V), len(w))
    r1 = next((i + 1 for i in range(1, len(w)) if w[i] not in _RU_V and w[i - 1] in _RU_V), len(w))
    r2 = next((i + 1 for i in range(r1 + 1, len(w)) if w[i] not in _RU_V and w[i - 1] in _RU_V), len(w))
    return rv, r2


def stem_ru(word):
    w = word.lower().replace("ё", "е")
    rv, r2 = _ru_regions(w)
    # step 1
    cut = _ru_group(w, _PG1, _PG2, rv)
    if cut is not None:
        w = cut
    else:
        s = _suffix(w, _REFL, rv)
        if s:
            w = w[:-len(s)]
        s = _suffix(w, _ADJ, rv)
        if s:
            w = w[:-len(s)]
            cut = _ru_group(w, _PART1, _PART2, rv)
            if cut is not None:
                w = cut
        else:
            cut = _ru_group(w, _VERB1, _VERB2, rv)
            if cut is not None:
                w = cut
            else:
                s = _suffix(w, _NOUN, rv)
                if s:
                    w = w[:-len(s)]
    # step 2
    if w.endswith("и") and len(w) - 1 >= rv:
        w = w[:-1]
    # step 3
    s = _suffix(w, _DERIV, rv)
    if s and len(w) - len(s) >= r2:
        w = w[:-len(s)]
    # step 4
    s = _suffix(w, _TIDY, rv)
    if s in ("ейш", "ейше"):
        w = w[:-len(s)]
        if w.endswith("нн") and len(w) - 2 >= rv:
            w = w[:-1]
    elif s == "н":
        if w.endswith("нн") and len(w) - 2 >= rv:
            w = w[:-1]
    elif s == "ь":
        w = w[:-1]
    return w


# ---------------------------------------------------------------- English (Porter2)

_EN_V = frozenset("aeiouy")
_EN_EXC1 = {"skis": "ski", "skies": "sky", "dying": "die", "lying": "lie", "tying": "tie", "idly": "idl",
            "gently": "gentl", "ugly": "ugli", "early": "earli", "only": "onli", "singly": "singl",
            "sky": "sky", "news": "news", "howe": "howe", "atlas": "atlas", "cosmos": "cosmos",
            "bias": "bias", "andes": "andes"}
_EN_EXC2 = frozenset(("inning", "outing", "canning", "herring", "earring", "proceed", "exceed", "succeed"))
_DOUBLES = ("bb", "dd", "ff", "gg", "mm", "nn", "pp", "rr", "tt")
_LI_END = frozenset("cdeghkmnrt")
_STEP2 = (("ization", "ize"), ("ational", "ate"), ("fulness", "ful"), ("ousness", "ous"),
          ("iveness", "ive"), ("tional", "tion"), ("biliti", "ble"), ("lessli", "less"),
          ("entli", "ent"), ("ation", "ate"), ("alism", "al"), ("aliti", "al"), ("ousli", "ous"),
          ("iviti", "ive"), ("fulli", "ful"), ("enci", "ence"), ("anci", "ance"), ("abli", "able"),
          ("izer", "ize"), ("ator", "ate"), ("alli", "al"), ("bli", "ble"), ("ogi", "og"), ("li", ""))
_STEP3 = (("ational", "ate"), ("tional", "tion"), ("alize", "al"), ("icate", "ic"), ("iciti", "ic"),
          ("ative", ""), ("ical", "ic"), ("ness", ""), ("ful", ""))
_STEP4 = _by_len("al", "ance", "ence", "er", "ic", "able", "ible", "ant", "ement", "ment", "ent", "ism",
                 "ate", "iti", "ous", "ive", "ize", "ion")


def _en_region(w, start):
    for i in range(max(start, 1), len(w)):
        if w[i] not in _EN_V and w[i - 1] in _EN_V and i - 1 >= start:
            return i + 1
    return len(w)


def _short_syllable_end(w):
    if len(w) == 2:
        return w[0] in _EN_V and w[1] not in _EN_V
    return (len(w) >= 3 and w[-3] not in _EN_V and w[-2] in _EN_V and w[-1] not in _EN_V
            and w[-1] not in "wxY")


def stem_en(word):
    w = word.lower()
    if w in _EN_EXC1:
        return _EN_EXC1[w]
    if len(w) <= 2:
        return w
    w = w.lstrip("'")
    w = "".join("Y" if c == "y" and (i == 0 or w[i - 1] in _EN_V) else c for i, c in enumerate(w))
    if w.startswith(("gener", "arsen")):
        r1 = 5
    elif w.startswith("commun"):
        r1 = 6
    else:
        r1 = _en_region(w, 0)
    r2 = _en_region(w, r1)
    # step 0
    for s in ("'s'", "'s", "'"):
        if w.endswith(s):
            w = w[:-len(s)]
            break
    # step 1a
    if w.endswith("sses"):
        w = w[:-2]
    elif w.endswith(("ied", "ies")):
        w = w[:-2] if len(w) > 4 else w[:-1]
    elif w.endswith(("us", "ss")):
        pass
    elif w.endswith("s") and any(c in _EN_V for c in w[:-2]):
        w = w[:-1]
    if w in _EN_EXC2:
        return w
    # step 1b
    for s in ("eedly", "ingly", "edly", "eed", "ing", "ed"):
        if not w.endswith(s):
            continue
        if s in ("eed", "eedly"):
            if len(w) - len(s) >= r1:
                w = w[:-len(s)] + "ee"
        elif any(c in _EN_V for c in w[:-len(s)]):
            w = w[:-len(s)]
            if w.endswith(("at", "bl", "iz")):
                w += "e"
            elif w.endswith(_DOUBLES):
                w = w[:-1]
            elif _short_syllable_end(w) and r1 >= len(w):
                w += "e"
        break
    # step 1c
    if len(w) > 2 and w[-1] in "yY" and w[-2] not in _EN_V:
        w = w[:-1] + "i"
    # step 2
    for s, rep in _STEP2:
        if w.endswith(s):
            if len(w) - len(s) >= r1:
                if s == "ogi":
                    if len(w) > 3 and w[-4] == "l":
                        w = w[:-1]
                elif s == "li":
                    if len(w) > 2 and w[-3] in _LI_END:
                        w = w[:-2]
                else:
                    w = w[:-len(s)] + rep
            break
    # step 3
    for s, rep in _STEP3:
        if w.endswith(s):
            if len(w) - len(s) >= r1 and (s != "ative" or len(w) - len(s) >= r2):
                w = w[:-len(s)] + rep
            break
    # step 4
    for s in _STEP4:
        if w.endswith(s):
            if len(w) - len(s) >= r2 and (s != "ion" or (len(w) > 3 and w[-4] in "st")):
                w = w[:-len(s)]
            break
    # step 5
    if w.endswith("e"):
        if len(w) - 1 >= r2 or (len(w) - 1 >= r1 and not _short_syllable_end(w[:-1])):
            w = w[:-1]
    elif w.endswith("l") and len(w) - 1 >= r2 and w.endswith("ll"):
        w = w[:-1]
    return w.replace("Y", "y")


# ---------------------------------------------------------------- tokens

WORD = re.compile(r"[^\W_]+")
_CYR = re.compile(r"[а-яё]")
_LAT = re.compile(r"^[a-z']+$")


@lru_cache(maxsize=1_000_000)
def stem(token):
    """Stem one lowercase token: Russian if it has Cyrillic, English if it is Latin, else as is."""
    if _CYR.search(token):
        return stem_ru(token) if token.isalpha() else token
    if _LAT.match(token):
        return stem_en(token)
    return token


def words(text):
    """Lowercase word tokens (letters and digits; `_` and punctuation split words)."""
    return WORD.findall(text.lower())


_TRANSLIT = dict(zip("абвгдезийклмнопрстуфхцыэ", "abvgdeziyklmnoprstufhcye"))
_TRANSLIT.update({"ж": "zh", "ч": "ch", "ш": "sh", "щ": "sch", "ю": "yu", "я": "ya", "ъ": "", "ь": "", "ё": "e"})


def translit(token):
    """Latin spelling of a Cyrillic token (сниппет → snippet), for loanwords written both ways."""
    return "".join(_TRANSLIT.get(c, c) for c in token)


if __name__ == "__main__":
    import sys
    for arg in sys.argv[1:]:
        print(arg, "→", " ".join(stem(t) for t in words(arg)))
