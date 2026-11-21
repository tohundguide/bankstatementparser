"""
R2: the table reads itself (bankbest-b, b-3; Karr's docs/BANK_BEST_SPEC.md §4.2).

For a text PDF whose bank no parser knows, before OCR and before any AI: the
columns, the row order and the date order are the ONE layout under which
every balance follows from the balance before it, to the paisa. A read that
cannot prove itself is not a read: this answers None and the caller goes on
to the next rung (or refuses).

  1. Words with their positions (pdfplumber), grouped into lines.
  2. A row is a line that starts with a date. Dates are read day first, by
     one reader of our own (never the platform's date parser), and checked
     as real days. A dated line whose date or figures cannot be read refuses
     the whole file: a row is never dropped.
  3. The figures at the end of a row: the last is the balance, the ones
     before it the amount. Which amount is money out is not taken from a
     heading: every way the money could be laid out is tried
       two_columns  a withdrawal column and a deposit column (by position)
       signed       one amount, a minus or brackets meaning money out
       dr_cr        one amount with Dr / Cr beside it
     in both row orders (oldest first, newest first).
  4. The layout kept is the only one under which, on every row after the
     first (three rows at least), the balance before plus the deposit less
     the withdrawal is the balance printed. Two different reads that both
     pass, or none: no read.

Nothing is learned or kept: the file is read in memory and forgotten.
"""

import hashlib
import re
from datetime import date
from typing import Dict, List, Optional, Tuple

MONTHS = {'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'may': 5, 'jun': 6,
          'jul': 7, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12}

MIN_ROWS = 3
PAISA = 0.005

_NUM_DATE = re.compile(r'^(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4}|\d{2})$')
_ISO_DATE = re.compile(r'^(\d{4})[/\-](\d{1,2})[/\-](\d{1,2})$')
_MON_DATE = re.compile(r'^(\d{1,2})[/\-]([A-Za-z]{3,9})[/\-](\d{4}|\d{2})$')
_TIME = re.compile(r'^\d{1,2}:\d{2}(:\d{2})?$|^(AM|PM)$', re.IGNORECASE)
_MONEY = re.compile(r'^(\(?)([+-]?)((?:\d{1,3}(?:,\d{2,3})+|\d+)\.\d{2})(\)?)(?:(Cr|Dr|CR|DR)\.?)?$')
_MARK = re.compile(r'^(Cr|Dr|CR|DR)\.?$')
_PAGE = re.compile(r'^page\s*\d+(\s*(of|/)\s*\d+)?$', re.IGNORECASE)
_SKIP_LABEL = re.compile(r'opening|closing|brought\s+forward|carried\s+forward|\bb/?f\b|\bc/?f\b|total', re.IGNORECASE)

# Header words by what the column holds. Only these words ever enter a fingerprint:
# a heading is the bank's, never the customer's.
HEADER_VOCAB = {
    'date': 'date', 'txn': 'date', 'tran': 'date', 'transaction': 'date', 'posting': 'date', 'value': 'value_date',
    'narration': 'narration', 'particulars': 'narration', 'description': 'narration', 'details': 'narration', 'remarks': 'narration',
    'ref': 'reference', 'reference': 'reference', 'chq': 'reference', 'cheque': 'reference', 'instrument': 'reference', 'utr': 'reference',
    'withdrawal': 'withdrawal', 'withdrawals': 'withdrawal', 'debit': 'withdrawal', 'debits': 'withdrawal',
    'deposit': 'deposit', 'deposits': 'deposit', 'credit': 'deposit', 'credits': 'deposit',
    'amount': 'amount', 'dr/cr': 'dr_cr', 'cr/dr': 'dr_cr', 'type': 'dr_cr', 'balance': 'balance',
}


# ── Dates: day first, our own reader ──

def _year(y: str) -> int:
    n = int(y)
    return n if len(y) == 4 else 2000 + n


def _real(y: int, m: int, d: int) -> Optional[date]:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def read_day(text: str, month_first: bool = False) -> Optional[date]:
    """A printed day as a date, or None. Numeric dates are day first unless month_first."""
    s = text.strip()
    m = _NUM_DATE.match(s)
    if m:
        a, b, y = int(m.group(1)), int(m.group(2)), _year(m.group(3))
        return _real(y, a, b) if month_first else _real(y, b, a)
    m = _ISO_DATE.match(s)
    if m:
        return _real(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = _MON_DATE.match(s)
    if m and m.group(2)[:3].lower() in MONTHS:
        return _real(_year(m.group(3)), MONTHS[m.group(2)[:3].lower()], int(m.group(1)))
    return None


def looks_like_date(text: str) -> bool:
    s = text.strip()
    return bool(_NUM_DATE.match(s) or _ISO_DATE.match(s) or
                (_MON_DATE.match(s) and _MON_DATE.match(s).group(2)[:3].lower() in MONTHS))


def _leading_date(words: List[dict]) -> Tuple[Optional[str], int]:
    """The date a line starts with ('01/04/2025', '01-Apr-25', '01 Apr 2025'), and how many words it took."""
    if not words:
        return None, 0
    w0 = words[0]['text']
    if looks_like_date(w0):
        return w0, 1
    if len(words) >= 3 and re.fullmatch(r'\d{1,2}', w0) and words[1]['text'][:3].lower() in MONTHS \
            and re.fullmatch(r'\d{4}|\d{2}', words[2]['text']):
        return f"{w0}-{words[1]['text'][:3]}-{words[2]['text']}", 3
    return None, 0


# ── Figures ──

def read_money(text: str) -> Optional[Tuple[float, int, Optional[str], str]]:
    """'1,234.50' / '-1,234.50' / '(1,234.50)' / '1,234.50Cr' -> (value, sign, mark, printed), else None."""
    m = _MONEY.match(text.strip())
    if not m or (m.group(1) == '(') != (m.group(4) == ')'):
        return None
    value = round(float(m.group(3).replace(',', '')), 2)
    sign = -1 if (m.group(2) == '-' or m.group(1) == '(') else 1
    mark = m.group(5).capitalize() if m.group(5) else None
    return value, sign, mark, text.strip()


# ── Lines ──

def _lines_of_page(page) -> List[List[dict]]:
    words = page.extract_words(x_tolerance=1.5, y_tolerance=2, keep_blank_chars=False, use_text_flow=False)
    words.sort(key=lambda w: (round(w['top']), w['x0']))
    lines: List[List[dict]] = []
    for w in words:
        if lines and abs(lines[-1][0]['top'] - w['top']) <= 2.5:
            lines[-1].append(w)
        else:
            lines.append([w])
    for ln in lines:
        ln.sort(key=lambda w: w['x0'])
    return lines


def _header_roles(line: List[dict]) -> List[str]:
    roles = []
    for w in line:
        r = HEADER_VOCAB.get(w['text'].strip().lower().rstrip('.:'))
        if r:
            roles.append(r)
    return roles


def is_header(line: List[dict]) -> bool:
    roles = set(_header_roles(line))
    return 'balance' in roles and ('date' in roles or 'value_date' in roles) and \
        bool(roles & {'withdrawal', 'deposit', 'amount'})


def fingerprint(reader: str, header_roles: List[str]) -> str:
    """The layout's shape and nothing else: who read it and its headings by kind, in order."""
    return hashlib.sha256(f"v1|{reader}|{','.join(header_roles)}".encode()).hexdigest()[:16]


def text_fingerprint(raw_text: str, reader: str) -> str:
    """The same fingerprint from plain text (a bank parser's read): the first heading line found."""
    for line in (raw_text or '').splitlines():
        words = [{'text': t} for t in line.split()]
        if is_header(words):
            return fingerprint(reader, _header_roles(words))
    return fingerprint(reader, [])


class Refused(Exception):
    """The table cannot be read without a guess; the message names the row."""


def _rows(pages_lines: List[List[List[dict]]]):
    """Dated rows with their narration and figures; the header's roles; the opening/closing printed above or below."""
    header_roles: List[str] = []
    ref_band = None
    narr_x = None
    rows = []
    opening = closing = None
    pending_dateless_money = None
    for lines in pages_lines:
        last_row_bottom = None
        for line in lines:
            texts = [w['text'] for w in line]
            joined = ' '.join(texts)
            if is_header(line):
                if not header_roles:
                    header_roles = _header_roles(line)
                    roles_at = [HEADER_VOCAB.get(w['text'].strip().lower().rstrip('.:')) for w in line]
                    for i, w in enumerate(line):
                        role = roles_at[i]
                        if role == 'narration' and narr_x is None:
                            narr_x = w['x0']
                        if role == 'reference' and ref_band is None:
                            # up to the next heading of another kind ("Ref No" is one heading)
                            nxt = next((line[j]['x0'] for j in range(i + 1, len(line))
                                        if roles_at[j] and roles_at[j] != 'reference'), w['x1'] + 60)
                            ref_band = (w['x0'] - 4, nxt - 4)
                last_row_bottom = None
                continue
            if _PAGE.match(joined.strip()):
                continue
            in_table = bool(header_roles)

            # The figures at the end of the line, and a Dr/Cr word belongs to the figure before it.
            figs = []
            k = len(line) - 1
            while k >= 0:
                t = line[k]['text']
                if _MARK.match(t) and k > 0 and read_money(line[k - 1]['text']):
                    v = read_money(line[k - 1]['text'])
                    figs.insert(0, (v[0], v[1], t.rstrip('.').capitalize(), v[3] + ' ' + t, line[k - 1]['x1']))
                    k -= 2
                    continue
                v = read_money(t)
                if not v:
                    break
                figs.insert(0, (v[0], v[1], v[2], v[3], line[k]['x1']))
                k -= 1
            body = line[:k + 1]

            day, used = _leading_date(body) if in_table else (None, 0)
            if day is None:
                if figs:
                    if re.search(r'opening', joined, re.IGNORECASE):
                        opening = figs[-1]
                    elif re.search(r'closing', joined, re.IGNORECASE):
                        closing = figs[-1]
                    elif rows and not _SKIP_LABEL.search(joined):
                        # Money on a line with no date inside the table: a row Karr would lose.
                        pending_dateless_money = joined
                    continue
                # A narration that runs onto the next line: only right under its row.
                if rows and last_row_bottom is not None and line[0]['top'] - last_row_bottom < 14 and \
                        (narr_x is None or line[0]['x0'] >= narr_x - 6) and not _SKIP_LABEL.search(joined):
                    rows[-1]['narration'] += ' ' + joined
                    last_row_bottom = line[0]['bottom']
                continue

            if pending_dateless_money:
                raise Refused(f'a line with money and no date, inside the table: "{pending_dateless_money[:40]}"')
            rest = body[used:]
            value_day = None
            while rest and _TIME.match(rest[0]['text']):
                rest = rest[1:]
            if rest and looks_like_date(rest[0]['text']):
                value_day = rest[0]['text']
                rest = rest[1:]
                while rest and _TIME.match(rest[0]['text']):
                    rest = rest[1:]
            if len(figs) < 2:
                raise Refused(f'the row of {day} does not print both an amount and a balance')
            if len(figs) > 3:
                raise Refused(f'the row of {day} prints more figures than a statement has columns')
            narr, ref = [], []
            for w in rest:
                c = (w['x0'] + w['x1']) / 2
                (ref if ref_band and ref_band[0] <= c <= ref_band[1] else narr).append(w['text'])
            rows.append({'day': day, 'value_day': value_day, 'narration': ' '.join(narr), 'reference': ' '.join(ref),
                         'amounts': figs[:-1], 'balance': figs[-1]})
            last_row_bottom = line[0]['bottom']
    return rows, header_roles, opening, closing


def _digits(printed: str) -> str:
    """The figure as the bank printed it, without its sign, brackets or Dr/Cr: '(1,234.50)' -> '1,234.50'."""
    m = re.search(r'(?:\d{1,3}(?:,\d{2,3})+|\d+)\.\d{2}', printed)
    return m.group(0) if m else printed


def _signed_balance(fig) -> float:
    value, sign, mark, _, _ = fig
    return -value if (sign < 0 or mark == 'Dr') else value


def _bands(rows) -> Optional[List[float]]:
    """The right edges of the amount columns: one or two, found from where the amounts sit."""
    xs = sorted({round(a[4], 1) for r in rows for a in r['amounts']})
    if not xs:
        return None
    if len(xs) == 1 or xs[-1] - xs[0] < 12:
        return [sum(xs) / len(xs)]
    gaps = [(xs[i + 1] - xs[i], i) for i in range(len(xs) - 1)]
    gap, i = max(gaps)
    if gap < 12:
        return [sum(xs) / len(xs)]
    left, right = xs[:i + 1], xs[i + 1:]
    if left[-1] - left[0] > 12 or right[-1] - right[0] > 12:
        return None  # three columns or a ragged print: not a shape to guess at
    return [sum(left) / len(left), sum(right) / len(right)]


def _band_of(x, bands) -> int:
    return min(range(len(bands)), key=lambda i: abs(bands[i] - x))


def _moves(rows, layout, flip, bands) -> Optional[List[Tuple[float, float]]]:
    """(withdrawal, deposit) for every row under one layout, or None when the layout does not fit the row."""
    if layout == 'signed' and not any(a[1] < 0 for r in rows for a in r['amounts']):
        # One column that never prints a minus is not a signed column: its first row could be either way,
        # and the chain cannot see the first row. It is two_columns with one column, placed by position.
        return None
    out = []
    for r in rows:
        am = r['amounts']
        if layout == 'two_columns':
            if not bands:
                return None
            wd = dep = 0.0
            seen = set()
            for v, sign, mark, _, x in am:
                if sign < 0 or mark:
                    return None
                b = _band_of(x, bands)
                if b in seen:
                    return None
                seen.add(b)
                is_out = (b == 0) != flip
                if is_out:
                    wd = v
                else:
                    dep = v
            out.append((wd, dep))
        else:
            if len(am) != 1:
                return None
            v, sign, mark, _, _ = am[0]
            if layout == 'signed':
                if mark:
                    return None
                is_out = (sign < 0) != flip
            else:
                if not mark or sign < 0:
                    return None
                is_out = (mark == 'Dr') != flip
            out.append((v, 0.0) if is_out else (0.0, v))
    return out


def _chain_holds(balances, moves) -> bool:
    for i in range(1, len(balances)):
        wd, dep = moves[i]
        if abs(balances[i - 1] + dep - wd - balances[i]) > PAISA:
            return False
    return True


def read_table_pages(pages_lines, raw_text: str = '') -> Optional[Dict]:
    """R2 over lines of positioned words (one list per page). None when no single layout proves itself."""
    try:
        rows, header_roles, opening_fig, closing_fig = _rows(pages_lines)
    except Refused:
        return None
    if len(rows) < MIN_ROWS:
        return None

    # Dates: day first; month first only when day first is not a real day on some row and month first is on all.
    day_first = [read_day(r['day']) for r in rows]
    date_order = 'day_first'
    days = day_first
    if any(d is None for d in day_first):
        month_first = [read_day(r['day'], month_first=True) for r in rows]
        if any(d is None for d in month_first):
            return None
        days, date_order = month_first, 'month_first'

    bands = _bands(rows)
    found = {}
    for layout in ('two_columns', 'signed', 'dr_cr'):
        for flip in (False, True):
            moves = _moves(rows, layout, flip, bands)
            if moves is None:
                continue
            for newest_first in (False, True):
                order = list(range(len(rows)))[::-1] if newest_first else list(range(len(rows)))
                ds = [days[i] for i in order]
                if any(ds[i] > ds[i + 1] for i in range(len(ds) - 1)):
                    continue  # the days must run forward in the order the chain runs
                bal = [_signed_balance(rows[i]['balance']) for i in order]
                mv = [moves[i] for i in order]
                if not _chain_holds(bal, mv):
                    continue
                opening = round(bal[0] - mv[0][1] + mv[0][0], 2)
                if opening_fig is not None and abs(_signed_balance(opening_fig) - opening) > PAISA:
                    continue
                if closing_fig is not None and abs(_signed_balance(closing_fig) - bal[-1]) > PAISA:
                    continue
                key = tuple((i, mv[j]) for j, i in enumerate(order))
                found.setdefault(key, (layout, flip, newest_first, order, mv, opening))
    if len(found) != 1:
        return None  # none proves itself, or two different reads both do: never a guess
    layout, flip, newest_first, order, mv, opening = next(iter(found.values()))

    transactions = []
    for j, i in enumerate(order):
        r = rows[i]
        wd, dep = mv[j]
        printed = {round(a[0], 2): _digits(a[3]) for a in r['amounts']}
        clean = lambda v: printed.get(round(v, 2), f'{v:.2f}')  # noqa: E731
        bal_text = _digits(r['balance'][3]) + (' Dr' if _signed_balance(r['balance']) < 0 else '')
        t = {
            'date': days[i].strftime('%d-%m-%Y'),
            'particulars': r['narration'].strip(),
            'chq_ref': r['reference'].strip(),
            'withdrawal': clean(wd) if wd else '',
            'deposit': clean(dep) if dep else '',
            'balance': bal_text,
        }
        if r['value_day']:
            vd = read_day(r['value_day'], month_first=(date_order == 'month_first'))
            if vd:
                t['value_date'] = vd.strftime('%d-%m-%Y')
        transactions.append(t)

    if layout == 'two_columns' and len(bands or []) == 2:
        money_cols = ['deposit', 'withdrawal'] if flip else ['withdrawal', 'deposit']
    elif layout == 'two_columns':
        money_cols = ['deposit' if flip else 'withdrawal']
    else:
        money_cols = ['amount', 'dr_cr'] if layout == 'dr_cr' else ['amount']
    column_map = {
        'reader': 'table',
        'columns': ['date'] + (['value_date'] if any(r['value_day'] for r in rows) else []) + ['narration'] +
                   (['reference'] if any(r['reference'] for r in rows) else []) + money_cols + ['balance'],
        'amounts': layout,
        'money_out': ({'two_columns': 'right column' if flip else 'left column',
                       'signed': 'plus' if flip else 'minus',
                       'dr_cr': 'Cr' if flip else 'Dr'})[layout],
        'row_order': 'newest_first' if newest_first else 'oldest_first',
        'date_order': date_order,
    }
    first, last = days[order[0]], days[order[-1]]
    return {
        'bank_name': _bank_name(raw_text),
        'bank_code': 'table',
        'account_info': {
            'account_number': _account_number(raw_text),
            'ifsc': _ifsc(raw_text),
            'opening_balance': f'{abs(opening):.2f}' + (' Dr' if opening < 0 else ''),
            'closing_balance': transactions[-1]['balance'],
        },
        'period': _period(raw_text) or f"{first.strftime('%d/%m/%Y')} to {last.strftime('%d/%m/%Y')}",
        'transactions': transactions,
        'column_map': column_map,
        'layout_fingerprint': fingerprint('table', header_roles),
        'warnings': [f'No bank parser knows this layout: Karr read it by its table ({layout.replace("_", " ")}, '
                     f'{column_map["row_order"].replace("_", " ")}), and every balance follows from the one before.'],
    }


def read_table(filepath: str, password: str = None, raw_text: str = '') -> Optional[Dict]:
    """R2 on a text PDF; None when it has no words, is locked, or no single layout proves itself."""
    try:
        import pdfplumber
        kwargs = {'password': password} if password else {}
        pages_lines = []
        with pdfplumber.open(filepath, **kwargs) as pdf:
            for page in pdf.pages:
                pages_lines.append(_lines_of_page(page))
                page.flush_cache()
    except Exception:
        return None
    return read_table_pages(pages_lines, raw_text)


# ── What the page prints about the account (above the table) ──

def _ifsc(raw_text: str) -> Optional[str]:
    m = re.search(r'IFSC(?:\s*Code)?\s*[:\-]?\s*([A-Z]{4}0[A-Z0-9]{6})', raw_text or '', re.IGNORECASE)
    return m.group(1).upper() if m else None


def _account_number(raw_text: str) -> Optional[str]:
    m = re.search(r'(?:A/?c|Account)\s*(?:No\.?|Number|#)\s*[:\-]?\s*([X*\d][X*\d\- ]{5,22}\d)', raw_text or '', re.IGNORECASE)
    return re.sub(r'[\s\-]', '', m.group(1)) if m else None


def _period(raw_text: str) -> Optional[str]:
    d = r'(\d{1,2}[/.\-](?:\d{1,2}|[A-Za-z]{3})[/.\-]\d{2,4})'
    m = re.search(r'(?:from|period)\s*[:\-]?\s*' + d + r'\s*(?:to|-)\s*' + d, raw_text or '', re.IGNORECASE)
    if not m:
        return None
    a, b = read_day(m.group(1)), read_day(m.group(2))
    return f"{a.strftime('%d/%m/%Y')} to {b.strftime('%d/%m/%Y')}" if a and b and a <= b else None


def _bank_name(raw_text: str) -> Optional[str]:
    """The bank whose IFSC the statement prints for itself, when one of our parsers owns that prefix."""
    ifsc = _ifsc(raw_text)
    if not ifsc:
        return None
    try:
        from parsers.registry import _PARSER_REGISTRY, _discover_parsers, _anchor_matches_ifsc
        if not _PARSER_REGISTRY:
            _discover_parsers()
        owners = [pc.BANK_NAME for pc in _PARSER_REGISTRY.values() if _anchor_matches_ifsc(pc, ifsc)]
        return owners[0] if len(owners) == 1 else None
    except Exception:
        return None
