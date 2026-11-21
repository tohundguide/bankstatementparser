"""
A statement's layout by its shape and nothing else (2.5.0; Karr bankbest-i i-1,
Karr's docs/BANK_BEST_SPEC.md §10.1).

The twin of Karr's supabase/functions/_shared/bankLayoutShape.ts. Four parts:

  headings  each heading kept only as the words of HEADING_VOCAB it holds; a
            heading with none of them is kept as its column's kind ('#text');
  kinds     what each column holds: date, money, side (Dr/Cr), text, empty;
  date      the date's shape ('dd/mm/yyyy', 'dd-mmm-yy', 'yyyy-mm-dd', ' t' after a time);
  bank      the code of a bank the statement names, or None.

The fingerprint is the first 16 hex of SHA-256 over 'v2|bank|headings|kinds|date'.
No narration, no figure, no account number, no name can enter it: a heading
word outside the vocabulary is never copied. contract/v1/fingerprint-v2.json
holds the golden vectors; Karr's test and tests/test_layout_shape.py assert the
same file byte for byte, so whichever side drifts goes red.

A PDF has no cells, so its headings are the heading roles the parser found on
the heading line, in order, each as its words ('value_date' is 'value date'),
and each role's kind is what that role holds.
"""

import hashlib
import re
from typing import Dict, List, Optional

HEADING_VOCAB = [
    'date', 'dt', 'txn', 'tran', 'trans', 'transaction', 'transactions', 'posting', 'post', 'posted', 'value',
    'narration', 'particulars', 'description', 'details', 'remarks', 'remark',
    'ref', 'reference', 'chq', 'cheque', 'check', 'instrument', 'utr', 'no', 'number', 'num', 'id',
    'withdrawal', 'withdrawals', 'debit', 'debits', 'deposit', 'deposits', 'credit', 'credits',
    'amount', 'amt', 'dr', 'cr', 'type',
    'balance', 'bal', 'closing', 'opening', 'running', 'available',
    'sl', 'sr', 's', 'serial', 'mode', 'branch', 'code', 'init', 'by',
    'inr', 'rs', 'in', 'out', 'paid', 'received',
]
_VOCAB = set(HEADING_VOCAB)
CELL_KINDS = ['date', 'money', 'side', 'text', 'empty']
MAX_COLUMNS = 40
_MAX_HEADING_WORDS = 6

_TIME = re.compile(r'(?:[ T]+\d{1,2}[:.]\d{2}(?:[:.]\d{2})?(?:\.\d+)?\s*(?:[ap]\.?\s?m\.?)?(?:\s*(?:z|[+-]\d{2}:?\d{2}|ist|utc|gmt))?)$', re.IGNORECASE)
_ISO_LIKE = re.compile(r'^(\d{4})([-/.])(\d{1,2})([-/.])(\d{1,2})$')
_NUMERIC = re.compile(r'^(\d{1,2})([-/.])(\d{1,2})([-/.])(\d{4}|\d{2})$')
_NAMED = re.compile(r'^(\d{1,2})([-/. ,]+)([A-Za-z]{3,9})\.?([-/. ,]+)(\d{4}|\d{2})$')
_BANK_CODE = re.compile(r'^[a-z][a-z0-9_]{1,31}$')
# The banks a shape can name: Karr's closed list (its BANK_WORDS, and the database's _bank_codes()).
BANK_CODES = {
    'karr_statement', 'hdfc_bank', 'icici_bank', 'sbi_bank', 'sbi', 'axis_bank', 'jk_bank', 'kotak_bank', 'yes_bank',
    'indusind_bank', 'pnb', 'bob', 'canara_bank', 'union_bank', 'idfc_first', 'idfc_first_bank', 'federal_bank',
    'equitas_sfb', 'kvb', 'paytm_payments_bank', 'airtel_payments_bank', 'ippb', 'fino_payments_bank',
    'enbd_bank', 'adcb_bank', 'chase_bank', 'bofa_bank',
}

# A PDF's heading roles (table_reader.HEADER_VOCAB's values) as heading words, and what each holds.
ROLE_WORDS = {'date': 'date', 'value_date': 'value date', 'narration': 'narration', 'reference': 'reference',
              'withdrawal': 'withdrawal', 'deposit': 'deposit', 'amount': 'amount', 'dr_cr': 'dr cr', 'balance': 'balance'}
ROLE_KINDS = {'date': 'date', 'value_date': 'date', 'narration': 'text', 'reference': 'text',
              'withdrawal': 'money', 'deposit': 'money', 'amount': 'money', 'dr_cr': 'side', 'balance': 'money'}


def _words(cell: str) -> List[str]:
    return [w for w in re.split(r'[^a-z0-9]+', (cell or '').lower()) if w]


def heading_token(cell: Optional[str], kind: str) -> str:
    kept = [w for w in _words(cell or '') if w in _VOCAB][:_MAX_HEADING_WORDS]
    return ' '.join(kept) if kept else f'#{kind}'


def _sep(run: str) -> str:
    return ' ' if run[0] == ',' else run[0]


def date_shape(raw: Optional[str], order: str = 'dmy') -> Optional[str]:
    s = re.sub(r'\s+', ' ', str(raw or '').strip())
    if not s:
        return None
    timed = bool(_TIME.search(s))
    s = _TIME.sub('', s).strip()
    t = ' t' if timed else ''
    m = _ISO_LIKE.match(s)
    if m:
        return f'yyyy{m.group(2)}mm{m.group(4)}dd{t}'
    m = _NUMERIC.match(s)
    if m:
        y = 'yyyy' if len(m.group(5)) == 4 else 'yy'
        return f'mm{m.group(2)}dd{m.group(4)}{y}{t}' if order == 'mdy' else f'dd{m.group(2)}mm{m.group(4)}{y}{t}'
    m = _NAMED.match(s)
    if m:
        y = 'yyyy' if len(m.group(5)) == 4 else 'yy'
        mon = 'mmm' if len(m.group(3)) <= 4 else 'mmmm'
        return f'dd{_sep(m.group(2))}{mon}{_sep(m.group(4))}{y}{t}'
    return None


def layout_shape(headings: List[Optional[str]], kinds: List[str], first_date: Optional[str],
                 order: str = 'dmy', bank: Optional[str] = None) -> Dict:
    width = min(max(len(headings), len(kinds)), MAX_COLUMNS)

    def kind_at(i):
        return kinds[i] if i < len(kinds) and kinds[i] in CELL_KINDS else 'empty'

    def head_at(i):
        return headings[i] if i < len(headings) else None

    while width > 0 and not str(head_at(width - 1) or '').strip() and kind_at(width - 1) == 'empty':
        width -= 1
    out_kinds = [kind_at(i) for i in range(width)]
    out_heads = [heading_token(head_at(i), out_kinds[i]) for i in range(width)]
    return {'v': 2, 'bank': bank if bank and _BANK_CODE.match(bank) and bank in BANK_CODES else None,
            'headings': out_heads, 'kinds': out_kinds, 'date': date_shape(first_date, order)}


def shape_of_roles(roles: List[str], first_date: Optional[str], order: str = 'dmy', bank: Optional[str] = None) -> Dict:
    """A PDF's shape: its heading roles as heading words, each role's kind."""
    known = [r for r in roles if r in ROLE_WORDS]
    return layout_shape([ROLE_WORDS[r] for r in known], [ROLE_KINDS[r] for r in known], first_date, order, bank)


def shape_text(s: Dict) -> str:
    return f"v2|{s['bank'] or ''}|{','.join(s['headings'])}|{','.join(s['kinds'])}|{s['date'] or ''}"


def fingerprint(s: Dict) -> str:
    return hashlib.sha256(shape_text(s).encode('utf-8')).hexdigest()[:16]
