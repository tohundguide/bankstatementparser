"""
Karur Vysya Bank (KVB) Statement Parser
=======================================
The net-banking "ACCOUNT STATEMENT" (feedback 3154667d26, 17 Nov 2026):

    Txn Date Value Date Particulars Ref. No. Debit Credit Balance
    01-OCT-2026 01-OCT-2026 B/F... - - - 12,345.67           <- the opening
    02-OCT-2026 UPI-DR-512345678901-PAYEE-                  <- the transaction date, the particulars begin
    02-OCT-2026 120.00 0.00 12,225.67                       <- the value date, [ref], debit, credit, balance
    10:15:22 SBIN-00012345678                               <- the time, the particulars go on
    03-OCT-2026                                              <- a row whose particulars sit on the next line:
    03-OCT-2026 CASH DEP-SELF-.-KADIRI - 0.00 5,000.00 17,225.67

Both columns are printed (the empty one as 0.00), so each side is read, never worked out. A line of digits
alone between rows is the next row's reference.
"""
import re
from typing import Dict, List, Optional

from parsers.base_parser import BaseBankParser


class KVBParser(BaseBankParser):
    BANK_CODE = "kvb"
    BANK_NAME = "Karur Vysya Bank"
    DETECTION_KEYWORDS = ["Karur Vysya", "KVBL0", "CA-KVB", "SB-KVB", "St. Period"]
    DETECTION_RULES = [
        (r"\bKVBL0\w{6}\b", 10, True),
        ("Karur Vysya", 10, False),
        ("kvb.co.in", 10, False),
        (r"Acc\.\s*Type\s*:\s*(?:CA|SB|OD|CC)-KVB", 10, True),
        (r"NEFT\s+(?:DR|CR)-KVBL", 5, True),
        ("St. Period :", 1, False),
        ("Trans.Type :", 1, False),
    ]

    DATE = r'\d{2}-[A-Za-z]{3}-\d{4}'
    AMOUNTS_RE = re.compile(r'^(' + DATE + r')\s+(.*?)\s*(?:(\S+)\s+)?([\d,]+\.\d{2})\s+([\d,]+\.\d{2})\s+(-?[\d,]+\.\d{2})\s*$')
    BF_RE = re.compile(r'^' + DATE + r'\s+' + DATE + r'\s+B/F\.*\s+-\s+-\s+-\s+(-?[\d,]+\.\d{2})\s*$', re.IGNORECASE)
    DATE_LINE_RE = re.compile(r'^(' + DATE + r')\b\s*(.*)$')
    TIME_LINE_RE = re.compile(r'^\d{2}:\d{2}:\d{2}\b\s*(.*)$')
    REF_LINE_RE = re.compile(r'^\d{6,}$')
    NOISE_RE = re.compile(r'^(Txn Date\s+Value Date|ACCOUNT STATEMENT$|Note: This is a computer|\d{1,3}$)', re.IGNORECASE)
    MON = {m: i for i, m in enumerate(['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'], 1)}

    def _date(self, s: str) -> str:
        d, m, y = s.split('-')
        return f"{int(d):02d}-{self.MON.get(m[:3].lower(), 0):02d}-{y}"

    def parse(self, raw_text: str) -> Dict:
        lines = [l.strip() for l in raw_text.split('\n')]
        head = next((i for i, l in enumerate(lines) if l.startswith('Txn Date') and 'Balance' in l), None)
        opening: Optional[float] = None
        transactions: List[Dict] = []
        if head is None:
            return {'bank_name': self.BANK_NAME, 'bank_code': self.BANK_CODE, 'account_info': self._account(raw_text, None, None),
                    'period': self._period(raw_text), 'transactions': []}
        body = [l for l in lines[head + 1:] if l and not self.NOISE_RE.match(l)]
        t: Optional[Dict] = None
        pending: List[str] = []       # lines after the last row and before the next amounts line
        for l in body:
            bf = self.BF_RE.match(l)
            if bf:
                opening = float(bf.group(1).replace(',', ''))
                continue
            a = self.AMOUNTS_RE.match(l)
            if a:
                # The lines since the last row: its own tail (a time line and what follows), then this row's
                # reference (digits alone) and its transaction-date line, which may carry the particulars.
                txn_date, first, ref = a.group(1), '', a.group(3) or ''
                tail, own = [], []
                for p in pending:
                    if own or self.DATE_LINE_RE.match(p) or self.REF_LINE_RE.match(p):
                        own.append(p)
                    else:
                        tail.append(p)
                if t is not None:
                    t['particulars'] += ' ' + ' '.join(self.TIME_LINE_RE.sub(r'\1', x) for x in tail)
                    transactions.append(t)
                for p in own:
                    if self.REF_LINE_RE.match(p):
                        ref = ref if ref and ref != '-' else p
                        continue
                    dl = self.DATE_LINE_RE.match(p)
                    if dl:
                        txn_date, first = dl.group(1), (first + ' ' + dl.group(2)).strip()
                    else:
                        first = (first + ' ' + p).strip()
                debit, credit = a.group(4).replace(',', ''), a.group(5).replace(',', '')
                t = {'date': self._date(txn_date), 'particulars': (first + ' ' + a.group(2)).strip(),
                     'chq_ref': '' if ref == '-' else ref,
                     'withdrawal': debit if float(debit) else '', 'deposit': credit if float(credit) else '',
                     'balance': f"{float(a.group(6).replace(',', '')):.2f}"}
                pending = []
                continue
            pending.append(l)
        if t is not None:
            t['particulars'] += ' ' + ' '.join(self.TIME_LINE_RE.sub(r'\1', x) for x in pending
                                                  if not self.DATE_LINE_RE.match(x) and not self.REF_LINE_RE.match(x))
            transactions.append(t)
        for x in transactions:
            x['particulars'] = re.sub(r'\s+', ' ', x['particulars']).strip()
        closing = transactions[-1]['balance'] if transactions else None
        return {'bank_name': self.BANK_NAME, 'bank_code': self.BANK_CODE,
                'account_info': self._account(raw_text, opening, closing),
                'period': self._period(raw_text), 'transactions': transactions}

    def _account(self, raw_text: str, opening: Optional[float], closing: Optional[str]) -> Dict:
        acct = re.search(r'Acc\.\s*No\.\s*:\s*(\d+)', raw_text)
        typ = re.search(r'Acc\.\s*Type\s*:\s*(\S+)', raw_text)
        ifsc = re.search(r'\bKVBL0\w{6}\b', raw_text)
        return {'account_number': acct.group(1) if acct else '', 'account_holder': '', 'ifsc': ifsc.group(0) if ifsc else '',
                'type': typ.group(1) if typ else '',
                'opening_balance': f"{opening:.2f}" if opening is not None else '', 'closing_balance': closing or ''}

    def _period(self, raw_text: str) -> str:
        m = re.search(r'St\.\s*Period\s*:\s*(\S+)\s+to\s+(\S+)', raw_text)
        return f"{m.group(1)} to {m.group(2)}" if m else 'N/A'
