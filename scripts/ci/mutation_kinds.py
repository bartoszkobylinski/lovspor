#!/usr/bin/env python3
"""Name the kind of change a mutant makes, from its function and the original (#984).

mutmut 3 records no operator for a mutant: the result artifact has carried
`operator: null` for every survivor since #119, and a killed mutant carried
nothing at all. So a night could say how many mutants died and never of which
kind.

This compares the tokens of the original function with the tokens of the
mutant and names what changed after the operator of mutmut 3.8.0 that makes
such a change (`mutmut/mutation/mutators.py`). The name is read off the text.
mutmut did not report it, so a record carries it as `kind`, never as
`operator`, and a change that fits no rule is `other`, never the nearest guess.

No mutmut import and no shadow tree: two strings in, one name out.
"""

from __future__ import annotations

import io
import textwrap
import tokenize
from typing import NamedTuple

Token = tuple[int, str]

NUMBER = "number"
STRING_XX = "string: XX added"
STRING_LOWER = "string: lower case"
STRING_UPPER = "string: upper case"
STRING_OTHER = "string: other"
DICT_KEY = "dict key: XX added"
BOOLEAN = "True / False swapped"
DEEPCOPY = "deepcopy to copy"
ASSIGN_NONE = "assignment: value to None"
ASSIGN_EMPTY = "assignment: None to empty string"
AUGMENTED_TO_PLAIN = "augmented assignment to plain"
UNARY = "not / ~ removed"
NEGATED = "is / in negated"
ARG_NONE = "argument: replaced by None"
ARG_REMOVED = "argument: removed"
STRING_METHOD = "string method swapped"
LAMBDA = "lambda: body replaced"
BREAK_CONTINUE = "break / continue swapped"
OP_ARITHMETIC = "operator: arithmetic"
OP_COMPARISON = "operator: comparison"
OP_AND_OR = "operator: and / or"
OP_BITWISE = "operator: bitwise"
OP_AUGMENTED = "operator: augmented"
MATCH_CASE = "match: case removed"
TERNARY = "ternary: condition forced"
OTHER = "other"
NO_CHANGE = "no change in the tokens"
UNREADABLE = "unreadable source"

# The end of a statement (NEWLINE) is kept: without it the shared tokens of the
# next statement are matched into the change, and `a = None if b else c` to
# `a = None` in front of an `if` reads as something else.
_LAYOUT = frozenset(
    {
        tokenize.NL,
        tokenize.INDENT,
        tokenize.DEDENT,
        tokenize.COMMENT,
        tokenize.ENDMARKER,
    }
)
_OPENING = frozenset("([{")
_CLOSING = frozenset(")]}")
_ARITHMETIC = frozenset({"+", "-", "*", "/", "//", "%", "**"})
_BITWISE = frozenset({"<<", ">>", "&", "|", "^"})
_COMPARISON = frozenset({"<", "<=", ">", ">=", "==", "!="})
_AUGMENTED = frozenset({f"{op}=" for op in _ARITHMETIC | _BITWISE})
_STRING_METHODS = frozenset(
    pair
    for left, right in (
        ("lower", "upper"),
        ("lstrip", "rstrip"),
        ("find", "rfind"),
        ("ljust", "rjust"),
        ("index", "rindex"),
        ("removeprefix", "removesuffix"),
        ("partition", "rpartition"),
        ("split", "rsplit"),
    )
    for pair in ((left, right), (right, left))
)
_NAME_SWAPS = {
    ("True", "False"): BOOLEAN,
    ("False", "True"): BOOLEAN,
    ("deepcopy", "copy"): DEEPCOPY,
    ("and", "or"): OP_AND_OR,
    ("or", "and"): OP_AND_OR,
    ("break", "return"): BREAK_CONTINUE,
    ("continue", "break"): BREAK_CONTINUE,
}
_TERNARY_TAILS = (["and", "False"], ["or", "True"])
# How far back a `lambda` is looked for from the colon its body follows.
_LAMBDA_REACH = 40


class Change(NamedTuple):
    """The tokens two functions do not share, and the shared ones around them."""

    removed: list[Token]
    added: list[Token]
    before: list[Token]
    after: list[Token]


def tokens(source: str) -> list[Token] | None:
    """A function's tokens without indentation and comments; None when it does not tokenize.

    The name after the first `def` is blanked: the shadow tree names the
    original `x_f__mutmut_orig` and a mutant `x_f__mutmut_7`, and that is not
    the mutation.
    """
    reader = io.StringIO(textwrap.dedent(source)).readline
    try:
        found = [
            (token.type, token.string)
            for token in tokenize.generate_tokens(reader)
            if token.type not in _LAYOUT
        ]
    except (tokenize.TokenError, SyntaxError, ValueError):
        return None
    for index, (_, text) in enumerate(found[:-1]):
        if text == "def":
            found[index + 1] = (tokenize.NAME, "_")
            break
    return found


def change(original: list[Token], mutant: list[Token]) -> Change:
    """Cut the common start and the common end off both token lists."""
    shortest = min(len(original), len(mutant))
    start = 0
    while start < shortest and original[start] == mutant[start]:
        start += 1
    end = 0
    while end < shortest - start and original[-1 - end] == mutant[-1 - end]:
        end += 1
    return Change(
        removed=original[start : len(original) - end],
        added=mutant[start : len(mutant) - end],
        before=original[:start],
        after=original[len(original) - end :],
    )


def _texts(found: list[Token]) -> list[str]:
    return [text for _, text in found]


def _brackets(found: list[Token]) -> list[int]:
    """+1 for each opening bracket, -1 for each closing one, 0 for anything else.

    Operator tokens only: the text of an f-string between two fields is a token
    too, and it can be a lone bracket.
    """
    return [
        (text in _OPENING) - (text in _CLOSING) if kind == tokenize.OP else 0
        for kind, text in found
    ]


def _depth(found: list[Token]) -> int:
    """How many brackets are open after these tokens."""
    return sum(_brackets(found))


def _body(literal: str) -> str:
    """A string literal without its prefix letters and its quotes."""
    quoted = literal.lstrip("rRbBuUfF")
    return quoted[1:-1]


def _string(old: str, new: str) -> str:
    before, after = _body(old), _body(new)
    if after == f"XX{before}XX":
        return STRING_XX
    if after.lower() != before.lower():
        return STRING_OTHER
    gained = sum(map(str.islower, after)) - sum(map(str.islower, before))
    return STRING_LOWER if gained > 0 else STRING_UPPER


def _operator(old: str, new: str) -> str:
    if old in _AUGMENTED:
        return AUGMENTED_TO_PLAIN if new == "=" else OP_AUGMENTED
    for members, name in (
        (_COMPARISON, OP_COMPARISON),
        (_ARITHMETIC, OP_ARITHMETIC),
        (_BITWISE, OP_BITWISE),
    ):
        if old in members:
            return name
    return OTHER


def _names(found: Change, old: str, new: str) -> str:
    """One name replaced by another."""
    if (old, new) in _STRING_METHODS and _texts(found.before)[-1:] == ["."]:
        return STRING_METHOD
    return DICT_KEY if new == f"{old}XX" else _NAME_SWAPS.get((old, new), OTHER)


def _one_for_one(found: Change) -> str:
    """One token replaced by one token."""
    (old_type, old), (new_type, new) = found.removed[0], found.added[0]
    if new == "None":
        return _to_none(found)
    both = old_type if old_type == new_type else None
    if both == tokenize.NUMBER:
        return NUMBER
    if both == tokenize.STRING:
        return _string(old, new)
    if both == tokenize.OP:
        return _operator(old, new)
    return _names(found, old, new) if both == tokenize.NAME else _from_none(found)


def _from_none(found: Change) -> str:
    """`None` replaced by a literal: `a = None` to `a = ""`, `lambda: None` to `lambda: 0`."""
    if _texts(found.removed) != ["None"]:
        return OTHER
    if _after_lambda(found.before):
        return LAMBDA
    statement = _depth(found.before) == 0 and _texts(found.before)[-1:] == ["="]
    return ASSIGN_EMPTY if statement and _texts(found.added) == ['""'] else OTHER


def _after_lambda(before: list[Token]) -> bool:
    """Whether the change starts right after the colon of a `lambda`."""
    texts = _texts(before)
    return texts[-1:] == [":"] and "lambda" in texts[-_LAMBDA_REACH:]


def _to_none(found: Change) -> str:
    """An expression replaced by `None`."""
    if _after_lambda(found.before):
        return LAMBDA
    if _depth(found.before) > 0:
        return ARG_NONE
    return ASSIGN_NONE if _texts(found.before)[-1:] == ["="] else OTHER


def _negation(found: Change) -> str | None:
    """A lone `not` or `~` that came or went, alone or with the brackets around it."""
    removed, added = _texts(found.removed), _texts(found.added)
    comparison = _texts(found.before)[-1:] == ["is"] or _texts(found.after)[:1] == ["in"]
    if (removed, added) == ([], ["not"]) and comparison:
        return NEGATED
    if (removed, added) == (["not"], []):
        return NEGATED if comparison else UNARY
    if (removed, added) == (["~"], []):
        return UNARY
    bracketed = removed[:2] in (["(", "not"], ["(", "~"]) and removed[2:] == [*added, ")"]
    return UNARY if bracketed else None


def _top_level_comma(found: list[Token]) -> bool:
    depth = 0
    for (_, text), step in zip(found, _brackets(found), strict=True):
        if text == "," and depth == 0:
            return True
        depth += step
    return False


def _lead(found: Change) -> str | None:
    """The token in front of an expression replaced by a `None` it began or ended with.

    `f(a if b else None)` to `f(None)` leaves the `None` among the shared
    tokens, so the change reads as a deletion. None when the tokens around
    the deletion do not have that shape.
    """
    before, after = _texts(found.before), _texts(found.after)
    lead = before[-2:-1] if before[-1:] == ["None"] else before[-1:]
    kept = before[-1:] == ["None"] or after[:1] == ["None"]
    return lead[0] if kept and lead in (["("], [","], ["="]) else None


def _deletion(found: Change) -> str:
    """Tokens removed and none added."""
    removed = _texts(found.removed)
    lead = _lead(found)
    if _depth(found.before) > 0:
        # A removed argument takes a comma with it; a replaced one does not.
        replaced = lead is not None and not _top_level_comma(found.removed)
        return ARG_NONE if replaced else ARG_REMOVED
    if lead == "=":
        return ASSIGN_NONE
    cases = "case" in (removed[0], removed[-1]) and "match" in _texts(found.before)
    return MATCH_CASE if cases else OTHER


def _ternary(found: Change) -> bool:
    """`a if c else b` to `a if (c) and False else b`, or `(c) or True`.

    A condition that ends in the literal the mutation appends shares it with
    the mutant, so up to two tokens are taken back from the shared end.
    """
    shared = _texts(found.after)
    for back in range(3):
        removed = [*_texts(found.removed), *shared[:back]]
        added = [*_texts(found.added), *shared[:back]]
        if any(added == ["(", *removed, ")", *tail] for tail in _TERNARY_TAILS):
            return True
    return False


def kind(found: Change) -> str:
    """The name of a change."""
    if not found.removed and not found.added:
        return NO_CHANGE
    if len(found.removed) == 1 and len(found.added) == 1:
        return _one_for_one(found)
    negation = _negation(found)
    if negation is not None:
        return negation
    if _texts(found.added) == ["None"]:
        return _to_none(found)
    if not found.added:
        return _deletion(found)
    return TERNARY if _ternary(found) else OTHER


class Described(NamedTuple):
    """A mutant's kind, and the tokens it took out and put in."""

    kind: str
    removed: str
    added: str


def _shown(found: list[Token]) -> str:
    """Tokens on one line, statement ends left out."""
    return " ".join(text for kind, text in found if kind != tokenize.NEWLINE)


def describe(original: str, mutant: str) -> Described:
    """Name the change between an original function and one mutant of it."""
    first, second = tokens(original), tokens(mutant)
    if first is None or second is None:
        return Described(UNREADABLE, "", "")
    found = change(first, second)
    return Described(kind(found), _shown(found.removed), _shown(found.added))
