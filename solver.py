import os
import re
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

DIRS = {
    "→": (0, 1), "←": (0, -1), "↓": (1, 0), "↑": (-1, 0),
    "↘": (1, 1), "↙": (1, -1), "↗": (-1, 1), "↖": (-1, -1),
}

HINT_RE = re.compile(
    r'(?:[A-Za-z][ \t]*[_*\-\.]{1,}[ \t]*[A-Za-z]|'
    r'[A-Za-z][ \t]*(?:[ \t]*[_*\-\.]){2,}|'
    r'(?:[ \t]*[_*\-\.]){2,}[ \t]*[A-Za-z]?[ \t]*[\(\[\{]?[ \t]*\d+)',
    re.I
)

@dataclass(frozen=True)
class Hint:
    first: str
    last: str
    length: int

@dataclass
class Cand:
    word: str
    start: tuple
    end: tuple
    arrow: str
    approx: bool = False
    score: float = 0.0

@dataclass
class Result:
    hint: Hint
    cands: list = field(default_factory=list)

    @property
    def best(self):
        return self.cands[0] if self.cands else None

RAW_CONFUSABLES = {
    "I": ["L", "J", "T", "1", "|"],
    "L": ["I", "J", "1"],
    "O": ["Q", "D", "C", "0"],
    "Q": ["O", "G"],
    "D": ["O", "B", "0"],
    "B": ["R", "D", "P", "8"],
    "R": ["B", "P", "K"],
    "M": ["N", "W"],
    "N": ["M", "H"],
    "W": ["M", "V", "U"],
    "S": ["Z", "5"],
    "Z": ["S", "2"],
    "P": ["R", "B"],
    "U": ["V", "W"],
    "V": ["U", "W", "Y"],
    "G": ["C", "Q", "6"],
    "C": ["G", "O"],
    "E": ["F", "B"],
    "F": ["E", "T", "P"],
    "H": ["N", "K"],
    "K": ["R", "H", "X"],
    "T": ["I", "F", "J", "7"],
    "J": ["I", "L"],
    "0": ["O"],
}

CONFUSABLES = defaultdict(set)
for k, vs in RAW_CONFUSABLES.items():
    for v in vs:
        CONFUSABLES[k].add(v)
        CONFUSABLES[v].add(k)

def load_words(path="words.txt"):
    if not os.path.exists(path):
        return frozenset(), defaultdict(list), defaultdict(list), defaultdict(list), defaultdict(list), set()
    words = set()
    by_hint = defaultdict(list)
    by_len = defaultdict(list)
    by_len_first = defaultdict(list)
    by_len_last = defaultdict(list)
    word_keys = set()
    with open(path, encoding="utf-8") as f:
        for line in f:
            w = line.strip().upper()
            if w and len(w) >= 2:
                words.add(w)
                key = (len(w), w[0], w[-1])
                by_hint[key].append(w)
                by_len[len(w)].append(w)
                by_len_first[(len(w), w[0])].append(w)
                by_len_last[(len(w), w[-1])].append(w)
                word_keys.add(key)
    return frozenset(words), by_hint, by_len, by_len_first, by_len_last, word_keys

WORDS, WORDS_BY_HINT, WORDS_BY_LEN, WORDS_BY_LEN_FIRST, WORDS_BY_LEN_LAST, WORD_KEYS = load_words()

def load_common_words(path="20k.txt"):
    if not os.path.exists(path):
        return frozenset()
    with open(path, encoding="utf-8") as f:
        return frozenset(w.strip().upper() for w in f if w.strip())

COMMON_WORDS = load_common_words()

COMMON_BIGRAMS = {
    "TH", "HE", "IN", "ER", "AN", "RE", "ON", "AT", "EN", "ND",
    "TI", "ES", "OR", "TE", "OF", "ED", "IS", "IT", "AL", "AR",
    "ST", "TO", "NT", "NG", "SE", "HA", "AS", "OU", "IO", "LE",
    "VE", "CO", "ME", "DE", "HI", "RI", "RO", "IC", "NE", "EA",
    "RA", "CE", "LI", "CH", "LL", "BE", "MA", "SI", "OM", "UR",
}

def _bigram_score(word: str) -> float:
    """Score based on English bigram frequency."""
    if len(word) < 2:
        return 0.0
    hits = sum(1 for i in range(len(word) - 1) if word[i:i+2] in COMMON_BIGRAMS)
    return hits / (len(word) - 1)

def parse_hints(text: str) -> list[Hint]:
    """
    Universally parse word hints of ANY letter length (e.g. 2, 3, 4, 5, 6, 7, 8, 9, 10+ letters).
    Supports:
    - Explicit count double-endpoint: G____Y (6 letters), E__S (4), H_______T (9 letters), C__T (3)
    - Implicit count from blanks double-endpoint: G____Y (6), E__S (4), G****Y (6), G - - - Y (5)
    - Single endpoint hints with explicit count: b ----(7), b - - - - (7), G_____ (6 letters)
    - Single endpoint hints without count: b ------, b - - - - - -
    - End-only hints: ------y (7), ____y
    """
    if not text:
        return []

    hints = []
    matched_spans = []

    BLANK = r'[_*\-\.]'
    BLANKS_SPACED = r'(?:[ \t]*' + BLANK + r')'

    pat1 = re.compile(
        r'(?:^|[ \t\d\.\)\-\*•💡])([A-Za-z])[ \t]*(' + BLANK + r'{1,}|' + BLANKS_SPACED + r'{2,})[ \t]*([A-Za-z])[ \t]*[\(\[\{:\-]?[ \t]*(\d+)[ \t]*(?:letters?|chars?)?[\)\]\}]?',
        re.I | re.M
    )

    pat2 = re.compile(
        r'\b([A-Za-z])(' + BLANKS_SPACED + r'{1,})[ \t]*([A-Za-z])\b',
        re.I | re.M
    )

    pat3 = re.compile(
        r'(?:^|[ \t\d\.\)\-\*•💡])([A-Za-z])[ \t]*(' + BLANK + r'{1,}|' + BLANKS_SPACED + r'{2,})[ \t]*[\(\[\{:\-]?[ \t]*(\d+)[ \t]*(?:letters?|chars?)?[\)\]\}]?',
        re.I | re.M
    )

    pat4 = re.compile(
        r'(?:^|[ \t\d\.\)\-\*•💡])([A-Za-z])(' + BLANKS_SPACED + r'{2,})(?=[ \t]*$|[ \t\n\r])',
        re.I | re.M
    )

    pat5 = re.compile(
        r'(?:^|[ \t\d\.\)\-\*•💡])(' + BLANK + r'{1,}|' + BLANKS_SPACED + r'{2,})[ \t]*([A-Za-z])[ \t]*[\(\[\{:\-]?[ \t]*(\d+)[ \t]*(?:letters?|chars?)?[\)\]\}]?',
        re.I | re.M
    )

    for m in pat1.finditer(text):
        f = m.group(1).upper()
        l = m.group(3).upper()
        n = int(m.group(4))
        if n >= 2:
            hints.append(Hint(f, l, n))
            matched_spans.append(m.span())

    for m in pat2.finditer(text):
        s, e = m.span()
        if any(not (e <= ms or s >= me) for ms, me in matched_spans):
            continue
        f = m.group(1).upper()
        l = m.group(3).upper()
        blanks = sum(1 for c in m.group(2) if c in '_*-.')
        n = 1 + blanks + 1
        if n >= 2:
            hints.append(Hint(f, l, n))
            matched_spans.append((s, e))

    for m in pat3.finditer(text):
        s, e = m.span()
        if any(not (e <= ms or s >= me) for ms, me in matched_spans):
            continue
        f = m.group(1).upper()
        n = int(m.group(3))
        if n >= 2:
            hints.append(Hint(f, '', n))
            matched_spans.append((s, e))

    for m in pat4.finditer(text):
        s, e = m.span()
        if any(not (e <= ms or s >= me) for ms, me in matched_spans):
            continue
        f = m.group(1).upper()
        blanks = sum(1 for c in m.group(2) if c in '_*-.')
        n = 1 + blanks
        if n >= 3:
            hints.append(Hint(f, '', n))
            matched_spans.append((s, e))

    for m in pat5.finditer(text):
        s, e = m.span()
        if any(not (e <= ms or s >= me) for ms, me in matched_spans):
            continue
        l = m.group(2).upper()
        n = int(m.group(3))
        if n >= 2:
            hints.append(Hint('', l, n))
            matched_spans.append((s, e))

    return hints

def _compare_path_to_word(path_str: str, dict_word: str) -> tuple[int, float]:
    """
    Compares a path from the grid to a target dictionary word.
    Returns (exact_matches, weighted_similarity_score).
    """
    exact = 0
    weighted = 0.0
    for c1, c2 in zip(path_str, dict_word):
        if c1 == c2:
            exact += 1
            weighted += 2.0
        elif c2 in CONFUSABLES.get(c1, ()):
            weighted += 1.6
        else:
            weighted += 0.0
    return exact, weighted

def solve_hint(grid: list[str], hint: Hint) -> Result:
    """
    Solves a single hint using Anchor-Ray Grid Search:
    - Finds anchor positions for the given letter (e.g. 'B') in the grid.
    - Traces straight rays in all 8 directions of length n.
    - Immediately verifies valid dictionary words (O(1) meaning check).
    - Tolerates OCR confusions and corruptions with fuzzy fallback.
    """
    R, C = len(grid), len(grid[0])
    n = hint.length
    f = hint.first.upper() if hint.first else ''
    l = hint.last.upper() if hint.last else ''
    candidates: list[Cand] = []

    if f and l:
        dict_words = WORDS_BY_HINT.get((n, f, l), [])
    elif f:
        dict_words = WORDS_BY_LEN_FIRST.get((n, f), [])
    elif l:
        dict_words = WORDS_BY_LEN_LAST.get((n, l), [])
    else:
        dict_words = WORDS_BY_LEN.get(n, [])

    exact_start_anchors = []
    conf_start_anchors = []
    exact_end_anchors = []
    conf_end_anchors = []

    conf_f = CONFUSABLES.get(f, set()) if f else set()
    conf_l = CONFUSABLES.get(l, set()) if l else set()

    for r in range(R):
        row = grid[r]
        for c in range(C):
            ch = row[c]
            if f:
                if ch == f:
                    exact_start_anchors.append((r, c))
                elif ch in conf_f:
                    conf_start_anchors.append((r, c))
            if l:
                if ch == l:
                    exact_end_anchors.append((r, c))
                elif ch in conf_l:
                    conf_end_anchors.append((r, c))

    primary_anchors = exact_start_anchors if f else [(r, c) for r in range(R) for c in range(C)]
    extracted_rays = []

    for r, c in primary_anchors:
        for arrow, (dr, dc) in DIRS.items():
            er, ec = r + dr * (n - 1), c + dc * (n - 1)
            if 0 <= er < R and 0 <= ec < C:
                dir_bonus = 2.0 if arrow in ("→", "←", "↓", "↑") else 0.0
                end_match = (grid[er][ec] == l) if l else True
                path_str = "".join(grid[r + dr * i][c + dc * i] for i in range(n))
                extracted_rays.append((path_str, (r, c), (er, ec), arrow, dir_bonus))

                if end_match and path_str in WORDS:
                    bonus = 50.0 if path_str in COMMON_WORDS else 0.0
                    candidates.append(
                        Cand(path_str, (r, c), (er, ec), arrow, approx=False, score=1000.0 + dir_bonus + bonus)
                    )

    if not any(c.score >= 1000 for c in candidates):

        for path_str, start, end, arrow, dir_bonus in extracted_rays:
            for dw in dict_words:
                exact, sim = _compare_path_to_word(path_str, dw)
                if exact == n - 1:
                    is_conf = any(c2 in CONFUSABLES.get(c1, ()) for c1, c2 in zip(path_str, dw) if c1 != c2)
                    boost = 850.0 if is_conf else 800.0
                    candidates.append(Cand(dw, start, end, arrow, approx=False, score=boost + sim + dir_bonus))
                elif exact == n - 2 and n >= 4:
                    candidates.append(Cand(dw, start, end, arrow, approx=False, score=600.0 + sim + dir_bonus))

        if not any(c.score >= 800 for c in candidates) and conf_start_anchors:
            for r, c in conf_start_anchors:
                for arrow, (dr, dc) in DIRS.items():
                    er, ec = r + dr * (n - 1), c + dc * (n - 1)
                    if 0 <= er < R and 0 <= ec < C:
                        path_str = "".join(grid[r + dr * i][c + dc * i] for i in range(n))
                        dir_bonus = 2.0 if arrow in ("→", "←", "↓", "↑") else 0.0
                        for dw in dict_words:
                            exact, sim = _compare_path_to_word(path_str, dw)
                            if exact == n - 1:
                                candidates.append(Cand(dw, (r, c), (er, ec), arrow, approx=True, score=850.0 + sim + dir_bonus))
                            elif exact == n - 2 and n >= 4:
                                candidates.append(Cand(dw, (r, c), (er, ec), arrow, approx=True, score=700.0 + sim + dir_bonus))

        if not any(c.score >= 800 for c in candidates) and l and exact_end_anchors:
            for er, ec in exact_end_anchors:
                for arrow, (dr, dc) in DIRS.items():
                    r, c = er - dr * (n - 1), ec - dc * (n - 1)
                    if 0 <= r < R and 0 <= c < C:
                        path_str = "".join(grid[r + dr * i][c + dc * i] for i in range(n))
                        dir_bonus = 2.0 if arrow in ("→", "←", "↓", "↑") else 0.0
                        for dw in dict_words:
                            exact, sim = _compare_path_to_word(path_str, dw)
                            if exact >= n - 2:
                                candidates.append(Cand(dw, (r, c), (er, ec), arrow, approx=True, score=750.0 + sim + dir_bonus))

    if not candidates:
        for path_str, start, end, arrow, dir_bonus in extracted_rays:
            f_match = (path_str[0] == f) if f else True
            l_match = (path_str[-1] == l) if l else True
            if f_match and l_match:
                score = (500.0 if path_str in WORDS else 400.0) + _bigram_score(path_str) * 30.0 + dir_bonus
                candidates.append(Cand(path_str, start, end, arrow, approx=False, score=score))

    seen = {}
    for c in candidates:
        key = (c.word, c.start, c.end)
        if key not in seen or seen[key].score < c.score:
            seen[key] = c

    sorted_cands = sorted(seen.values(), key=lambda x: -x.score)
    return Result(hint, sorted_cands[:15])

def solve(grid: list[str], hints: list[Hint]) -> list[Result]:
    """
    Solves all hints on the grid concurrently using parallel thread workers,
    and assigns distinct word/path candidates across hints to prevent collisions.
    """
    if not hints:
        return []

    num_workers = min(len(hints), 8)
    with ThreadPoolExecutor(max_workers=num_workers) as executor:
        raw_results = list(executor.map(lambda h: solve_hint(grid, h), hints))

    used_paths = set()
    used_words = set()

    hint_indices = list(range(len(raw_results)))
    hint_indices.sort(key=lambda i: sum(1 for c in raw_results[i].cands if c.score >= 500))

    assigned = {}

    for idx in hint_indices:
        res = raw_results[idx]
        chosen = None

        for c in res.cands:
            p1 = (c.start, c.end)
            p2 = (c.end, c.start)
            if p1 not in used_paths and p2 not in used_paths and c.word not in used_words:
                chosen = c
                break

        if not chosen:
            for c in res.cands:
                p1 = (c.start, c.end)
                p2 = (c.end, c.start)
                if p1 not in used_paths and p2 not in used_paths:
                    chosen = c
                    break

        if not chosen and res.cands:
            chosen = res.cands[0]

        if chosen:
            assigned[idx] = chosen
            used_paths.add((chosen.start, chosen.end))
            used_words.add(chosen.word)

    final_results = []
    for idx in range(len(raw_results)):
        res = raw_results[idx]
        if idx in assigned:
            chosen = assigned[idx]
            rem = [c for c in res.cands if c != chosen]
            final_results.append(Result(res.hint, [chosen] + rem))
        else:
            final_results.append(res)

    return final_results
