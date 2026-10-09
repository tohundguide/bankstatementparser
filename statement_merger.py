"""
Statement merger
================
Combines several parsed statements of the SAME account (e.g. four quarterly
downloads) into one chronological statement, so they export as a single Excel
sheet whose verification column checks the running balance end to end.

What it does, in order:
  1. Puts each statement in oldest-first order (banks print newest-first too).
     Rows inside a statement keep the bank's own sequence — within a day that
     sequence is what makes the balances chain, so rows are never re-sorted.
  2. Orders the statements by their first transaction date.
  3. Drops rows that appear in an earlier statement as well (downloads whose
     periods overlap). A row is a duplicate only if date, amounts, balance and
     reference all match.
  4. Checks every boundary: the first row of the next statement must continue
     the closing balance of the previous one. A break means a period is
     missing (or the files are from different accounts) and is reported.
"""

from collections import Counter
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from parsers.base_parser import BaseBankParser

_pbv = BaseBankParser.parse_balance_value

_DATE_FORMATS = ('%d-%m-%Y', '%d/%m/%Y', '%d-%m-%y', '%d/%m/%y', '%d-%b-%Y', '%d-%b-%y', '%d %b %Y')


def _date(value) -> Optional[datetime]:
    if not value:
        return None
    s = str(value).strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _amount(value) -> float:
    if value in (None, ''):
        return 0.0
    try:
        return float(str(value).replace(',', '').strip() or 0)
    except ValueError:
        return 0.0


def _row_key(t: Dict) -> Tuple:
    bal = _pbv(t.get('balance'))
    return (
        str(t.get('date', '')).strip(),
        round(_amount(t.get('withdrawal')), 2),
        round(_amount(t.get('deposit')), 2),
        round(bal, 2) if bal is not None else None,
        str(t.get('chq_ref', '')).strip().upper(),
    )


def _period_bounds(period: str) -> Tuple[Optional[datetime], Optional[datetime]]:
    """'01/04/2025 to 30/06/2025' -> (start, end)."""
    if not period or ' to ' not in str(period):
        return None, None
    a, _, b = str(period).partition(' to ')
    return _date(a), _date(b)


def _chronological(txns: List[Dict]) -> List[Dict]:
    dates = [d for d in (_date(t.get('date')) for t in txns) if d]
    if len(dates) >= 2 and dates[0] > dates[-1]:
        return list(reversed(txns))
    return list(txns)


def _fmt(n: Optional[float]) -> str:
    return 'N/A' if n is None else f'₹{n:,.2f}'


def merge_statements(statements: List[Dict]) -> Dict:
    """
    Args:
        statements: parsed results, each {filename, bank_name, account_info,
                    period, transactions, warnings?}.
    Returns:
        A result dict in the same shape the exporters take, plus
        `merge_report` (per-file summary, duplicates removed, boundary checks).
    """
    parts = []
    for i, s in enumerate(statements):
        txns = _chronological(s.get('transactions') or [])
        if not txns:
            continue
        dates = [d for d in (_date(t.get('date')) for t in txns) if d]
        p_start, p_end = _period_bounds(s.get('period', ''))
        parts.append({
            'index': i,
            'filename': s.get('filename') or f'Statement {i + 1}',
            'bank_name': s.get('bank_name') or '',
            'account_info': s.get('account_info') or {},
            'period': s.get('period') or '',
            'warnings': s.get('warnings') or [],
            'txns': txns,
            'first': dates[0] if dates else None,
            'last': dates[-1] if dates else None,
            'p_start': p_start or (dates[0] if dates else None),
            'p_end': p_end or (dates[-1] if dates else None),
        })

    # Undated statements go last, in upload order
    parts.sort(key=lambda p: (p['first'] is None, p['first'] or datetime.max, p['index']))

    warnings: List[str] = []

    accounts = {p['account_info'].get('account_number', '').strip()
                for p in parts if p['account_info'].get('account_number', '').strip()}
    if len(accounts) > 1:
        warnings.append(
            'These statements are from different accounts (' + ', '.join(sorted(accounts)) +
            '). They were merged anyway — check that this is what you wanted.'
        )

    merged: List[Dict] = []
    seen = Counter()
    files_report = []
    boundaries = []
    duplicates_total = 0

    for n, p in enumerate(parts):
        kept, dropped = [], 0
        for t in p['txns']:
            k = _row_key(t)
            if seen[k] > 0:
                seen[k] -= 1
                dropped += 1
                continue
            kept.append(t)
        duplicates_total += dropped

        if n > 0 and kept and merged:
            prev = parts[n - 1]
            prev_close = _pbv(merged[-1].get('balance'))
            first = kept[0]
            shown = _pbv(first.get('balance'))
            expected = None
            if prev_close is not None:
                expected = round(prev_close + _amount(first.get('deposit')) - _amount(first.get('withdrawal')), 2)
            ok = expected is not None and shown is not None and abs(shown - expected) < 0.02
            gap_days = None
            if prev['p_end'] and p['p_start']:
                gap_days = (p['p_start'] - prev['p_end']).days - 1
            boundaries.append({
                'from_file': prev['filename'],
                'to_file': p['filename'],
                'closing_balance': prev_close,
                'expected_balance': expected,
                'shown_balance': shown,
                'balance_continues': ok,
                'missing_days': gap_days if gap_days and gap_days > 0 else 0,
            })
            if not ok and expected is not None and shown is not None:
                warnings.append(
                    f'Balance break between “{prev["filename"]}” and “{p["filename"]}”: '
                    f'closing {_fmt(prev_close)}, so the next row should show {_fmt(expected)} '
                    f'but shows {_fmt(shown)} (difference {_fmt(shown - expected)}). '
                    'A statement period may be missing.'
                )
            elif gap_days and gap_days > 0:
                # Balance carries over, so no money moved — just say the periods don't touch
                warnings.append(
                    f'{gap_days} day(s) between “{prev["filename"]}” and “{p["filename"]}” are not '
                    'covered by any file, but the balance carries over, so no transactions are missing.'
                )

        for t in kept:
            seen[_row_key(t)] += 1
        merged.extend(kept)

        files_report.append({
            'filename': p['filename'],
            'period': p['period'] or (
                f"{p['first']:%d/%m/%Y} to {p['last']:%d/%m/%Y}" if p['first'] and p['last'] else 'N/A'),
            'transactions': len(p['txns']),
            'duplicates_removed': dropped,
            'opening_balance': _opening(p['txns']),
            'closing_balance': _pbv(p['txns'][-1].get('balance')),
        })
        for w in p['warnings']:
            warnings.append(f'{p["filename"]}: {w}')

    if duplicates_total:
        warnings.append(f'{duplicates_total} row(s) appeared in more than one file (overlapping periods) and were kept once.')

    starts = [p['p_start'] for p in parts if p['p_start']]
    ends = [p['p_end'] for p in parts if p['p_end']]
    period = f'{min(starts):%d/%m/%Y} to {max(ends):%d/%m/%Y}' if starts and ends else 'N/A'

    banks = [p['bank_name'] for p in parts if p['bank_name']]
    account_info = dict(parts[0]['account_info']) if parts else {}
    account_info.pop('opening_balance', None)
    account_info.pop('closing_balance', None)

    return {
        'bank_name': banks[0] if banks else 'Bank',
        'account_info': account_info,
        'period': period,
        'transactions': merged,
        'warnings': warnings,
        'notes': f'Merged from {len(parts)} statement file(s)',
        'merge_report': {
            'files': files_report,
            'boundaries': boundaries,
            'duplicates_removed': duplicates_total,
            'balance_continuous': all(b['balance_continues'] for b in boundaries),
        },
    }


def _opening(txns: List[Dict]) -> Optional[float]:
    """Balance before the first row: first balance minus its own movement."""
    if not txns:
        return None
    t = txns[0]
    bal = _pbv(t.get('balance'))
    if bal is None:
        return None
    return round(bal - _amount(t.get('deposit')) + _amount(t.get('withdrawal')), 2)


def verification_stats(txns: List[Dict]) -> Dict:
    """Running-balance check over every row (row 1 is the opening row)."""
    matched = total = 0
    prev = None
    for i, t in enumerate(txns):
        bal = _pbv(t.get('balance'))
        if i > 0 and prev is not None and bal is not None:
            total += 1
            if abs(bal - (prev + _amount(t.get('deposit')) - _amount(t.get('withdrawal')))) < 0.02:
                matched += 1
        prev = bal
    return {'matched': matched, 'total': total, 'percent': round(matched / total * 100, 1) if total else 0}

