"""
OCR chain reconciliation
========================
A bank statement carries a running balance on every row, which makes the
(amount, balance) sequence self-checking:

    balance[i] == balance[i-1] + deposit[i] - withdrawal[i]

Tesseract breaks that identity in predictable ways — it reads a leading "1"
as "4" (1,400.00 -> 41,400.00), swaps "," and ".", drops a minus sign, or
splits a wrapped row so a value goes missing.  Because every row is
constrained by its neighbours, most single-row damage can be repaired
without guessing: pick, for every row, the reading of (amount, balance) that
makes the whole chain consistent with the smallest total "OCR edit cost".

This is a beam (Viterbi-style) search over a handful of candidates per row:

  keep          both values as read           cost 0     (chain holds)
  printed_early both as read, chained from    cost 0.3+  (the bank printed the
                the balance of the next m rows            row m rows earlier than
                (which chain from prev)                   it posted it)
  keep_prior    both as read, but chained     cost 0.5+  (the bank printed the
                from a balance 2-4 rows back,             row out of posting
                or from that balance plus the             order — common on
                rows printed since                        J&K Bank statements)
  fix_amount    amount := |balance - base|    cost = edit(amount_read, new)
  fix_balance   balance := prev ± amount      cost = edit(balance_read, new)
  fix_both      balance from the NEXT row,    cost = edit(amount) + edit(balance)
                amount from prev
  fix_variant   amount := the read amount     cost = 0.5 + edit(balance)
                with one document-typical                (both values misread in
                digit confusion undone,                   a mutually consistent
                balance := prev ± that                    way, e.g. 15,000 -> 45,000
                                                          and 1,55,524 -> 1,85,524)
  accept        keep both, break the chain    cost 4.5 — 1.0 when the NEXT row
                                              chains cleanly from the balance
                                              as read, 0.3 when the break is
                                              explained by a nearby row the
                                              bank posted earlier than it
                                              printed it ("phantom" rows)

Three further signals keep the search honest:

  * Page totals.  Every "Page Total :" line gives the page's withdrawal and
    deposit sums; a path whose sums disagree pays a penalty, which settles
    ties like "24,000 W then 1,000 W" vs "34,000 W then 9,000 D".
  * Corroboration.  A balance that the following row chains from as read is
    almost certainly right; changing it costs extra.
  * Adaptation.  After a first pass the digit substitutions it needed are
    counted; a substitution the document shows repeatedly (this font's
    "1 read as 4") becomes cheap and the search is run again.

`edit()` is a weighted Levenshtein on the digit strings where substitutions
between glyphs Tesseract actually confuses are cheap and everything else is
expensive, so "one confusable digit changed" beats "invent a number".

Text-layer statements are already consistent, cost 0, and come back
untouched.  Every change is reported so the caller can surface it.
"""

from collections import Counter
from typing import Dict, List, Optional, Tuple

TOL = 0.011               # rupee-paise comparisons
ACCEPT_COST = 4.5         # leave an uncorroborated inconsistent row as read
ACCEPT_CORROBORATED = 2.2 # ... when the next row chains from it as read (above any cheap digit fix)
ACCEPT_PHANTOM = 0.3      # ... and a nearby out-of-order row explains the gap
PRINTED_EARLY_COST = 0.3  # row chains from the balance of the next m rows (+0.1 per row)
LEAD4_COST = 1.0          # '41,400' read for '1,400': a spurious 4 glued to a leading 1 (classic Tesseract)
CORROBORATION_PENALTY = 2.0
KEEP_PRIOR_COST = 0.5     # chains from the balance 2 rows back (+0.25 per extra row)
HISTORY = 4               # rows remembered for keep_prior
MISSING_COST = 0.5        # filling a value OCR dropped entirely
SIGN_FLIP_COST = 1.0
PAGE_TOTAL_MISMATCH = 1.5 # per total (withdrawals, deposits) that disagrees
BEAM = 16
INDEL = 2.5
OTHER_SUB = 2.5
CONFUSABLE_SUB = 1.0
ADAPTED_SUB = 0.5
ADAPT_MIN_COUNT = 3

# Digit pairs Tesseract commonly swaps in Latin numerals (either direction).
_CONFUSABLE = {
    frozenset(p) for p in (
        '14', '17', '49', '58', '38', '68', '08', '06', '27', '56',
        '89', '09', '35', '13', '23', '47', '15', '79', '16', '01', '77',
    )
}

Cand = Tuple[float, float, float, str, Optional[str]]   # amount, balance, cost, how, direction
Hist = Tuple[Tuple[float, float], ...]                  # ((balance, signed_amount), ...) newest first


def _default_costs() -> Dict[Tuple[str, str], float]:
    costs: Dict[Tuple[str, str], float] = {}
    for a in '0123456789':
        for b in '0123456789':
            if a != b:
                costs[(a, b)] = CONFUSABLE_SUB if frozenset((a, b)) in _CONFUSABLE else OTHER_SUB
    return costs


def _digits(v: float) -> str:
    return f"{abs(v):.2f}".replace('.', '')


def _read_digits(read: Optional[str]) -> str:
    return ''.join(ch for ch in (read or '') if ch.isdigit())


def edit_cost(read: Optional[str], true_val: float, costs: Dict[Tuple[str, str], float]) -> float:
    """Weighted Levenshtein between the digits OCR produced and a candidate value."""
    src = _read_digits(read)
    dst = _digits(true_val)
    if not src:
        return MISSING_COST
    if src == dst:
        return 0.0
    # Very common: a spurious "4" glued in front of a leading "1"  (41,400 <- 1,400)
    if dst and dst[0] == '1' and src == '4' + dst:
        return costs.get(('4+', '1'), LEAD4_COST)
    n, m = len(src), len(dst)
    prev = [j * INDEL for j in range(m + 1)]
    for i in range(1, n + 1):
        cur = [i * INDEL] + [0.0] * m
        si = src[i - 1]
        for j in range(1, m + 1):
            dj = dst[j - 1]
            sub = 0.0 if si == dj else costs[(si, dj)]
            cur[j] = min(prev[j] + INDEL, cur[j - 1] + INDEL, prev[j - 1] + sub)
        prev = cur
    return prev[m]


def _sign_cost(read_balance: Optional[float], new_balance: float) -> float:
    if read_balance is None or abs(new_balance) < TOL or abs(read_balance) < TOL:
        return 0.0
    return SIGN_FLIP_COST if (read_balance < 0) != (new_balance < 0) else 0.0


def _dir(new_bal: float, base: float) -> Optional[str]:
    if new_bal < base - TOL:
        return 'withdrawal'
    if new_bal > base + TOL:
        return 'deposit'
    return None


def _eq(x: float, y: float) -> bool:
    return abs(x - y) <= TOL


class _Ctx:
    def __init__(self, rows: List[Dict], costs: Dict[Tuple[str, str], float]):
        self.rows = rows
        self.costs = costs
        n = len(rows)
        A = [r.get('amount') for r in rows]
        B = [r.get('balance') for r in rows]

        # corroborated[i]: the next row, as read, chains from balance[i] as read
        self.corroborated = [False] * n
        for i in range(n - 1):
            if B[i] is not None and B[i + 1] is not None and A[i + 1] is not None:
                self.corroborated[i] = _eq(abs(B[i + 1] - B[i]), A[i + 1])

        # phantom[i]: row i's printed balance already includes a row j printed
        # 1-3 rows LATER (the bank posted j first). Signature, on read values:
        #   |amount_i - |balance_i - balance_{i-1}|| == amount_j
        # and row j itself chains from an older balance (it is out of order).
        self.phantom = [False] * n
        for i in range(1, n - 1):
            if A[i] is None or B[i] is None or B[i - 1] is None:
                continue
            gap = abs(A[i] - abs(B[i] - B[i - 1]))
            if gap <= TOL:
                continue
            for j in range(i + 1, min(i + 4, n)):
                if A[j] is None or B[j] is None or not _eq(gap, A[j]):
                    continue
                for k in range(i - 1, j - 1):
                    if B[k] is not None and _eq(abs(B[j] - B[k]), A[j]):
                        self.phantom[i] = True
                        break
                if self.phantom[i]:
                    break

    def amount_variants(self, i: int) -> List[float]:
        """Readings of row i's amount with ONE document-typical confusion undone
        (only substitutions the adaptation pass marked cheap, plus the leading-4
        deletion)."""
        src = _read_digits(self.rows[i].get('amount_raw'))
        if len(src) < 3:
            return []
        out = []
        cheap = {pair for pair, c in self.costs.items() if c <= ADAPTED_SUB and pair[0] != '4+'}
        for pos, ch in enumerate(src):
            for (frm, to) in cheap:
                if ch == frm:
                    d = src[:pos] + to + src[pos + 1:]
                    if d.lstrip('0'):
                        out.append(float(d[:-2] + '.' + d[-2:]))
        if self.costs.get(('4+', '1'), LEAD4_COST) <= ADAPTED_SUB and src.startswith('41') and len(src) > 3:
            d = src[1:]
            out.append(float(d[:-2] + '.' + d[-2:]))
        return out

    def bal_cost(self, i: int, new_balance: float) -> float:
        row = self.rows[i]
        c = edit_cost(row.get('balance_raw'), new_balance, self.costs) + _sign_cost(row.get('balance'), new_balance)
        if self.corroborated[i] and row.get('balance') is not None and abs(row['balance'] - new_balance) > TOL:
            c += CORROBORATION_PENALTY
        return c

    def amt_cost(self, i: int, new_amount: float) -> float:
        return edit_cost(self.rows[i].get('amount_raw'), new_amount, self.costs)

    @staticmethod
    def _bases(hist: Hist) -> List[Tuple[float, float, str]]:
        """(balance, extra_cost, how) values row i may chain from: the previous
        row's balance; an older balance (row printed out of order); an older
        balance plus the amounts printed since (the row above was the one out
        of order)."""
        out = [(hist[0][0], 0.0, 'keep')]
        run = 0.0
        for k in range(1, len(hist)):
            run += hist[k - 1][1]
            extra = KEEP_PRIOR_COST + 0.25 * (k - 1)
            out.append((hist[k][0], extra, 'keep_prior'))
            merged = hist[k][0] + run
            if not _eq(merged, hist[0][0]):
                out.append((merged, extra, 'keep_prior'))
        return out

    def candidates(self, i: int, hist: Hist) -> List[Cand]:
        """Candidate readings of row i given the reconciled recent rows."""
        row = self.rows[i]
        nxt = self.rows[i + 1] if i + 1 < len(self.rows) else None
        a, b = row.get('amount'), row.get('balance')
        prev_bal = hist[0][0]
        out: List[Cand] = []
        seen = set()

        def add(c: Cand):
            key = (round(c[0], 2), round(c[1], 2))
            if key not in seen:
                seen.add(key)
                out.append(c)

        consistent = a is not None and b is not None and _eq(abs(b - prev_bal), a)

        bases = self._bases(hist)
        # Printed early: the next m rows, as read, chain straight from the
        # previous balance (skipping this row), so this row was posted after
        # them and its balance follows the last of theirs.
        run = prev_bal
        for m in range(1, 4):
            j = i + m
            if j >= len(self.rows):
                break
            aj, bj = self.rows[j].get('amount'), self.rows[j].get('balance')
            if aj is None or bj is None or not _eq(abs(bj - run), aj):
                break
            run = bj
            bases.append((run, PRINTED_EARLY_COST + 0.1 * m, 'printed_early'))

        for base, extra, how in bases:
            primary = extra == 0.0
            if a is not None and b is not None and _eq(abs(b - base), a):
                add((a, b, extra, how, _dir(b, base)))
            # Trust the balance, re-derive the amount.
            if b is not None:
                a2 = round(abs(b - base), 2)
                if a2 > TOL and (a is None or abs(a2 - a) > TOL):
                    add((a2, b, extra + self.amt_cost(i, a2), 'fix_amount', _dir(b, base)))
            if not primary:
                continue
            # Trust the amount, re-derive the balance (either direction).
            if a is not None and a > TOL:
                for s in (1, -1):
                    b2 = round(base + s * a, 2)
                    if b is None or abs(b2 - b) > TOL:
                        add((a, b2, self.bal_cost(i, b2), 'fix_balance', _dir(b2, base)))
            # Both damaged, consistently: undo one typical confusion in the
            # amount and re-derive the balance from it.
            for a2 in self.amount_variants(i):
                if a is not None and _eq(a2, a):
                    continue
                for s in (1, -1):
                    b2 = round(base + s * a2, 2)
                    if b is None or abs(b2 - b) > TOL:
                        add((a2, b2, ADAPTED_SUB + self.bal_cost(i, b2), 'fix_variant', _dir(b2, base)))
            # Both damaged: balance implied by the NEXT row, amount from prev.
            if nxt is not None and nxt.get('amount') is not None and nxt.get('balance') is not None:
                for s in (1, -1):
                    b2 = round(nxt['balance'] - s * nxt['amount'], 2)
                    a2 = round(abs(b2 - base), 2)
                    if a2 > TOL and (b is None or abs(b2 - b) > TOL):
                        add((a2, b2, self.amt_cost(i, a2) + self.bal_cost(i, b2), 'fix_both', _dir(b2, base)))

        # A real break in the chain: keep what was read.
        if a is not None and b is not None and not consistent:
            if self.phantom[i] and self.corroborated[i]:
                cost = ACCEPT_PHANTOM
            elif self.corroborated[i]:
                cost = ACCEPT_CORROBORATED
            else:
                cost = ACCEPT_COST
            add((a, b, cost, 'accept', _dir(b, prev_bal)))

        if not out:
            # Nothing usable on this row at all — carry the balance forward.
            add((0.0, prev_bal, ACCEPT_COST, 'accept', None))
        return out


def _page_penalty(w: float, d: float, totals: Tuple[float, float], slack: float) -> float:
    """Penalty for a page whose reconciled sums disagree with its printed totals.
    `slack` is the first row's amount when its direction is unknown (row 0)."""
    tw, td = totals
    pen = 0.0
    if not (_eq(w, tw) or (slack and _eq(w + slack, tw))):
        pen += PAGE_TOTAL_MISMATCH
    if not (_eq(d, td) or (slack and _eq(d + slack, td))):
        pen += PAGE_TOTAL_MISMATCH
    return pen


def _signed(a: float, direction: Optional[str]) -> float:
    if direction == 'withdrawal':
        return -a
    if direction == 'deposit':
        return a
    return 0.0


def _run(rows: List[Dict], page_totals: Dict[int, Tuple[float, float]],
         costs: Dict[Tuple[str, str], float]):
    ctx = _Ctx(rows, costs)
    n = len(rows)

    # ── Row 0: its balance is the anchor. Offer the read balance, and the
    #    balance implied by row 1 in case row 0 itself was misread.
    r0 = rows[0]
    a0 = r0.get('amount') or 0.0
    starts: List[Cand] = []
    if r0.get('balance') is not None:
        starts.append((a0, r0['balance'], 0.0, 'keep', None))
    if n > 1 and rows[1].get('amount') is not None and rows[1].get('balance') is not None:
        for s in (1, -1):
            b2 = round(rows[1]['balance'] - s * rows[1]['amount'], 2)
            if r0.get('balance') is None or abs(b2 - r0['balance']) > TOL:
                starts.append((a0, b2, ctx.bal_cost(0, b2) + 0.5, 'fix_balance', None))
    if not starts:
        starts.append((a0, r0.get('balance') or 0.0, ACCEPT_COST, 'accept', None))

    # Row 0's direction is unknown to the chain: its amount is carried as
    # `slack` for the first page-total check instead of into w/d.
    # state: (total_cost, hist, w_sum, d_sum, path)
    beam = [(c, ((b, 0.0),), 0.0, 0.0, [(a, b, how, d)]) for (a, b, c, how, d) in starts]

    def page_of(i: int) -> int:
        return rows[i].get('page', 0)

    def end_of_page(i: int) -> bool:
        return i == n - 1 or page_of(i + 1) != page_of(i)

    def apply_page_end(states, i, slack):
        totals = page_totals.get(page_of(i)) if page_totals else None
        out = []
        for (c, hist, w, d, path) in states:
            if totals:
                c += _page_penalty(w, d, totals, slack)
            out.append((c, hist, 0.0, 0.0, path))
        return out

    if end_of_page(0):
        beam = apply_page_end(beam, 0, a0)
    beam.sort(key=lambda t: t[0])
    beam = beam[:BEAM]

    for i in range(1, n):
        new_beam = []
        for total, hist, w, d, path in beam:
            for (a, b, c, how, direction) in ctx.candidates(i, hist):
                nw, nd = w, d
                if direction == 'withdrawal':
                    nw += a
                elif direction == 'deposit':
                    nd += a
                new_hist = ((b, _signed(a, direction)),) + hist[:HISTORY - 1]
                new_beam.append((total + c, new_hist, nw, nd, path + [(a, b, how, direction)]))
        if end_of_page(i):
            slack = a0 if page_of(i) == page_of(0) else 0.0
            new_beam = apply_page_end(new_beam, i, slack)
        new_beam.sort(key=lambda t: t[0])
        seen, pruned = set(), []
        for item in new_beam:
            key = (tuple(round(x, 2) for x, _y in item[1][:2]), round(item[2], 2), round(item[3], 2))
            if key in seen:
                continue
            seen.add(key)
            pruned.append(item)
            if len(pruned) >= BEAM:
                break
        beam = pruned

    return beam[0][4]


def _adapt_costs(rows: List[Dict], path, costs: Dict[Tuple[str, str], float]) -> bool:
    """Make the digit substitutions this document keeps needing cheap. Returns
    True if anything changed."""
    counter: Counter = Counter()
    for row, (a, b, how, _d) in zip(rows, path):
        for read, val in ((row.get('amount_raw'), a), (row.get('balance_raw'), b)):
            src, dst = _read_digits(read), _digits(val)
            if src and dst and dst[0] == '1' and src == '4' + dst:
                counter[('4+', '1')] += 1
            elif src and len(src) == len(dst) and src != dst:
                diffs = [(s, t) for s, t in zip(src, dst) if s != t]
                if len(diffs) <= 2:
                    counter.update(diffs)
    changed = False
    for pair, cnt in counter.items():
        if cnt >= ADAPT_MIN_COUNT and costs.get(pair, LEAD4_COST) > ADAPTED_SUB:
            costs[pair] = ADAPTED_SUB
            changed = True
    return changed


def reconcile(rows: List[Dict], page_totals: Optional[Dict[int, Tuple[float, float]]] = None) -> List[Dict]:
    """
    rows: [{'amount': float|None, 'amount_raw': str|None,
            'balance': float|None (signed, Dr negative), 'balance_raw': str|None,
            'page': int (optional)}, ...]
    page_totals: {page: (withdrawals_total, deposits_total)} as printed.

    Returns one dict per row:
        {'amount', 'balance', 'direction' ('withdrawal'|'deposit'|None),
         'how', 'corrections': [(field, read_value, new_value), ...]}
    """
    if not rows:
        return []
    page_totals = page_totals or {}

    costs = _default_costs()
    path = _run(rows, page_totals, costs)
    if _adapt_costs(rows, path, costs):
        path = _run(rows, page_totals, costs)

    out: List[Dict] = []
    for i, (a, b, how, direction) in enumerate(path):
        row = rows[i]
        corrections = []
        if row.get('amount') is None or abs(row['amount'] - a) > TOL:
            corrections.append(('amount', row.get('amount_raw'), f"{a:.2f}"))
        if row.get('balance') is None or abs(row['balance'] - b) > TOL:
            corrections.append(('balance', row.get('balance_raw'), f"{b:.2f}"))
        out.append({
            'amount': round(a, 2),
            'balance': round(b, 2),
            'direction': direction,
            'how': how,
            'corrections': corrections,
        })
    return out
