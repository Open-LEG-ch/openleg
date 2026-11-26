# SPDX-License-Identifier: AGPL-3.0-or-later
"""Public site search: folded matching and ranking over public documents.

The engine knows nothing about Flask, storage or templates. Callers hand it
immutable documents; `site_search_index` decides which public content becomes
a document. Matching is case and accent insensitive, treats German umlaut
spellings alike, completes prefixes for type-ahead and tolerates typos in
longer words. Domain synonyms connect the four site languages.
"""

import bisect
import re
import threading
import unicodedata
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass, field

MAX_QUERY_LENGTH = 100
MAX_TERMS = 8

TITLE_WEIGHT = 8.0
KEYWORD_WEIGHT = 4.0
HEADING_WEIGHT = 4.0
SUMMARY_WEIGHT = 2.0
BODY_WEIGHT = 1.0

EXACT = 1.0
CONCEPT = 0.9
PREFIX = 0.7
FUZZY_ONE = 0.6
FUZZY_TWO = 0.45
INFIX = 0.4

SNIPPET_LENGTH = 180

# Combining marks stay with their letter, so a decomposed "e\u0300" is one word.
_TOKEN = re.compile(
    r"[^\W_](?:[^\W_]|[\u0300-\u036f\u1ab0-\u1aff\u1dc0-\u1dff\u20d0-\u20ff\ufe20-\ufe2f])*"
)
_SPECIAL = str.maketrans({"ø": "o", "ł": "l", "đ": "d", "ð": "d", "þ": "th"})
_UMLAUT_SPELLING = re.compile(r"([aou])e")

# Words that carry no meaning on their own. A query keeps them only when it
# consists of nothing else.
STOP_WORDS = frozenset(
    [
        "der",
        "die",
        "das",
        "den",
        "dem",
        "des",
        "ein",
        "eine",
        "einer",
        "eines",
        "und",
        "oder",
        "mit",
        "von",
        "zu",
        "zum",
        "zur",
        "im",
        "in",
        "am",
        "an",
        "auf",
        "fur",
        "ist",
        "sind",
        "was",
        "wie",
        "wer",
        "ich",
        "wir",
        "le",
        "la",
        "les",
        "l",
        "un",
        "une",
        "de",
        "du",
        "d",
        "et",
        "ou",
        "en",
        "au",
        "aux",
        "pour",
        "est",
        "qui",
        "que",
        "il",
        "lo",
        "gli",
        "i",
        "di",
        "del",
        "della",
        "e",
        "per",
        "con",
        "che",
        "the",
        "a",
        "an",
        "of",
        "and",
        "or",
        "to",
        "for",
        "is",
        "what",
        "how",
    ]
)

# Domain synonym groups. Each phrase is matched as consecutive folded words,
# so inflected and translated spellings listed here lead to the same concept.
SYNONYMS = {
    "leg": (
        "LEG",
        "LEGs",
        "lokale Elektrizitätsgemeinschaft",
        "lokalen Elektrizitätsgemeinschaft",
        "lokale Elektrizitätsgemeinschaften",
        "lokalen Elektrizitätsgemeinschaften",
        "Elektrizitätsgemeinschaft",
        "Elektrizitätsgemeinschaften",
        "Stromgemeinschaft",
        "Stromgemeinschaften",
        "CEL",
        "communauté électrique locale",
        "communautés électriques locales",
        "communauté locale de l'électricité",
        "communautés locales de l'électricité",
        "comunità elettrica locale",
        "comunità elettriche locali",
        "comunità locale dell'energia elettrica",
        "local electricity community",
        "local electricity communities",
        "local energy community",
        "local energy communities",
    ),
    "vnb": (
        "VNB",
        "Verteilnetzbetreiber",
        "Verteilnetzbetreiberin",
        "Verteilnetzbetreibers",
        "Netzbetreiber",
        "Netzbetreiberin",
        "Netzbetreibers",
        "gestionnaire de réseau",
        "gestionnaire du réseau",
        "gestionnaires de réseau",
        "gestionnaire de réseau de distribution",
        "gestionnaires de réseau de distribution",
        "GRD",
        "gestore di rete",
        "gestore della rete",
        "gestori di rete",
        "gestori della rete",
        "gestore della rete di distribuzione",
        "gestori della rete di distribuzione",
        "DSO",
        "DSOs",
        "distribution system operator",
        "distribution system operators",
        "network operator",
        "network operators",
        "grid operator",
        "grid operators",
    ),
    "zev": (
        "ZEV",
        "Zusammenschluss zum Eigenverbrauch",
        "Zusammenschlüsse zum Eigenverbrauch",
        "RCP",
        "regroupement dans le cadre de la consommation propre",
        "raggruppamento ai fini del consumo proprio",
    ),
    "vzev": (
        "vZEV",
        "virtueller ZEV",
        "virtuellen ZEV",
        "virtueller Zusammenschluss zum Eigenverbrauch",
        "ZEV virtuel",
        "RCP virtuel",
        "ZEV virtuale",
        "RCP virtuale",
        "virtual ZEV",
    ),
    "smart_meter": (
        "Smart Meter",
        "Smart Meters",
        "Smartmeter",
        "Smart Metering",
        "intelligentes Messsystem",
        "intelligente Messsysteme",
        "intelligenten Messsystemen",
        "intelligenter Zähler",
        "intelligente Zähler",
        "compteur intelligent",
        "compteurs intelligents",
        "système de mesure intelligent",
        "systèmes de mesure intelligents",
        "contatore intelligente",
        "contatori intelligenti",
        "sistema di misurazione intelligente",
        "sistemi di misurazione intelligenti",
    ),
    "evu": (
        "EVU",
        "Energieversorgungsunternehmen",
        "Elektrizitätsversorgungsunternehmen",
        "Energieversorger",
        "Stromversorger",
        "utility",
        "utilities",
        "fournisseur d'énergie",
        "fournisseur d'électricité",
        "fornitore di energia",
        "fornitore di elettricità",
    ),
    "messpunkt": (
        "Messpunkt",
        "Messpunkte",
        "Messpunkts",
        "point de mesure",
        "points de mesure",
        "punto di misurazione",
        "punti di misurazione",
        "metering point",
        "metering points",
    ),
    "netzentgelt": (
        "Netzentgelt",
        "Netzentgelte",
        "Netznutzungsentgelt",
        "Netznutzungsentgelte",
        "tarif d'utilisation du réseau",
        "rémunération pour l'utilisation du réseau",
        "tariffa di utilizzo della rete",
        "network fee",
        "network fees",
        "grid fee",
        "grid fees",
        "network charge",
        "network charges",
    ),
    "rangliste": ("Rangliste", "Ranking", "classement", "classifica"),
    "sankt": ("Sankt", "St", "Saint", "Sainte", "Ste"),
    "gemeinde": (
        "Gemeinde",
        "Gemeinden",
        "commune",
        "communes",
        "comune",
        "comuni",
        "municipality",
        "municipalities",
    ),
    "photovoltaik": (
        "Photovoltaik",
        "Fotovoltaik",
        "PV",
        "Solaranlage",
        "Solaranlagen",
        "photovoltaïque",
        "fotovoltaico",
        "fotovoltaica",
        "photovoltaics",
        "solar panels",
    ),
}


def fold(text):
    """Return the matching form: lower case, no accents, one umlaut spelling.

    `ä`, `ae` and `a` all fold to `a` (likewise `ö`/`oe`, `ü`/`ue`), so either
    German spelling and the unaccented form find each other. `ß` becomes `ss`.
    """
    text = unicodedata.normalize("NFKD", text.casefold().translate(_SPECIAL))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return _UMLAUT_SPELLING.sub(r"\1", text.replace("æ", "ae").replace("œ", "oe"))


def tokenize(text):
    """Yield `(start, end, folded)` for every word of the original text."""
    for match in _TOKEN.finditer(text):
        folded = fold(match.group())
        if folded:
            yield match.start(), match.end(), folded


def folded_words(text):
    return [folded for _start, _end, folded in tokenize(text)]


def clean_query(raw):
    """Keep printable text, collapse whitespace and cap the length."""
    text = "".join(char if char.isprintable() else " " for char in str(raw or ""))
    return " ".join(text.split())[:MAX_QUERY_LENGTH].strip()


def _concept_token(name):
    return "\x00" + name


_PHRASES = {}
for _name, _phrases in SYNONYMS.items():
    for _phrase in _phrases:
        _words = tuple(folded_words(_phrase))
        _PHRASES.setdefault(_words[0], set()).add((_words, _name))
_PHRASES = {
    first: tuple(sorted(entries, key=lambda entry: -len(entry[0])))
    for first, entries in _PHRASES.items()
}


def _phrase_matches(words):
    """Yield `(start, length, concept)` for synonym phrases in a word list."""
    for index, word in enumerate(words):
        for phrase, name in _PHRASES.get(word, ()):
            if tuple(words[index : index + len(phrase)]) == phrase:
                yield index, len(phrase), name


def _concepts(words):
    return {_concept_token(name) for _index, _length, name in _phrase_matches(words)}


@dataclass(frozen=True)
class Block:
    """One section of a page: its heading, anchor and text."""

    text: str
    heading: str = ""
    anchor: str = ""


@dataclass(frozen=True)
class Document:
    """One searchable public item.

    `group_key` is shared by the language versions of the same item so a
    result appears once. `duplicate_key` merges items whose text is identical.
    `boosts` maps a folded word to extra score when a query word equals it.
    """

    kind: str
    url: str
    title: str
    locale: str | None = None
    summary: str = ""
    keywords: tuple[str, ...] = ()
    blocks: tuple[Block, ...] = ()
    context: str = ""
    anchor: str = ""
    group_key: str = ""
    duplicate_key: str = ""
    summary_fallback: bool = True
    boosts: Mapping[str, float] = field(default_factory=dict)
    data: Mapping[str, object] = field(default_factory=dict)

    @property
    def headings(self):
        return tuple(block.heading for block in self.blocks if block.heading)


@dataclass(frozen=True)
class Segment:
    text: str
    match: bool = False


@dataclass(frozen=True)
class Hit:
    document: Document
    score: float
    secondary: bool
    title: tuple[Segment, ...]
    snippet: tuple[Segment, ...]
    anchor: str = ""
    section: str = ""


@dataclass(frozen=True)
class Group:
    kind: str
    hits: tuple[Hit, ...]
    total: int


@dataclass(frozen=True)
class SearchResult:
    query: str
    groups: tuple[Group, ...]
    total: int
    suggestion: str | None = None
    ignored: tuple[str, ...] = ()

    def group(self, kind):
        return next((group for group in self.groups if group.kind == kind), None)


@dataclass(frozen=True)
class _Clause:
    """One required part of a query: a typed word or a recognised concept."""

    term: str = ""
    concept: str = ""
    words: tuple[str, ...] = ()


def osa_distance(left, right, limit):
    """Damerau (optimal string alignment) distance, or `limit + 1` beyond it."""
    if abs(len(left) - len(right)) > limit:
        return limit + 1
    previous2 = None
    previous = list(range(len(right) + 1))
    for i in range(1, len(left) + 1):
        current = [i] + [0] * len(right)
        best = i
        char = left[i - 1]
        for j in range(1, len(right) + 1):
            cost = 0 if char == right[j - 1] else 1
            value = min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + cost)
            if i > 1 and j > 1 and char == right[j - 2] and left[i - 2] == right[j - 1]:
                value = min(value, previous2[j - 2] + 1)
            current[j] = value
            best = min(best, value)
        if best > limit:
            return limit + 1
        previous2, previous = previous, current
    return previous[-1]


def typo_limit(term):
    """Allowed edits: none up to three letters, one up to seven, then two."""
    if len(term) < 4 or term.isdigit():
        return 0
    return 1 if len(term) <= 7 else 2


def _letters(word):
    mask = 0
    for char in word:
        mask |= 1 << (ord(char) % 61)
    return mask


class SearchIndex:
    """An immutable in-memory index; build a new one to change the content.

    Threads share one index. Only the bounded cache of expanded query words
    changes after construction, under its own lock.
    """

    EXPANSION_CACHE_SIZE = 4096

    def __init__(self, documents):
        self.documents = tuple(documents)
        self._postings = {}
        self._document_words = []
        # Per word and kind: how many documents use it and how they spell it,
        # so hidden kinds neither steer typo matching nor corrections.
        self._word_kinds = {}
        self._spellings = {}
        for doc_id, document in enumerate(self.documents):
            words = {}
            for weight, texts in (
                (TITLE_WEIGHT, (document.title,)),
                (KEYWORD_WEIGHT, document.keywords),
                (HEADING_WEIGHT, document.headings),
                (SUMMARY_WEIGHT, (document.summary,)),
                (BODY_WEIGHT, tuple(block.text for block in document.blocks)),
            ):
                for text in texts:
                    tokens = list(tokenize(text))
                    folded = [token[2] for token in tokens]
                    for word in (*folded, *_concepts(folded)):
                        if words.get(word, 0) < weight:
                            words[word] = weight
                    for start, end, word in tokens:
                        counts = self._spellings.setdefault(word, {}).setdefault(
                            document.kind, {}
                        )
                        spelling = text[start:end]
                        counts[spelling] = counts.get(spelling, 0) + 1
            self._document_words.append(words)
            for word, weight in words.items():
                self._postings.setdefault(word, {})[doc_id] = weight
                kinds = self._word_kinds.setdefault(word, {})
                kinds[document.kind] = kinds.get(document.kind, 0) + 1
        self._kinds = frozenset(document.kind for document in self.documents)
        self._vocabulary = sorted(
            word for word in self._postings if not word.startswith("\x00")
        )
        self._by_length = {}
        for word in self._vocabulary:
            if not word.isdigit():
                self._by_length.setdefault(len(word), []).append((_letters(word), word))
        self._expansions = OrderedDict()
        self._expansions_lock = threading.Lock()

    # --- term expansion -------------------------------------------------

    def _visible(self, word, kinds):
        return kinds is None or any(kind in kinds for kind in self._word_kinds[word])

    def _frequency(self, word, kinds):
        return sum(
            count
            for kind, count in self._word_kinds[word].items()
            if kinds is None or kind in kinds
        )

    def _spelling(self, word, kinds):
        """The most common original spelling of a word in the allowed kinds."""
        counts = {}
        for kind, spellings in self._spellings.get(word, {}).items():
            if kinds is None or kind in kinds:
                for spelling, count in spellings.items():
                    counts[spelling] = counts.get(spelling, 0) + count
        if not counts:
            return None
        return min(counts, key=lambda spelling: (-counts[spelling], spelling))

    def _cached(self, key, compute):
        with self._expansions_lock:
            cached = self._expansions.get(key)
            if cached is not None:
                self._expansions.move_to_end(key)
                return cached
        value = compute()
        with self._expansions_lock:
            self._expansions[key] = value
            if len(self._expansions) > self.EXPANSION_CACHE_SIZE:
                self._expansions.popitem(last=False)
        return value

    def _direct(self, term):
        """Exact, prefix and infix matches: `{vocabulary word: quality}`."""
        expansion = {}
        if term in self._postings:
            expansion[term] = EXACT
        if len(term) >= 2 or term.isdigit():
            start = bisect.bisect_left(self._vocabulary, term)
            for word in self._vocabulary[start:]:
                if not word.startswith(term):
                    break
                expansion.setdefault(word, PREFIX)
        if len(term) >= 4 and not term.isdigit():
            for length, entries in self._by_length.items():
                if length <= len(term):
                    continue
                for _mask, word in entries:
                    if term in word:
                        expansion.setdefault(word, INFIX)
        return expansion

    def _typos(self, term):
        return {
            word: FUZZY_ONE if distance == 1 else FUZZY_TWO
            for word, distance in self._near(term, typo_limit(term))
        }

    def _expand(self, term, kinds=None):
        """Return `{vocabulary word: quality}` for one folded query word.

        Typos count when the word is long or nothing visible starts with it.
        """
        direct = self._cached(("direct", term), lambda: self._direct(term))
        if kinds is not None:
            direct = {
                word: quality
                for word, quality in direct.items()
                if self._visible(word, kinds)
            }
        if len(term) < 6 and any(quality >= PREFIX for quality in direct.values()):
            return direct
        expansion = dict(direct)
        for word, quality in self._cached(
            ("typos", term), lambda: self._typos(term)
        ).items():
            if self._visible(word, kinds) and expansion.get(word, 0) < quality:
                expansion[word] = quality
        return expansion

    def _near(self, term, limit):
        if limit == 0:
            return []
        mask = _letters(term)
        found = []
        for length in range(max(4, len(term) - limit), len(term) + limit + 1):
            for word_mask, word in self._by_length.get(length, ()):
                if word == term or (word_mask ^ mask).bit_count() > 2 * limit:
                    continue
                distance = osa_distance(term, word, limit)
                if distance <= limit:
                    found.append((word, distance))
        return found

    def correction(self, term, kinds=None):
        """The most likely intended word for a misspelt term, or None."""
        if term.isdigit() or len(term) < 3:
            return None
        limit = 1 if len(term) <= 4 else 2
        candidates = [
            (distance, -self._frequency(word, kinds), word)
            for word, distance in self._near(term, limit)
            if self._visible(word, kinds) and self._spelling(word, kinds)
        ]
        if not candidates:
            return None
        return min(candidates)[2]

    # --- query --------------------------------------------------------------

    def _clauses(self, query):
        words = folded_words(query)[: MAX_TERMS * 2]
        clauses = []
        index = 0
        # The longest phrase starting at a word wins; it is listed first.
        concepts = {}
        for start, length, name in _phrase_matches(words):
            concepts.setdefault(start, (length, name))
        while index < len(words):
            if index in concepts and concepts[index][0] > 1:
                length, name = concepts[index]
                clauses.append(
                    _Clause(
                        concept=_concept_token(name),
                        words=tuple(words[index : index + length]),
                    )
                )
                index += length
                continue
            word = words[index]
            concept = _concept_token(concepts[index][1]) if index in concepts else ""
            clauses.append(_Clause(term=word, concept=concept, words=(word,)))
            index += 1
        meaningful = [
            clause
            for clause in clauses
            if clause.concept or clause.term not in STOP_WORDS
        ]
        return (meaningful or clauses)[:MAX_TERMS]

    def _clause_scores(self, clause, kinds=None):
        """Best weighted match per document, limited to the allowed kinds."""
        scores = {}
        if clause.concept:
            for doc_id, weight in self._postings.get(clause.concept, {}).items():
                scores[doc_id] = weight * CONCEPT
        if clause.term:
            for word, quality in self._expand(clause.term, kinds).items():
                for doc_id, weight in self._postings[word].items():
                    value = weight * quality
                    if scores.get(doc_id, 0) < value:
                        scores[doc_id] = value
        if kinds is not None:
            scores = {
                doc_id: value
                for doc_id, value in scores.items()
                if self.documents[doc_id].kind in kinds
            }
        return scores

    def search(self, query, *, locale=None, kinds=None, limit=8, offset=0):
        """Rank documents that match every query part; group them by kind.

        With `kinds`, other documents neither match nor shape suggestions.
        """
        query = clean_query(query)
        if kinds is not None:
            kinds = frozenset(kinds)
            if self._kinds <= kinds:
                kinds = None
        clauses = self._clauses(query)
        if not clauses:
            return SearchResult(query=query, groups=(), total=0)
        scored = [(clause, self._clause_scores(clause, kinds)) for clause in clauses]
        known = [(clause, scores) for clause, scores in scored if scores]
        ignored = ()
        if known and len(known) < len(scored):
            # A word that matches nothing at all would empty the result. Leave
            # it out and say so, rather than show nothing.
            surface = {
                folded: query[start:end] for start, end, folded in tokenize(query)
            }
            ignored = tuple(
                " ".join(surface.get(word, word) for word in clause.words)
                for clause, scores in scored
                if not scores
            )
            scored = known
        clause_scores = [scores for _clause, scores in scored]
        matched = set.intersection(*(set(scores) for scores in clause_scores))
        hits = self._rank(query, clauses, clause_scores, matched, locale)
        suggestion = self._suggest(query, clauses, locale, kinds)
        grouped = {}
        for doc_id, score, secondary in hits:
            grouped.setdefault(self.documents[doc_id].kind, []).append(
                (doc_id, score, secondary)
            )
        groups = []
        for kind, entries in sorted(
            grouped.items(), key=lambda item: (item[1][0][2], -item[1][0][1])
        ):
            if kinds is not None and kind not in kinds:
                continue
            selected = entries[offset : offset + limit]
            groups.append(
                Group(
                    kind=kind,
                    hits=tuple(
                        self._hit(doc_id, score, secondary, clauses, kinds)
                        for doc_id, score, secondary in selected
                    ),
                    total=len(entries),
                )
            )
        return SearchResult(
            query=query,
            groups=tuple(groups),
            total=sum(
                len(entries)
                for kind, entries in grouped.items()
                if kinds is None or kind in kinds
            ),
            suggestion=suggestion,
            ignored=ignored,
        )

    def _rank(self, query, clauses, clause_scores, matched, locale):
        folded_query = " ".join(folded_words(query))
        exact_words = {clause.term for clause in clauses if clause.term}
        ranked = []
        for doc_id in matched:
            document = self.documents[doc_id]
            score = sum(scores[doc_id] for scores in clause_scores)
            folded_title = " ".join(folded_words(document.title))
            if folded_title == folded_query:
                score += 40
            elif folded_title.startswith(folded_query):
                score += 15
            title_words = self._document_words[doc_id]
            if all(
                scores.get(doc_id, 0) >= TITLE_WEIGHT * FUZZY_TWO
                for scores in clause_scores
            ) and any(title_words.get(word) == TITLE_WEIGHT for word in exact_words):
                score += 8
            score += sum(document.boosts.get(word, 0) for word in exact_words)
            secondary = document.locale is not None and document.locale != locale
            ranked.append((doc_id, score, secondary))
        ranked.sort(
            key=lambda item: (
                item[2],
                -item[1],
                self.documents[item[0]].title.casefold(),
                item[0],  # Exact ties keep index order, not set order.
            )
        )
        kept = []
        seen_groups = set()
        seen_duplicates = set()
        for doc_id, score, secondary in ranked:
            document = self.documents[doc_id]
            group_key = (document.kind, document.group_key or document.url)
            duplicate_key = (document.locale, document.duplicate_key)
            if group_key in seen_groups:
                continue
            # A page merged into an identical one also hides its translations.
            seen_groups.add(group_key)
            if document.duplicate_key and duplicate_key in seen_duplicates:
                continue
            if document.duplicate_key:
                seen_duplicates.add(duplicate_key)
            kept.append((doc_id, score, secondary))
        return kept

    def _suggest(self, query, clauses, locale, kinds=None):
        """Offer a corrected query when a typed word is not in the index.

        A word that matches nothing and has no likely spelling is left out.
        """
        replacements = {}
        for clause in clauses:
            term = clause.term
            if not term or clause.concept:
                continue
            if term in self._postings and self._visible(term, kinds):
                continue
            if any(quality >= PREFIX for quality in self._expand(term, kinds).values()):
                continue
            corrected = self.correction(term, kinds)
            if corrected:
                replacements[term] = self._spelling(corrected, kinds)
            elif not self._clause_scores(clause, kinds):
                replacements[term] = ""
        if not any(replacements.values()):
            return None
        suggestion = query
        for start, end, folded in reversed(list(tokenize(query))):
            if folded in replacements:
                suggestion = (
                    suggestion[:start] + replacements[folded] + suggestion[end:]
                )
        suggestion = " ".join(suggestion.split())
        if not suggestion or fold(suggestion) == fold(query):
            return None
        check = self.search(suggestion, locale=locale, kinds=kinds, limit=1)
        if not check.total or check.ignored:
            return None
        return suggestion

    # --- presentation -----------------------------------------------------

    def _matcher(self, clauses, kinds=None):
        terms = {}
        for clause in clauses:
            if clause.term:
                terms.update(self._expand(clause.term, kinds))
        concepts = {clause.concept[1:] for clause in clauses if clause.concept}
        return terms, concepts

    def _highlight(self, text, matcher, start=0, end=None):
        terms, concepts = matcher
        end = len(text) if end is None else end
        tokens = [
            token for token in tokenize(text) if token[0] >= start and token[1] <= end
        ]
        marked = [token[2] in terms for token in tokens]
        if concepts:
            words = [token[2] for token in tokens]
            for index, length, name in _phrase_matches(words):
                if name in concepts:
                    for offset in range(length):
                        marked[index + offset] = True
        segments = []
        position = start
        for (token_start, token_end, _word), is_match in zip(
            tokens, marked, strict=True
        ):
            if not is_match:
                continue
            if token_start > position:
                segments.append(Segment(text[position:token_start]))
            segments.append(Segment(text[token_start:token_end], True))
            position = token_end
        if position < end:
            segments.append(Segment(text[position:end]))
        return tuple(segments)

    def _count(self, text, matcher):
        return sum(segment.match for segment in self._highlight(text, matcher))

    def _excerpt(self, text, matcher):
        highlighted = self._highlight(text, matcher)
        if len(text) <= SNIPPET_LENGTH:
            return highlighted
        first = 0
        position = 0
        for segment in highlighted:
            if segment.match:
                first = position
                break
            position += len(segment.text)
        start = max(0, first - SNIPPET_LENGTH // 3)
        if start:
            space = text.find(" ", start)
            start = space + 1 if 0 <= space < first else start
        end = min(len(text), start + SNIPPET_LENGTH)
        if end < len(text):
            space = text.rfind(" ", start, end)
            end = space if space > first else end
        segments = list(self._highlight(text, matcher, start, end))
        if start:
            segments.insert(0, Segment("… "))
        if end < len(text):
            segments.append(Segment(" …"))
        return tuple(segments)

    def _hit(self, doc_id, score, secondary, clauses, kinds=None):
        document = self.documents[doc_id]
        matcher = self._matcher(clauses, kinds)
        best = None
        for block in document.blocks:
            count = self._count(block.heading, matcher) * 2 + self._count(
                block.text, matcher
            )
            if count and (best is None or count > best[0]):
                best = (count, block)
        anchor = document.anchor
        section = ""
        if best is not None:
            block = best[1]
            anchor = block.anchor or anchor
            section = block.heading if block.heading != document.title else ""
            snippet = self._excerpt(block.text or block.heading, matcher)
        elif document.summary and (
            document.summary_fallback or self._count(document.summary, matcher)
        ):
            snippet = self._excerpt(document.summary, matcher)
        elif document.summary_fallback and document.blocks:
            snippet = self._excerpt(document.blocks[0].text, matcher)
        else:
            snippet = ()
        return Hit(
            document=document,
            score=score,
            secondary=secondary,
            title=self._highlight(document.title, matcher),
            snippet=snippet,
            anchor=anchor,
            section=section,
        )
