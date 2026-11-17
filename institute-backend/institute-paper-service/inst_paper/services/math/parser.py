"""Precedence parser: formula tokens -> structure tree -> LaTeX.

It never simplifies, reorders or evaluates; it only changes notation. Whenever the source allows more than one reading
(1/2x, sin x/x, a/b/c, x2, e^x+1, √x², two numbers side by side) it raises Ambiguous instead of guessing.
"""
import re

from .lexer import Tok, classify, constant_group, lex
from .symbols import ALLOWED_COMMANDS, FUNCTIONS

MAX_DEPTH = 30
MAX_TOKENS = 400
FACTOR_START = {"NUM", "VAR", "POINTS", "UNIT", "GREEK", "VFRAC", "ATOM", "VEC", "HAT", "OPEN", "BAR", "ROOT", "PARTIAL", "BIG", "FUNC", "PREFIX",
                "SUP"}
INTEGRALS = {r"\int", r"\iint", r"\iiint", r"\oint"}
PAIRS = {"(": ")", "[": "]", "{": "}", "⌊": "⌋", "⌈": "⌉"}       # ⌊x⌋ floor, ⌈x⌉ ceiling
_DELIM_LATEX = {"{": r"\{", "}": r"\}", "⌊": r"\lfloor ", "⌋": r"\rfloor ", "⌈": r"\lceil ", "⌉": r"\rceil "}
UNIT_LETTERS = set("msgJNKMAVWCTLh")
PREFIX_LETTERS = set("mkMGnpc")     # milli, kilo, mega, giga, nano, pico, centi


class Ambiguous(Exception):
    """More than one mathematical reading: keep the original text and ask the provider. `code` is a stable reason code."""

    def __init__(self, message: str, code: str = "AMBIGUOUS_NOTATION"):
        super().__init__(message)
        self.code = code


class Unreadable(Exception):
    """Not a formula this engine understands: keep the original text."""

    def __init__(self, message: str, code: str = "UNSUPPORTED_NOTATION"):
        super().__init__(message)
        self.code = code


def parse_tokens(toks: list[Tok], depth: int = 0, *, molar: bool = False, physics: bool = False,
                 context_units: frozenset = frozenset(), unit_answer: bool = False):
    """`molar`: the paper's authoritative subject is Chemistry, so an uppercase M after a number is the molar unit. Off by default:
    every other (or unknown) subject treats M as a letter."""
    if depth > MAX_DEPTH:
        raise Ambiguous("The expression is nested too deeply to convert safely.", "EXPRESSION_TOO_COMPLEX")
    if len(toks) > MAX_TOKENS:
        raise Ambiguous("The expression is too long to convert safely.", "EXPRESSION_TOO_COMPLEX")
    if not toks:
        raise Unreadable("an empty group")
    parser = _Parser(toks, depth, molar, physics, context_units)
    parser.unit_answer = unit_answer and depth == 0
    return parser.parse()


def parse_text(text: str, depth: int = 0, *, molar: bool = False):
    return parse_tokens(classify(lex(text)), depth, molar=molar)


ANGLES = {r"\theta", r"\phi", r"\varphi", r"\alpha", r"\beta", r"\gamma"}
TRIG = {"sin", "cos", "tan", "cot", "sec", "csc", "cosec"}


class _Parser:
    def __init__(self, toks: list[Tok], depth: int, molar: bool, physics: bool = False, context_units: frozenset = frozenset()):
        """`physics`: the paper's subject is Physics (an angle is the argument of a trigonometric function). `context_units`: unit
        letters the rest of the field establishes as units (see __init__._context_units)."""
        self.t, self.i, self.depth, self.molar = toks, 0, depth, molar
        self.physics, self.context_units = physics, context_units
        self.unit_answer = False        # the whole text answers "which unit?": a lone unit symbol with a power is that unit
        self.unit_letters = UNIT_LETTERS if molar else UNIT_LETTERS - {"M"}

    # ------------------------------------------------------------------ helpers
    def peek(self, k: int = 0) -> Tok | None:
        j = self.i + k
        return self.t[j] if j < len(self.t) else None

    def at(self, kind: str, value=None, k: int = 0, glued: bool = False) -> bool:
        tok = self.peek(k)
        return tok is not None and tok.kind == kind and (value is None or tok.value == value) and not (glued and tok.space)

    def take(self) -> Tok:
        tok = self.t[self.i]
        self.i += 1
        return tok

    def sub(self, toks: list[Tok]):
        return parse_tokens(toks, self.depth + 1, molar=self.molar, physics=self.physics, context_units=self.context_units)

    def closing(self, start: int) -> int:
        """Index of the bracket closing the one at `start`."""
        stack = []
        for j in range(start, len(self.t)):
            tok = self.t[j]
            if tok.kind == "OPEN":
                stack.append(tok.value)
            elif tok.kind == "CLOSE":
                if not stack:
                    raise Unreadable(f"the bracket '{tok.raw}' does not match")
                opener = stack.pop()
                interval = not stack and opener in "([" and tok.value in ")]" and any(
                    x.kind == "COMMA" for x in self.t[start + 1:j])               # [0, 2π), (0, 1]
                if PAIRS[opener] != tok.value and not interval:
                    raise Unreadable(f"the bracket '{tok.raw}' does not match")
                if not stack:
                    return j
        raise Unreadable("a bracket is not closed")

    def is_diff(self, k: int = 0) -> bool:
        """dx, dy, dθ, dA: the differential ending an integrand (k: look ahead)."""
        return self.at("VAR", "d", k=k) and (self.at("VAR", k=k + 1, glued=True) or self.at("GREEK", k=k + 1, glued=True))

    # ------------------------------------------------------------------ grammar
    def parse(self):
        node = self.relation()
        if self.i != len(self.t):
            raise Unreadable(f"unexpected '{self.peek().raw}'")
        return node

    def relation(self, sd: bool = False):
        parts = [self.expr(sd)]
        while self.at("REL"):
            parts += [self.take().value, self.expr(sd)]
        if len(parts) == 1:
            return parts[0]
        if any(p[0] == "quantity" and p[1] is None for p in parts[::2]):
            # J s⁻¹ = W, N m = J: a side made only of units makes a lone unit symbol on the other side a unit too
            parts = [("quantity", None, [("unit", p[1])]) if isinstance(p, tuple) and p[0] == "var" and p[1] in self.unit_letters else p
                     for p in parts]
        return ("rel", parts)

    def expr(self, sd: bool = False):
        items = []
        op = self.take().value if self.at("ADD") else None
        items.append((op, self.term(sd)))
        while self.at("ADD"):
            op = self.take().value
            items.append((op, self.term(sd)))
        return items[0][1] if len(items) == 1 and items[0][0] is None else ("add", items)

    def term(self, sd: bool = False):
        node = self.factorseq(sd)
        slashes = 0
        while True:
            if self.at("MUL") and not (sd and self.is_diff(1)):
                op = self.take().value
                node, slashes = ("op", node, op, self.factorseq(sd)), 0
            elif self.at("MUL") and sd and self.is_diff(1):
                return node                                                   # ∮ E · dA: the dot belongs to the differential
            elif self.at("SLASH"):
                self.take()
                slashes += 1
                if slashes > 1:
                    raise Ambiguous("Several '/' in a row (like a/b/c) can be read in more than one way; add brackets.", "AMBIGUOUS_FRACTION")
                if node[0] == "op" and node[2] == r"\div":
                    # 12 ÷ 4/2: left to right it is (12 ÷ 4)/2; drawn as 12 ÷ (4/2) it would change the value
                    raise Ambiguous("'÷' followed by '/' can be read in more than one way; add brackets.", "AMBIGUOUS_FRACTION")
                left = node[3] if node[0] == "op" else node
                last = left[1][-1] if left[0] == "seq" else left
                if last[0] in ("int", "bigop", "lim"):
                    raise Ambiguous("It is unclear whether the fraction is inside the integral, sum or limit; add brackets.", "AMBIGUOUS_FRACTION")
                if last[0] == "unit" and self._unit_candidate():                 # kg/m³, mol/L: a unit word over a unit
                    node = _replace_last(node, ("quantity", None, [last, "/", self.unit_factor()]))
                    continue
                if last[0] == "quantity" and self.at("NUM"):                   # 12 m / 4 s: a quantity divided by a quantity
                    right = self.factorseq(sd)
                    if right[0] != "quantity":
                        raise Ambiguous("It is unclear how much of the text after '/' is in the denominator; add brackets.",
                                        "AMBIGUOUS_FRACTION")
                    node = _replace_last(node, ("frac", last, right))
                    continue
                if last[0] == "quantity":                                     # 2 m/s²: a unit, written as typed
                    unit = self.unit_factor()
                    quantity = ("quantity", last[1], last[2] + ["/", unit])
                    node = _replace_last(node, quantity)
                    continue
                # sin x/x is ambiguous; cos x/sin x is not: a function over a function is the ratio of the two, as in print
                function_first = last[0] == "func" and not last[5] and not self.at("FUNC")
                science = self.physics or self.molar
                if function_first and not (self.physics and _angle_argument(last) or science and _number_logarithm(last)):
                    raise Ambiguous("It is unclear whether the function applies to the whole fraction (like sin x/x); add brackets.", "AMBIGUOUS_FRACTION")
                start = self.i
                right = self.factorseq(sd)
                if function_first and not _quantity_denominator(right):
                    raise Ambiguous("It is unclear whether the function applies to the whole fraction (like sin θ/2); add brackets.", "AMBIGUOUS_FRACTION")
                if right[0] in ("seq", "points") and not _one_denominator(self.t[start:self.i], self._word_before(start - 1)) \
                        and not ((self.physics or self.molar) and _labelled_denominator(self.t[start:self.i], self.t)):
                    raise Ambiguous("It is unclear how much of the text after '/' is in the denominator (like 1/2x); add brackets.", "AMBIGUOUS_FRACTION")
                if right[0] == "quantity" and right[1] is not None and not any(n[0] in ("unit", "quantity") for n in _walk_nodes(left)):
                    # (3 × 8)/2 L: is it 12 L, or 24 divided by 2 L? A unit after the denominator's number is never absorbed
                    raise Ambiguous("It is unclear whether the unit after the '/' belongs to the denominator or to the whole result; "
                                    "add brackets.", "AMBIGUOUS_FRACTION")
                frac = ("frac", _ungroup(left), _ungroup(right))
                node = ("op", node[1], node[2], frac) if node[0] == "op" else frac
            else:
                return node

    def factorseq(self, sd: bool = False, bare: bool = False):
        """Implicit multiplication. `bare`: the argument of a function written without brackets (sin x, cos 2θ)."""
        factors = []
        while self.peek() is not None and self.peek().kind in FACTOR_START and not (sd and self.is_diff()):
            tok = self.peek()
            if factors and bare and (tok.space or tok.kind in ("FUNC", "BIG")):
                break
            if factors and tok.kind == "NUM":
                raise Ambiguous(f"The number '{tok.raw}' directly follows another term (like x2): it could be a power, an index or a "
                                "product; write it with ², a subscript or a multiplication sign.", "AMBIGUOUS_NUMBER_ADJACENT")
            if not factors and not bare and self.units_start():
                factors.append(("quantity", None, self.units()))            # mol L⁻¹ s⁻¹: units without a number
                continue
            if (factors and not bare and tok.space and _is_number(factors[-1]) and self.unit_ahead()):
                factors[-1] = ("quantity", factors[-1], self.units())       # 5 m s⁻¹, 10⁻³ M, 9 J
                continue
            if factors and not bare and tok.space and factors[-1][0] == "unit" and self._unit_candidate() and tok.kind != "GREEK":
                factors[-1] = ("quantity", None, [factors[-1]] + self.units())   # 40π rad s⁻¹: the unit word opens a unit group
                continue
            if (factors and not bare and tok.space and tok.kind == "VAR" and tok.value in self.unit_letters
                    and factors[-1][0] not in ("unit", "quantity") and not _is_number(factors[-1])):
                end = self._after_scripts(0)
                nxt = self.peek(end)
                if (nxt is None or nxt.kind in ("REL", "CLOSE", "COMMA")) and tok.value in self.context_units:
                    factors[-1] = ("quantity", factors[-1], self.units())   # x(t)=3t²−2t³ m: the field shows m is the metre
                    continue
                if nxt is None or nxt.kind in ("REL", "CLOSE", "COMMA"):
                    # k = 5t N, P = 2y² W: a unit after an algebraic term, or one more variable - cannot be proved either way
                    raise Ambiguous(f"'{tok.raw}' after '{self.t[self.i - 1].raw}' could be a unit or a variable; write the unit after "
                                    "a number, or the variable without a space.", "AMBIGUOUS_UNIT")
            factors.append(self.factor(sd))
        if not factors:
            tok = self.peek()
            raise Unreadable(f"expected a term before '{tok.raw}'" if tok else "the expression ends too early")
        return factors[0] if len(factors) == 1 else ("seq", factors)

    def _word_before(self, slash: int) -> str:
        """The letters typed glued together right before the '/' at `slash` (Tc in 1−Tc/Th), or ""."""
        j, word = slash - 1, ""
        while j >= 0 and self.t[j].kind in ("VAR", "POINTS") and self.t[j].raw.isascii() and self.t[j].raw.isalpha():
            word = self.t[j].raw + word
            if self.t[j].space:
                break
            j -= 1
        return word

    def _unit_candidate(self, k: int = 0) -> bool:
        tok = self.peek(k)
        return tok is not None and (tok.kind == "UNIT" or (tok.kind == "VAR" and tok.value in self.unit_letters)
                                    or (tok.kind == "GREEK" and tok.value == r"\Omega"))

    def _after_scripts(self, k: int) -> int:
        """Index (relative) of the first token after the token at k and its glued superscripts/subscripts."""
        k += 1
        while (x := self.peek(k)) is not None and not x.space and x.kind in ("SUP", "SUB"):
            k += 1
        return k

    def _unit_dot(self, k: int = 0) -> bool:
        """A multiplication dot glued between two units (N·m, kg·m/s): it joins the units like a space does."""
        tok, nxt = self.peek(k), self.peek(k + 1)
        return (tok is not None and tok.kind == "MUL" and tok.raw in "·⋅∙" and nxt is not None and not nxt.space
                and self._unit_candidate(k + 1))

    def _unit_group(self, k: int = 0) -> tuple[int, bool, bool, int]:
        """Spaced (or ·-joined) unit candidates starting at k (N m² C⁻², N·m): (count, any negative power, any unit word), and where
        the group ends."""
        count, negative, word, dotted = 0, False, False, False
        while self._unit_candidate(k) and (count == 0 or self.peek(k).space or dotted):
            tok = self.peek(k)
            word = word or tok.kind == "UNIT"
            end = self._after_scripts(k)
            negative = negative or any(self.peek(j).kind == "SUP" and str(self.peek(j).value).startswith("-") for j in range(k + 1, end))
            count, k = count + 1, end
            dotted = self._unit_dot(k)
            if dotted:
                k += 1
        return count, negative, word, k

    def _side(self, k: int) -> str:
        """What follows an '=' or '+' at relative index k-1: "quantity" (a number with a unit: 500 nm, 3 m s⁻¹), "counted" (a number
        ending the formula before a word: '= 3 half-lives'), "bare" (a number alone) or "other"."""
        if self.at("ADD", k=k):
            k += 1
        if not (self.at("NUM", k=k) or self.at("VFRAC", k=k)):
            return "other"
        num = self.peek(k)
        k = self._after_scripts(k)
        if self.at("MUL", k=k) and self.at("NUM", "10", k=k + 1) and self.at("SUP", k=k + 2, glued=True):   # 5 × 10⁻⁷
            k = self._after_scripts(k + 1)
            num = self.peek(k - 1)
        nxt = self.peek(k)
        if nxt is not None and nxt.space and self._unit_candidate(k):
            return "quantity"
        if nxt is None and num.next_word:
            return "counted"
        return "bare" if nxt is None or nxt.kind in ("REL", "ADD", "CLOSE") else "other"

    def unit_ahead(self) -> bool:
        """After a number and a space: is this a unit? 5 m s⁻¹, 9 J, 2 m/s², 100 Ω. Before '=' or '+' (1 h = 3 half-lives,
        5 × 10⁻⁷ m = 500 nm, 2 m + 3 m) the other side decides: a quantity or a counted number makes it a unit; a bare number
        (3 m = 6) leaves both readings open, so it is flagged. Letters that are never unit symbols (x, y, n ...) are never units."""
        if not self._unit_candidate():
            return False
        if self._prefixed_pair():
            # 6 ms: millisecond; 6 ms⁻¹ / 6 ms^-1: metre per second or per millisecond - never decided silently
            after = self.peek(2)
            if after is not None and not after.space and after.kind in ("SUP", "CARET"):
                raise Ambiguous(f"'{self.peek().raw}{self.peek(1).raw}' with a power can be a prefixed unit (per millisecond) or two "
                                "units (metre per second); write 'm s⁻¹' or 'm/s' for metre per second.", "AMBIGUOUS_UNIT")
            return True
        count, _, word, k = self._unit_group()
        nxt = self.peek(k)
        if nxt is None or nxt.kind == "SLASH" or count > 1 and nxt.kind not in ("ADD", "MUL", "OPEN", "SUP"):
            return True
        if nxt.kind in ("VAR", "UNIT") and nxt.space:
            return True
        if nxt.kind == "REL" and nxt.raw in ("=", "≈", "≠", "<", ">", "≤", "≥", "→", "->"):
            side = self._side(k + 1)
            if side in ("quantity", "counted") or word:
                return True
            if side == "bare":
                raise Ambiguous(f"'{self.peek().raw}' after a number could be a unit or a variable; write the unit with the other "
                                "side's unit too, or the variable without a space.", "AMBIGUOUS_UNIT")
            return False
        if nxt.kind == "ADD":
            return self._side(k + 1) == "quantity" and word or (self._side(k + 1) == "quantity" and self._same_unit(k + 1))
        return False

    def _prefixed_pair(self, k: int = 0) -> bool:
        """'ms', 'ns', 'kN' typed as one word and split into two letters: an SI prefix glued to a unit symbol."""
        a, b = self.peek(k), self.peek(k + 1)
        return (a is not None and b is not None and a.kind == "VAR" and b.kind == "VAR" and not b.space and a.value in PREFIX_LETTERS
                and b.value in self.unit_letters and not (self.peek(k + 2) is not None and not self.peek(k + 2).space
                                                     and self.peek(k + 2).kind in ("VAR", "NUM")))

    def _same_unit(self, k: int) -> bool:
        """2 m + 3 m: the other term carries the same unit symbol."""
        if self.at("ADD", k=k):
            k += 1
        j = k
        while (x := self.peek(j)) is not None and not (x.space and self._unit_candidate(j)):
            if x.kind in ("REL", "ADD"):
                return False
            j += 1
        return self.peek(j) is not None and self.peek(j).value == self.peek().value

    def units_start(self) -> bool:
        """Units written without a number: mol L⁻¹ s⁻¹, mol² L⁻², m s⁻¹, N m⁻¹, N m² C⁻². A group of two or more spaced unit symbols
        with a unit word or a negative power, ending the formula or before a relation; anything else (x y, a b, m n) stays a product."""
        count, negative, word, k = self._unit_group()
        if self.unit_answer and self.i == 0 and count >= 1 and k == len(self.t):
            return True                      # s⁻¹ as the answer to "Unit of rate constant ...?"
        if count < 2 or not (negative or word):
            return False
        if not word and all(self.peek(j).kind == "VAR" and self.peek(j).value.isupper() for j in range(k) if self.peek(j).kind == "VAR"):
            return False                     # M L T⁻² K⁻¹, A B⁻¹: capitals only could be variables or dimensions, never assumed units
        nxt = self.peek(k)
        return nxt is None or nxt.kind in ("REL", "SLASH", "CLOSE")

    def units(self) -> list:
        out = [self.unit_factor()]
        while True:
            if self._unit_dot():                                          # N·m: shown with its dot
                self.take()
                out += ["·", self.unit_factor()]
            elif self.peek() is not None and self.peek().space and self.peek().kind in ("VAR", "UNIT") and (
                    self.peek(1) is None or self.peek(1).kind not in ("REL", "ADD", "OPEN")):
                out.append(self.unit_factor())
            else:
                break
        tok = self.peek()
        if tok is not None and not tok.space and tok.kind in ("VAR", "UNIT"):
            raise Ambiguous(f"'{tok.raw}' is glued to the unit before it, so it is unclear whether it is part of the unit; separate "
                            "the units with a space.", "AMBIGUOUS_UNIT")
        return out

    def unit_factor(self):
        tok = self.peek()
        if tok is None or tok.kind not in ("VAR", "UNIT", "POINTS", "GREEK"):
            raise Unreadable("expected a unit")
        if self._prefixed_pair():
            if self.peek(2) is not None and not self.peek(2).space and self.peek(2).kind in ("SUP", "CARET"):
                raise Ambiguous(f"'{tok.raw}{self.peek(1).raw}' with a power can be a prefixed unit or two units; separate them.",
                                "AMBIGUOUS_UNIT")
            self.take()
            return self.postfix(("unit", tok.value + self.take().value))       # 6 ms: one prefixed unit, upright
        self.take()
        if tok.kind == "GREEK":                                           # 100 Ω: the ohm symbol, upright
            return self.postfix(("unitsym", tok.value))
        return self.postfix(("unit", tok.value))

    def factor(self, sd: bool = False):
        tok = self.peek()
        if tok.kind == "PREFIX":
            self.take()
            return ("prefix", tok.value, self.factor(sd))
        if tok.kind in ("PARTIAL", "VAR"):
            d = self.derivative()
            if d is not None:
                return d
        if tok.kind == "PARTIAL":
            self.take()
            return ("partial", self.factor(sd))
        if tok.kind == "ROOT":
            return self.root()
        if tok.kind == "BIG":
            return self.big(sd)
        if tok.kind == "FUNC":
            return self.func(sd)
        if tok.kind == "SUP":                                           # ⁿCᵣ: a superscript written before its symbol
            self.take()
            return self.postfix(("prescript", self.script(tok.value), self.primary()))
        return self.postfix(self.primary())

    def primary(self):
        if self.peek() is None:
            raise Unreadable("the expression ends too early")
        tok = self.take()
        k = tok.kind
        if k in ("NUM", "VAR", "POINTS", "UNIT", "GREEK", "ATOM", "VEC", "HAT"):
            return (k.lower(), tok.value)
        if k == "VFRAC":
            return ("frac", ("num", str(tok.value[0])), ("num", str(tok.value[1])))
        if k == "OPEN":
            j = self.closing(self.i - 1)
            inner, close = self.t[self.i:j], self.t[j].value
            self.i = j + 1
            return self.group(tok.value, inner, close)
        if k == "BAR":
            j, depth = self.i, 0
            while j < len(self.t) and not (self.t[j].kind == "BAR" and depth == 0):
                depth += {"OPEN": 1, "CLOSE": -1}.get(self.t[j].kind, 0)
                j += 1
            if j >= len(self.t):
                raise Unreadable("a '|' is not closed")
            inner = self.t[self.i:j]
            self.i = j + 1
            if any(x.kind == "SEMI" for x in inner):
                return ("matrix", "vmatrix", self.rows(inner))
            return ("abs", self.sub(inner))
        raise Unreadable(f"unexpected '{tok.raw}'")

    def group(self, open_: str, inner: list[Tok], close: str):
        if not inner:
            raise Unreadable("empty brackets")
        if open_ == "[" and close == "]" and (dims := _dimensions(inner)):
            return ("dims", dims)                                              # [M L² T⁻²]: SI dimension symbols, upright
        top = _top_level(inner)
        if open_ == "[" and inner[0].kind == "OPEN" and inner[0].value == "[":   # [[1,2],[3,4]]
            rows = []
            for part in _split(inner, "COMMA", top):
                if len(part) < 2 or part[0].value != "[" or part[-1].value != "]":
                    raise Unreadable("a matrix row is not in [ ]")
                rows.append([self.sub(e) for e in _split(part[1:-1], "COMMA", _top_level(part[1:-1]))])
            return ("matrix", "bmatrix", _same_width(rows))
        if any(inner[j].kind == "SEMI" for j in top):                         # [1 2; 3 4]
            return ("matrix", "bmatrix" if open_ == "[" else "pmatrix", self.rows(inner))
        if any(inner[j].kind == "COMMA" for j in top):                        # (x, y)
            return ("paren", open_, close, [self.sub(e) for e in _split(inner, "COMMA", top)])
        return ("paren", open_, close, [self.sub(inner)])

    def rows(self, inner: list[Tok]):
        out = []
        for row in _split(inner, "SEMI", _top_level(inner)):
            top = _top_level(row)
            if any(row[j].kind == "COMMA" for j in top):
                cells = _split(row, "COMMA", top)
            else:                                                             # elements separated by spaces
                cells, cur = [], []
                for j, tok in enumerate(row):
                    if (cur and tok.space and j in top and tok.kind == "ADD" and j + 1 < len(row) and not row[j + 1].space):
                        # |1 -2; 3 4|: is the entry -2, or is it 1 - 2? A space decides it; never guessed
                        raise Ambiguous("A matrix entry starts with a sign separated only by a space; use commas between entries.",
                                        "AMBIGUOUS_MATRIX")
                    if cur and tok.space and j in top:
                        cells.append(cur)
                        cur = []
                    cur.append(tok)
                cells.append(cur)
            out.append([self.sub(c) for c in cells])
        return _same_width(out)

    def postfix(self, node):
        tok = self.peek()
        if node[0] == "points" and len(node[1]) > 1 and tok is not None and not tok.space and tok.kind in ("SUP", "SUB", "CARET", "USCORE", "POSTFIX"):
            return ("seq", [("points", node[1][:-1]), self.postfix(("var", node[1][-1]))])
        lo = hi = None
        order = ""
        while (tok := self.peek()) is not None and not tok.space:
            if tok.kind in ("SUP", "CARET"):
                if hi is not None:
                    # 3^2^2, x²^3, a^b^c: a power on a power can be read (3²)² or 3^(2²); never guessed
                    raise Ambiguous("A power is written on top of another power (like 3^2^2); add brackets to show which is meant.",
                                    "AMBIGUOUS_POWER")
                hi = self.script(self.take().value) if tok.kind == "SUP" else self.ascii_script()
                order += "^"
            elif tok.kind in ("SUB", "USCORE"):
                if lo is not None:
                    raise Ambiguous("A subscript is written on another subscript (like a_i_j); write the indices together.", "AMBIGUOUS_POWER")
                lo = self.script(self.take().value) if tok.kind == "SUB" else self.ascii_script()
                order += "_"
            elif tok.kind == "POSTFIX":
                self.take()
                node = _scripted(node, lo, hi, order)
                lo = hi = None
                order = ""
                node = ("post", node, tok.value)
            else:
                break
        return _scripted(node, lo, hi, order)

    def script(self, content: str):
        """The text of a Unicode superscript/subscript run (⁻¹ -> "-1", ᵢ₌₁ -> "i=1"), parsed as a formula of its own. A word written
        as a subscript (Vᵣₘₛ, vₘₐₓ) is a label, set upright."""
        try:
            node = parse_text(content, self.depth + 1, molar=self.molar)
        except Unreadable:
            if content.isascii() and content.isalpha() and len(content) > 1:
                return ("unit", content)
            raise
        return _ungroup(node)

    def ascii_script(self):
        """x^2, x^(n+1), x^-1, a_n, lim_(x→0), R_eq."""
        mark = self.take()
        tok = self.peek()
        if mark.kind == "USCORE" and tok is not None and not tok.space:
            j, word = 0, ""
            while (x := self.peek(j)) is not None and (j == 0 or not x.space) and x.kind in ("VAR", "PROSE", "POINTS", "UNIT") \
                    and str(x.raw).isascii() and str(x.raw).isalpha():
                word += x.raw
                j += 1
            nxt = self.peek(j)
            # v_rms=, R_eq+: a relation ends any label; a longer label (3+ letters) also ends at an operator
            if len(word) >= 2 and (nxt is None or nxt.space or nxt.kind in ("CLOSE", "COMMA", "REL")
                                   or len(word) >= 3 and nxt.kind in ("ADD", "MUL", "SLASH")):
                self.i += j
                if len(word) == 2 and set(word) <= set("ijklmnpqrst"):
                    return ("seq", [("var", word[0]), ("var", word[1])])     # x_ij: two indices, italic
                return ("unit", word)                                         # R_eq, v_max: a label, set upright like Vᵣₘₛ
        if tok is None or tok.space:
            raise Unreadable("'^' or '_' is not followed by anything")
        if tok.kind == "OPEN":
            return _ungroup(self.primary())
        if tok.kind == "ADD" and self.peek(1) is not None and self.peek(1).kind in ("NUM", "VAR", "GREEK"):
            sign = self.take().value
            return ("add", [(sign, (self.take().kind.lower(), self.t[self.i - 1].value))])
        if tok.kind in ("NUM", "VAR", "GREEK", "ATOM"):
            self.take()
            nxt = self.peek()
            if tok.kind == "NUM" and nxt is not None and not nxt.space and nxt.kind in ("VAR", "GREEK", "OPEN", "ROOT", "POINTS", "PROSE"):
                # e^3t, x^2y, 10^2n: is the letter in the power? never guessed
                raise Ambiguous(f"It is unclear what is in the power after '^{tok.raw}' (like e^3t); put the power in brackets.",
                                "AMBIGUOUS_POWER")
            # e^x+C, xe^x−∫e^x dx: an integral or sum, or the constant of integration C ending the expression, is never in a power
            after = self.peek(1)
            ends = nxt is not None and nxt.kind == "ADD" and after is not None and not after.space and (
                after.kind == "BIG" or after.kind == "VAR" and after.value == "C" and self.peek(2) is None)
            if tok.kind == "VAR" and nxt is not None and not nxt.space and nxt.kind in ("ADD", "VAR", "NUM") and not ends:
                raise Ambiguous("It is unclear what is in the power after '^' (like e^x+1); put the power in brackets.", "AMBIGUOUS_POWER")
            return (tok.kind.lower(), tok.value)
        raise Unreadable(f"unexpected '{tok.raw}' after '^' or '_'")

    def func(self, sd: bool):
        name = self.take().value
        power = sub = None
        while (tok := self.peek()) is not None and not tok.space:
            if tok.kind == "SUP":
                self.take()
                power = self.script(tok.value)
            elif tok.kind == "CARET":
                power = self.ascii_script()
            elif tok.kind == "SUB":
                self.take()
                sub = self.script(tok.value)
                if self.at("REL", r"\to", glued=True) and self.at("SUB", k=1, glued=True):   # limₓ→₀
                    self.take()
                    sub = ("rel", [sub, r"\to", self.script(self.take().value)])
                elif self.at("REL", r"\to", glued=True) and (self.at("ATOM", r"\infty", k=1, glued=True) or (
                        self.at("ADD", k=1, glued=True) and self.at("ATOM", r"\infty", k=2, glued=True))):   # limₓ→∞ (∞ has no subscript form)
                    self.take()
                    sign = self.take().value if self.at("ADD") else None
                    self.take()
                    if self.peek() is not None and not self.peek().space:
                        raise Ambiguous("It is unclear where the limit ends (like limₓ→∞x); put a space after ∞.", "BOUND_TYPED_ON_LINE")
                    target = ("atom", r"\infty")
                    sub = ("rel", [sub, r"\to", ("add", [(sign, target)]) if sign else target])
            elif tok.kind == "USCORE":
                sub = self.ascii_script()
            else:
                break
        if name == "lim":
            if sub is None and self.at("OPEN", "("):                         # lim(x→0) ...
                inner = self.primary()
                if inner[0] != "paren" or len(inner[3]) != 1 or inner[3][0][0] != "rel" or r"\to" not in inner[3][0][1]:
                    raise Unreadable("'lim' needs a limit such as x→0")
                sub = inner[3][0]
            if sub is None:                                                  # lim x→2 ...
                var = self.primary()
                if not self.at("REL", r"\to"):
                    raise Unreadable("'lim' needs a limit such as x→0")
                self.take()
                sign = self.take().value if self.at("ADD") else None
                value = self.postfix(self.primary())
                sub = ("rel", [var, r"\to", ("add", [(sign, value)]) if sign else value])
            return ("lim", sub, self.term(sd))
        if self.at("OPEN", "("):
            return self.postfix(("func", name, power, sub, self.primary(), True))
        if name == "det":
            return ("func", name, power, sub, self.factor(sd), False)
        return ("func", name, power, sub, self.factorseq(sd, bare=True), False)

    def root(self):
        index = self.take().value
        if self.at("OPEN", "("):
            return self.postfix(("sqrt", index, _ungroup(self.primary())))
        base = self.primary()
        tok = self.peek()
        if tok is not None and not tok.space and tok.kind in ("SUP", "CARET"):
            raise Ambiguous("It is unclear whether the power is under the root (like √x²); add brackets.", "AMBIGUOUS_POWER")
        return ("sqrt", index, base)

    def big(self, sd: bool):
        cmd = self.take().value
        lo = hi = None
        while (tok := self.peek()) is not None and not tok.space:
            if tok.kind == "SUB":
                self.take()
                lo = self.script(tok.value)
            elif tok.kind == "SUP":
                self.take()
                hi = self.script(tok.value)
            elif tok.kind == "USCORE":
                lo = self.ascii_script()
            elif tok.kind == "CARET":
                hi = self.ascii_script()
            else:
                break
        nxt = self.peek()
        if (lo is None) != (hi is None) and nxt is not None and not nxt.space:
            # ∫₀π, ∑ᵢ₌₁n: the second limit is typed on the line. Reading it as a factor would silently lose the limit.
            raise Ambiguous("A limit seems to be typed on the line right after the other limit (like ∫₀π): write both limits as "
                            "superscript/subscript (∫₀^π) or put a space after the limit.", "BOUND_TYPED_ON_LINE")
        after = self.peek(1)
        if (lo is None and hi is None and nxt is not None and not nxt.space and nxt.kind == "NUM"
                and ((after is not None and not after.space and after.kind in ("NUM", "GREEK", "VAR", "POINTS"))
                     or (len(nxt.raw) > 1 and nxt.raw.startswith("0") and "." not in nxt.raw))):
            # ∫0π, ∫01: two limits typed on the line with no scripts (copying lost them), or the integrand starting with a number
            # (∫2x): the formatting that decided it was lost, so it is never decided here
            raise Ambiguous("Numbers right after the sign may be its limits typed on the line (like ∫0π for ∫₀^π) or the start of "
                            "what is integrated: choose the reading meant.", "BOUND_TYPED_ON_LINE")
        if cmd == r"\int" and (lo is None) != (hi is None):
            raise Ambiguous("The integral has only one limit; write both limits (like ∫₀^π) or none.", "BOUND_INCOMPLETE")
        if cmd in INTEGRALS:
            if self.is_diff() and self.at("SLASH", k=2):                      # ∫ dx/(x² + 4)
                self.take()
                d = ("seq", [("var", "d"), self.primary()])
                self.take()
                den = self.factorseq()
                if den[0] == "seq":
                    raise Ambiguous("It is unclear how much of the text after '/' is in the denominator; add brackets.", "AMBIGUOUS_FRACTION")
                return ("int", cmd, lo, hi, ("frac", d, _ungroup(den)), [], None)
            body = self.expr(sd=True) if (self.peek() is not None and not self.is_diff()) else None
            dot = None
            if body is not None and self.at("MUL") and self.is_diff(1):        # ∮ E · dA (a dot product with the area element)
                dot = self.take().value
            diffs = []
            while self.is_diff():
                self.take()
                diffs.append(self.primary())
            return ("int", cmd, lo, hi, body, diffs, dot)
        return ("bigop", cmd, lo, hi, self.term(sd))

    def derivative(self):
        """dy/dx, d²y/dx², d/dx, ∂f/∂x, ∂²f/∂x², ∂²f/∂x∂y, and the derivative of a bracketed expression d(x² + 1)/dx, d[sin(3x)]/dx,
        d²(x⁴)/dx² -> ("deriv", symbol, order, numerator (variable, bracket or None), [(denominator variable, power), ...])."""
        j, t = self.i, self.t

        def tok(k):
            return t[k] if k < len(t) else None

        def symbol(k):
            x = tok(k)
            if x is None:
                return None
            if x.kind == "PARTIAL":
                return "partial"
            return "d" if x.kind == "VAR" and x.value == "d" else None

        def power(k):
            """A digit power glued at k (² or ^2) -> (power, next index)."""
            if (x := tok(k)) is not None and x.kind == "SUP" and not x.space and x.value.isdigit():
                return x.value, k + 1
            if (x := tok(k)) is not None and x.kind == "CARET" and not x.space and (y := tok(k + 1)) is not None and y.kind == "NUM" \
                    and not y.space and y.value.isdigit():
                return y.value, k + 2                                          # d^2x/dt^2: the order typed with a caret
            return None, k

        sym = symbol(j)
        if sym is None:
            return None
        order, j = power(j + 1)
        num = None
        if (x := tok(j)) is not None and not x.space and x.kind in ("VAR", "GREEK"):
            num, j = (x.kind.lower(), x.value), j + 1
        elif x is not None and not x.space and x.kind == "OPEN":
            # d(…)/dx: the bracket glued to d is what is differentiated - only when the '/ d<variable>' follows it
            close = self.closing(j)
            if (y := tok(close + 1)) is None or y.kind != "SLASH" or symbol(close + 2) != sym:
                return None
            start, self.i = self.i, j
            num = self.primary()
            self.i, j = start, close + 1
        if (x := tok(j)) is None or x.kind != "SLASH":
            return None
        j += 1
        dens = []
        while symbol(j) == sym and (x := tok(j + 1)) is not None and not x.space and x.kind in ("VAR", "GREEK") \
                and (not dens or not tok(j).space):
            p, k = power(j + 2)
            dens.append(((x.kind.lower(), x.value), p))
            j = k
        if not dens:
            return None
        # the order above the bar is the total order below it: d²y/dx², ∂²f/∂x∂y; anything else is never guessed
        if order != dens[0][1] if len(dens) == 1 else int(order or 1) != sum(int(p or 1) for _, p in dens):
            raise Ambiguous("The order of the derivative differs above and below the fraction bar.", "AMBIGUOUS_DERIVATIVE")
        self.i = j
        return ("deriv", sym, order, num, dens)


DIMENSIONS = "MLTINJ"   # mass, length, time, electric current, amount of substance, luminous intensity; temperature is Θ


def _dimensions(inner: list[Tok]) -> list[list] | None:
    """[M L² T⁻²], [ML⁻¹T⁻²], [M⁰ L T⁻²], [Θ]... : an explicitly bracketed dimensional formula, decision D4. Only the SI dimension
    symbols M L T I N J Θ, each with an optional integer power, and at least one power written (so [MLT], [ABC], [M] are not taken).
    K is a unit (kelvin), not the temperature dimension, so [M L T⁻² K⁻¹] is not a dimensional formula."""
    out: list[list] = []
    powered = False
    for tok in inner:
        if tok.kind in ("VAR", "POINTS") and tok.raw.isascii() and tok.raw.isalpha() and all(c in DIMENSIONS for c in tok.raw):
            if len(tok.raw) > 1 and len(set(tok.raw)) != len(tok.raw):
                return None
            out += [[c, None] for c in tok.raw]
        elif tok.kind == "GREEK" and tok.raw == "Θ":
            out.append(["Θ", None])
        elif tok.kind == "SUP" and not tok.space and out and out[-1][1] is None and re.fullmatch(r"-?\d+", str(tok.value)):
            out[-1][1] = str(tok.value)
            powered = True
        else:
            return None
    if not powered or len({s for s, _ in out}) != len(out):
        return None
    return out


def _walk_nodes(node):
    if isinstance(node, tuple):
        yield node
        for x in node[1:]:
            yield from _walk_nodes(x)
    elif isinstance(node, list):
        for x in node:
            yield from _walk_nodes(x)


def _scripted(base, lo, hi, order: str):
    """One node for a base and its scripts. Subscript and superscript on the same base form ONE stacked node ([t²]₁³ is the
    evaluation bracket from 1 to 3, x₁² is x-one squared), written in the order typed."""
    if lo is not None and hi is not None:
        return ("subsup", base, lo, hi, order)
    if hi is not None:
        return ("sup", base, hi)
    if lo is not None:
        return ("sub", base, lo)
    return base


def _is_number(node) -> bool:
    return node[0] == "num" or (node[0] in ("sup", "post") and node[1][0] == "num") or (node[0] == "op" and _is_number(node[3]))


LABELLED = re.compile(r"[A-Z][a-z]{1,2}")


def _one_denominator(toks: list[Tok], before: str = "") -> bool:
    """Juxtaposed factors after '/' that are still ONE denominator: Δ glued to one symbol (NΔΦ/Δt: the change in t, never Δ times
    t), a number with π and the vacuum constants (h/2π, 1/4πε₀), or labelled quantities - the same capital with different small
    labels on both sides (Tc/Th, Vs/Vp, Ns/Np). Any other glued word (a/bc, R/KT: a/(bc) or (a/b)c) and a number before a variable
    (1/2x, 1/2mv²) stay ambiguous. `before`: the letters glued right before the '/'."""
    if any(t.space for t in toks[1:]):
        return False
    if constant_group(toks):
        return True
    core = list(toks)
    while core and core[-1].kind in ("SUP", "SUB"):                       # a/bc², Δx²: the power belongs to the last letter
        core.pop()
    if len(core) == 2 and core[0].kind == "GREEK" and core[0].value == r"\Delta" and core[1].kind in ("VAR", "GREEK"):
        return True
    word = "".join(t.raw for t in toks)
    return (all(t.kind in ("VAR", "POINTS") for t in toks) and bool(LABELLED.fullmatch(word)) and bool(LABELLED.fullmatch(before))
            and word[0] == before[0] and word != before)


def _labelled_denominator(toks: list[Tok], whole: list[Tok]) -> bool:
    """Labelled quantities in Physics and Chemistry, proved by the formula itself:
      [A]t when the same bracket also carries a subscript label in the formula (ln([A]₀/[A]t), 1/[A]t = 1/[A]₀ + kt): t labels it;
      xA when the same capital follows another small letter in the formula (pA = pA°/xA): the capital is the component's label."""
    raws = [t.raw for t in toks]
    if (len(toks) == 4 and raws[0] == "[" and raws[2] == "]" and toks[3].kind == "VAR" and toks[3].raw.islower()
            and not any(t.space for t in toks[1:])):
        return any(whole[n].raw == "[" and whole[n + 1].raw == raws[1] and whole[n + 2].raw == "]" and whole[n + 3].kind == "SUB"
                   and not whole[n + 3].space for n in range(len(whole) - 3))
    while len(toks) > 2 and toks[-1].kind in ("POSTFIX", "SUP", "SUB") and not toks[-1].space:
        toks = toks[:-1]                                                  # pA°: the standard-state mark belongs to the quantity
    if len(toks) == 2 and toks[0].kind == toks[1].kind == "VAR" and toks[0].raw.islower() and toks[1].raw.isupper() and not toks[1].space:
        label = toks[1].raw
        others = {whole[n].raw for n in range(len(whole) - 1) if whole[n].kind == "VAR" and whole[n].raw.islower()
                  and whole[n + 1].raw == label and whole[n + 1].kind == "VAR" and not whole[n + 1].space
                  and (n + 2 >= len(whole) or not (whole[n + 2].kind == "VAR" and not whole[n + 2].space))}
        return len(others - {toks[0].raw}) >= 1
    return False


def _number_logarithm(node) -> bool:
    """ln2/k, log 10/t: the logarithm of a bare number (Physics, Chemistry) - the quantity after '/' (a letter, which carries units)
    cannot be inside a logarithm. A number after '/' (log 3/2) keeps both readings open (checked by _quantity_denominator)."""
    return node[1] in ("ln", "log", "lg") and node[4][0] == "num"


def _angle_argument(node) -> bool:
    """sinθ, sin²θ, sin2θ, cos 2φ: a trigonometric function of one angle symbol, with at most a number before it."""
    arg = node[4]
    parts = arg[1] if arg[0] == "seq" else [arg]
    return (node[1] in TRIG and parts[-1][0] == "greek" and parts[-1][1] in ANGLES
            and all(p[0] == "num" for p in parts[:-1]) and len(parts) <= 2)


def _quantity_denominator(node) -> bool:
    """u²sin²θ/(2g), u²sinθ/g: in Physics an angle divided by a quantity (a Latin letter) cannot be a trigonometric argument, which
    must be dimensionless; a number (sin θ/2: a half angle) or an angle (sin θ/θ) in the denominator keeps both readings open."""
    nodes = list(_walk_nodes(node))
    return any(n[0] == "var" for n in nodes) and not any(n[0] == "greek" and n[1] in ANGLES for n in nodes)


def _replace_last(node, new):
    if node[0] == "op":
        return ("op", node[1], node[2], _replace_last(node[3], new))
    if node[0] == "seq":
        return ("seq", node[1][:-1] + [new])
    return new


def _top_level(toks: list[Tok]) -> set[int]:
    out, depth, bars = set(), 0, 0
    for j, tok in enumerate(toks):
        if tok.kind == "CLOSE":
            depth -= 1
        if depth == 0 and bars % 2 == 0:
            out.add(j)
        if tok.kind == "OPEN":
            depth += 1
        elif tok.kind == "BAR":
            bars += 1
    return out


def _split(toks: list[Tok], kind: str, top: set[int]) -> list[list[Tok]]:
    parts, cur = [], []
    for j, tok in enumerate(toks):
        if tok.kind == kind and j in top:
            parts.append(cur)
            cur = []
        else:
            cur.append(tok)
    parts.append(cur)
    if any(not p for p in parts):
        raise Unreadable("an empty item")
    return parts


def _same_width(rows):
    if len({len(r) for r in rows}) != 1:
        raise Ambiguous("The rows of the matrix have different numbers of entries.", "AMBIGUOUS_MATRIX")
    return rows


def _ungroup(node):
    """Brackets that only group (the numerator of a/b, the argument of a root, the power x^(n+1)) become braces in LaTeX."""
    if node[0] == "paren" and node[1] == "(" and len(node[3]) == 1:
        return node[3][0]
    return node


# ---------------------------------------------------------------------- LaTeX
def _cat(parts: list[str]) -> str:
    out = ""
    for p in parts:
        if out and re.search(r"\\[A-Za-z]+$", out) and p[:1].isalpha():
            out += " "
        out += p
    return out


def _base(node) -> str:
    s = to_latex(node)
    simple = (node[0] in ("var", "unit", "greek", "atom", "vec", "hat", "paren", "abs", "matrix", "dims") or (node[0] == "num" and len(s) == 1)
              or (node[0] == "prescript" and node[2][0] in ("var", "greek")))
    return s if simple else "{" + s + "}"


def to_latex(node) -> str:
    k = node[0]
    if k in ("num", "var", "points", "greek", "atom"):
        return node[1]
    if k == "dims":
        return "[" + r"\,".join((r"\Theta" if s == "Θ" else r"\mathrm{" + s + "}") + ("^{" + p + "}" if p is not None else "")
                                for s, p in node[1]) + "]"
    if k == "unitsym":
        return r"\mathrm{" + node[1] + "}"
    if k == "unit":
        if node[1] == "°C":
            return r"{}^{\circ}\mathrm{C}"
        if node[1][1:] == "Ω":
            return r"\mathrm{" + node[1][0] + r"}\mathrm{\Omega}"
        if node[1][:1] == "μ":
            return r"\mu" + (r"\Omega" if node[1][1:] == "Ω" else r"\mathrm{" + node[1][1:] + "}")
        return r"\mathrm{" + node[1] + "}"
    if k == "quantity":
        out = to_latex(node[1]) if node[1] is not None else ""
        for u in node[2]:
            if u in ("/", "·"):
                out += "/" if u == "/" else r" \cdot "
            else:
                out += ("" if out.endswith(("/", r"\cdot ")) or not out else r"\,") + to_latex(u)
        return out
    if k == "vec":
        return r"\vec{" + node[1] + "}"
    if k == "hat":
        return r"\hat{" + node[1] + "}"
    if k == "frac":
        return r"\frac{" + to_latex(node[1]) + "}{" + to_latex(node[2]) + "}"
    if k == "subsup":
        _, base, lo, hi, order = node
        if base[0] == "paren" and len(base[3]) == 1:                       # [t²]₁³: the bracket grows with its contents
            o, c = (r"\{", r"\}") if base[1] == "{" else (base[1], base[2])
            b = r"\left" + o + to_latex(base[3][0]) + r"\right" + c
        else:
            b = _base(base)
        parts = {"_": "_{" + to_latex(lo) + "}", "^": "^{" + to_latex(hi) + "}"}
        return b + "".join(parts[x] for x in order)
    if k in ("sup", "sub"):
        return _base(node[1]) + ("^{" if k == "sup" else "_{") + to_latex(node[2]) + "}"
    if k == "post":
        return _cat([to_latex(node[1]), node[2]])
    if k == "prescript":
        return "{}^{" + to_latex(node[1]) + "}" + to_latex(node[2])
    if k == "paren":
        o, c = _DELIM_LATEX.get(node[1], node[1]), _DELIM_LATEX.get(node[2], node[2])
        return o + ", ".join(to_latex(x) for x in node[3]) + c
    if k == "abs":
        return r"\lvert " + to_latex(node[1]) + r" \rvert"
    if k == "matrix":
        body = r" \\ ".join(" & ".join(to_latex(x) for x in row) for row in node[2])
        return r"\begin{" + node[1] + "}" + body + r"\end{" + node[1] + "}"
    if k == "seq":
        return _cat([(r"\," if n and (x[0] == "unit" or (x[0] == "quantity" and x[1] is None)) else "") + to_latex(x)
                     for n, x in enumerate(node[1])])
    if k == "op":
        return to_latex(node[1]) + " " + node[2] + " " + to_latex(node[3])
    if k == "add":
        out = ""
        for n, (op, term) in enumerate(node[1]):
            t = to_latex(term)
            out += (op + t if op else t) if n == 0 else " " + op + " " + t
        return out
    if k == "rel":
        return " ".join(p if isinstance(p, str) else to_latex(p) for p in node[1])
    if k == "func":
        _, name, power, sub, arg, parens = node
        head = FUNCTIONS[name] + ("^{" + to_latex(power) + "}" if power is not None else "") + ("_{" + to_latex(sub) + "}" if sub is not None else "")
        return head + to_latex(arg) if parens else head + " " + to_latex(arg)
    if k == "lim":
        return r"\lim\limits_{" + to_latex(node[1]) + "} " + to_latex(node[2])     # the condition under "lim", also inside a sentence
    if k in ("int", "bigop"):
        # ∑ ∏ ⋃ ⋂: bounds above and below (as Word shows them), also inside a sentence; ∫ keeps its bounds at the side
        lim = r"\limits" if k == "bigop" and (node[2] is not None or node[3] is not None) else ""
        head = node[1] + lim + ("_{" + to_latex(node[2]) + "}" if node[2] is not None else "") + ("^{" + to_latex(node[3]) + "}" if node[3] is not None else "")
        body = " " + to_latex(node[4]) if node[4] is not None else ""
        dot = node[6] if k == "int" and len(node) > 6 else None
        diffs = "".join((" " + dot + " " if dot and n == 0 else r"\,") + _cat(["d", to_latex(v)]) for n, v in enumerate(node[5])) if k == "int" else ""
        return head + body + diffs
    if k == "sqrt":
        return r"\sqrt" + ("[" + node[1] + "]" if node[1] else "") + "{" + to_latex(node[2]) + "}"
    if k == "prefix":
        return node[1] + " " + to_latex(node[2])
    if k == "partial":
        return r"\partial " + to_latex(node[1])
    if k == "deriv":
        _, sym, order, num, den = node
        o = "^{" + order + "}" if order else ""
        if sym == "partial":
            top = r"\partial" + o + (" " + to_latex(num) if num else "")
            bottom = " ".join(r"\partial " + to_latex(v) + ("^{" + p + "}" if p else "") for v, p in den)
        else:
            top = "d" + o + (to_latex(num) if num else "")
            bottom = " ".join(_cat(["d", to_latex(v)]) + ("^{" + p + "}" if p else "") for v, p in den)
        return r"\frac{" + top + "}{" + bottom + "}"
    raise ValueError(f"unknown node {k}")


def tree_repr(node) -> str:
    """The parsed structure as an s-expression (shown in the PoC report)."""
    if isinstance(node, tuple):
        return "(" + " ".join(tree_repr(x) for x in node if x is not None and x is not False and x is not True) + ")"
    if isinstance(node, list):
        return "[" + " ".join(tree_repr(x) for x in node) + "]"
    return str(node)


def check_latex(latex: str, allowed: frozenset | set = ALLOWED_COMMANDS) -> None:
    """Refuse output that is not balanced or uses a command outside `allowed` (default: what the converter itself may emit)."""
    depth = 0
    for ch in re.sub(r"\\[{}]", "", latex):
        depth += {"{": 1, "}": -1}.get(ch, 0)
        if depth < 0:
            raise Unreadable("internal check failed (braces)", "LATEX_VALIDATION_FAILED")
    if depth:
        raise Unreadable("internal check failed (braces)", "LATEX_VALIDATION_FAILED")
    for cmd in re.findall(r"\\([A-Za-z]+|.)", latex):
        if cmd not in allowed:
            raise Unreadable(f"internal check failed (\\{cmd})", "LATEX_VALIDATION_FAILED")
