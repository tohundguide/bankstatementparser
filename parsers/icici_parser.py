"""
ICICI Bank Statement Parser
============================
Parses ICICI Bank statement PDFs which come in three format variants:

Format 1a (Detailed - TULASHI style):
  Columns: Sr No | Tran ID | DD-Mon-YYYY | DD-Mon-YYYY | Narration | Withdrawal | Deposit | Balance
  - Dates use DD-Mon-YYYY format (02-Apr-2023)
  - Tran ID is on same line (C7272298)

Format 1b (Detailed - WETECH/AZU style):
  Columns: Sl No | Tran Id | DD/Mon/YYYY | DD/Mon/YYYY | Posted Date | Cheque no | Narration | Withdrawal | Deposit | Balance
  - Dates use DD/Mon/YYYY format (07/Mar/2024)
  - Tran ID is SPLIT across TWO lines (S8459 on line1 + 7152 on line2)
  - Has "Posted Date" and "Transaction Date" columns with timestamps

Format 2 (Simple Statement - savings):
  Columns: Date | Description | Amount | Type (CR/DR)
  - DD-MM-YYYY dates
"""

import re
from typing import Dict, List, Optional
from parsers.base_parser import BaseBankParser


class ICICIBankParser(BaseBankParser):
    """Parser for ICICI Bank statements (all format variants)."""

    BANK_CODE = "icici_bank"
    BANK_NAME = "ICICI Bank"
    DETECTION_KEYWORDS = [
        "ICIC0",
        "ICICI",
        "Detailed Statement",
        "A/C No:",
        "Cust ID:",
        "Transaction Remarks",
        "Withdrawl",  # ICICI typo in their PDFs
        "ICICI BANK",
        "End Of Statement",
    ]
    DETECTION_RULES = [
        (r"\bICIC0\w{6}\b", 10, True),
        ("icicibank.com", 10, False),
        ("ICICI BANK", 3, False),
        ("Transaction Remarks", 3, False),
        ("Cust ID:", 1, False),
        ("Withdrawl", 1, False),
        ("End Of Statement", 1, False),
    ]


    # ── Format 3: "Statement of Transactions in Saving Account no. ..." (feedback 415dbedc2a, 17 Nov 2026) ──
    #   PAYEE NAME                              <- the line before a row belongs to it
    #   12 01.04.2025 25000.00 412345.67        <- S No, date, ONE amount, balance (a cheque no. may sit between)
    #   BIL/INFT/FD12345678/ PAYEE              <- the remarks, one or more lines
    # No opening balance is printed: each side follows from the balance, the first row's from its remarks,
    # and that one figure is marked amount_source 'inferred'.
    SAVINGS_HEAD_RE = re.compile(r'Statement of Transactions in Saving(?:s)? Account', re.IGNORECASE)
    SAVINGS_ROW_RE = re.compile(r'^(\d{1,4})\s+(\d{2}\.\d{2}\.\d{4})\s+(?:(\S+)\s+)?([\d,]+\.\d{2})\s+(-?[\d,]+\.\d{2})\s*$')
    SAVINGS_NOISE_RE = re.compile(r'^(www\.icici|Please call from|Never share your OTP|Transaction Withdrawal Deposit|S No\.\s+Cheque|Date Amount \(INR\)|Sincerly|Sincerely|Team ICICI|This is a system generated|Legends for transactions|\d{1,3}$)', re.IGNORECASE)
    CREDIT_HINT_RE = re.compile(r'^(NEFT-|RTGS-|IMPS/P2A|MMT/IMPS/\d+/[^/]*/?CR|INT\.?PD|.*\bInterest\b|.*\bCREDIT\b|BY\b|CLG/|CMS/)', re.IGNORECASE)

    def _parse_savings_statement(self, raw_text: str) -> Dict:
        lines = [l.strip() for l in raw_text.split('\n')]
        legend = next((i for i, l in enumerate(lines) if l.lower().startswith('legends for transactions')), len(lines))
        lines = [l for l in lines[:legend] if l and not self.SAVINGS_NOISE_RE.match(l)]
        start = next((i for i, l in enumerate(lines) if l.startswith('Date Amount')), 0)
        rows, blocks = [], []
        pending: List[str] = []
        for l in lines[start:]:
            m = self.SAVINGS_ROW_RE.match(l)
            if m:
                # The last line before a row is its payee; the lines before that are the previous row's remarks.
                name = pending[-1] if pending else ''
                if rows:
                    blocks[-1].extend(pending[:-1] if pending else [])
                rows.append(m)
                blocks.append([name] if name else [])
                pending = []
            else:
                pending.append(l)
        if rows:
            blocks[-1].extend(pending)
        transactions: List[Dict] = []
        prev = None
        for k, (m, block) in enumerate(zip(rows, blocks)):
            d, mo, y = m.group(2).split('.')
            amount = m.group(4).replace(',', '')
            balance = float(m.group(5).replace(',', ''))
            text = re.sub(r'\s+', ' ', ' '.join(block)).strip()
            remarks = ' '.join(block[1:]) if len(block) > 1 else text
            t = {'date': f'{d}-{mo}-{y}', 'particulars': text, 'chq_ref': m.group(3) or '', 'withdrawal': '', 'deposit': '',
                 'balance': f'{balance:.2f}'}
            if prev is not None:
                if balance > prev + 1e-9:
                    t['deposit'] = amount
                else:
                    t['withdrawal'] = amount
            else:
                t['deposit' if self.CREDIT_HINT_RE.match(remarks.strip()) else 'withdrawal'] = amount
                t['amount_source'] = 'inferred'
            prev = balance
            transactions.append(t)
        first = transactions[0] if transactions else None
        opening = ''
        if first:
            f_amt = float(first['deposit'] or first['withdrawal'])
            opening = f"{float(first['balance']) - f_amt if first['deposit'] else float(first['balance']) + f_amt:.2f}"
        acct = re.search(r'Saving(?:s)? Account no\.\s*(\d+)', raw_text, re.IGNORECASE)
        per = re.search(r'for the period\s+(.+?)\s+-\s+(.+?)\s*$', raw_text, re.IGNORECASE | re.MULTILINE)
        return {
            'bank_name': self.BANK_NAME, 'bank_code': self.BANK_CODE,
            'account_info': {'account_number': acct.group(1) if acct else '', 'account_holder': '', 'ifsc': '',
                             'opening_balance': opening, 'closing_balance': transactions[-1]['balance'] if transactions else ''},
            'period': f"{per.group(1)} to {per.group(2)}" if per else 'N/A',
            'transactions': transactions,
        }

    # ── Format 4: "Account statement ... S.no Transaction ID Transaction date ..." (feedback 74f1da4cf5) ──
    #   3 S1234567 12-Sep- MMT/IMPS/5 125000.00 -        <- withdrawal, deposit "-" (or "-" then deposit)
    #   2025 123456789 1234567.89                         <- the year, more of the row, the balance
    # In the PDF's text the "-" of the empty column does not keep its place, so each side follows from the
    # balance (as every one-amount layout here); the first row's from where its "-" stands, marked inferred.
    CURRENT_HEAD_RE = re.compile(r'S\.no\s+Transaction\s+Transaction\s+Cheque\s+No\s+Description\s+Withdrawal\s+Deposit', re.IGNORECASE)
    CURRENT_ROW_RE = re.compile(r'^(\d{1,5})\s+(\S+)\s+(\d{1,2}-[A-Za-z]{3}-)\s*(.*?)\s+([\d,]+\.\d{2}|-)\s+([\d,]+\.\d{2}|-)\s*$')
    CURRENT_YEAR_RE = re.compile(r'^(\d{4})\b\s*(.*?)\s*(-?[\d,]+\.\d{2})\s*$')
    # A row whose balance fits prints it on the row's own line (below nil too), and its year line has none:
    #   41 S1234567 16-Oct- RTGS- 1250000.00 -987654.32
    #   2025 JAKAR2025
    CURRENT_OD_ROW_RE = re.compile(r'^(\d{1,5})\s+(\S+)\s+(\d{1,2}-[A-Za-z]{3}-)\s*(.*?)\s+([\d,]+\.\d{2})\s+(-[\d,]+\.\d{2})\s*$')
    CURRENT_YEAR_ONLY_RE = re.compile(r'^(\d{4})\b\s*(.*)$')
    CURRENT_NOISE_RE = re.compile(r'^(Generated on\s*:|S\.no\s+Transaction|ID\s+date\s+\(Dr\))', re.IGNORECASE)
    MON = {m: i for i, m in enumerate(['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'], 1)}

    def _parse_current_statement(self, raw_text: str) -> Dict:
        transactions: List[Dict] = []
        t: Optional[Dict] = None
        started = False
        for raw in raw_text.split('\n'):
            l = raw.strip()
            if not l:
                continue
            if self.CURRENT_HEAD_RE.search(l):
                started = True
                continue
            if not started or self.CURRENT_NOISE_RE.match(l):
                continue
            m = self.CURRENT_ROW_RE.match(l) or self.CURRENT_OD_ROW_RE.match(l)
            if m and m.group(5) != '-':
                if t:
                    transactions.append(t)
                # The row's amount, then "-" (its balance on the year line) or the balance itself, which the
                # PDF puts on the row's own line whenever it fits (above or below nil).
                tail = m.group(6)
                t = {'date': '', '_dm': m.group(3), 'particulars': m.group(4), 'chq_ref': m.group(2),
                     'withdrawal': m.group(5).replace(',', ''), 'deposit': '',
                     # "-123.45" is either a balance below nil or the empty column's "-" glued to the balance:
                     # the text cannot say which, so the balance chain decides below (_glued).
                     'balance': '' if tail == '-' else f"{float(tail.replace(',', '')):.2f}",
                     '_glued': tail != '-'}
                continue
            if t and not t['date'] and t['balance']:
                y = self.CURRENT_YEAR_ONLY_RE.match(l)
                if y:
                    day, mon, _ = t['_dm'].split('-')
                    t['date'] = f"{int(day):02d}-{self.MON.get(mon[:3].lower(), 0):02d}-{y.group(1)}"
                    if y.group(2):
                        t['chq_ref'] += y.group(2)
                    continue
            if t and not t['date']:
                y = self.CURRENT_YEAR_RE.match(l)
                if y:
                    day, mon, _ = t['_dm'].split('-')
                    t['date'] = f"{int(day):02d}-{self.MON.get(mon[:3].lower(), 0):02d}-{y.group(1)}"
                    t['balance'] = f"{float(y.group(3).replace(',', '')):.2f}"
                    if y.group(2):
                        t['chq_ref'] += y.group(2)
                    continue
            if t:
                t['particulars'] += ' ' + l
        if t:
            transactions.append(t)
        transactions = [x for x in transactions if x['date']]
        # A balance printed on the row's own line loses or gains its minus in the PDF's text (a glued "-" from
        # the empty column, or a minus dropped): the sign that lets the row's own amount carry the balance from
        # the row before (or, for the first row, to the row after) is the one; when both or neither do, the
        # printed sign stands.
        def fits(b_from: float, b_to: float, amt: str) -> bool:
            return abs(abs(b_to - b_from) - float(amt or 0)) < 0.005
        for k, x in enumerate(transactions):
            if not x.pop('_glued', False):
                continue
            neg = -abs(float(x['balance'])); pos = abs(float(x['balance']))
            amt = x['withdrawal'] or x['deposit']
            if k > 0:
                p_bal = float(transactions[k - 1]['balance'])
                if fits(p_bal, pos, amt) != fits(p_bal, neg, amt):
                    x['balance'] = f"{pos if fits(p_bal, pos, amt) else neg:.2f}"
            elif k + 1 < len(transactions):
                n = transactions[k + 1]
                n_amt = n['withdrawal'] or n['deposit']
                if fits(pos, float(n['balance']), n_amt) != fits(neg, float(n['balance']), n_amt):
                    x['balance'] = f"{pos if fits(pos, float(n['balance']), n_amt) else neg:.2f}"
        prev = None
        for x in transactions:
            x.pop('_glued', None)
            x.pop('_dm', None)
            x['particulars'] = re.sub(r'\s+', ' ', x['particulars']).strip()
            amount = x['withdrawal'] or x['deposit']
            bal = float(x['balance'])
            if prev is None:
                x['amount_source'] = 'inferred'
            else:
                x['withdrawal'], x['deposit'] = ('', amount) if bal > prev + 1e-9 else (amount, '')
            prev = bal
        opening = ''
        if transactions:
            f = transactions[0]
            opening = f"{float(f['balance']) + float(f['withdrawal'] or 0) - float(f['deposit'] or 0):.2f}"
        acct = re.search(r'Account number:\s*(\d+)', raw_text)
        ifsc = re.search(r'IFSC code:\s*(ICIC\w{7})', raw_text)
        per = re.search(r'Statement of transactions from\s+(.+?)\s+-\s+(.+?)\s*$', raw_text, re.IGNORECASE | re.MULTILINE)
        return {
            'bank_name': self.BANK_NAME, 'bank_code': self.BANK_CODE,
            'account_info': {'account_number': acct.group(1) if acct else '', 'account_holder': '', 'ifsc': ifsc.group(1) if ifsc else '',
                             'opening_balance': opening, 'closing_balance': transactions[-1]['balance'] if transactions else ''},
            'period': f"{per.group(1)} to {per.group(2)}" if per else 'N/A',
            'transactions': transactions,
        }

    def parse(self, raw_text: str) -> Dict:
        """Parse ICICI Bank statement text into structured data."""
        if self.SAVINGS_HEAD_RE.search(raw_text[:600]):
            return self._parse_savings_statement(raw_text)
        if self.CURRENT_HEAD_RE.search(raw_text[:2000]):
            return self._parse_current_statement(raw_text)
        # Check for "Detailed Statement" format markers
        # Must NOT match the simple format which contains "Transaction date"
        first_500 = raw_text[:500]
        is_detailed = (
            'Detailed' in raw_text[:200]
            or 'Tran Id' in first_500
            or 'Tran\nId' in first_500
            or ('Sl' in first_500 and 'Tran' in first_500 and 'Remarks' in raw_text[:800])
        )
        if is_detailed:
            return self._parse_detailed(raw_text)
        else:
            return self._parse_simple(raw_text)

    def _extract_account_info(self, raw_text: str) -> Dict:
        """Extract account information from the header."""
        info = {
            'account_number': '',
            'account_holder': '',
            'branch': '',
            'ifsc': '',
            'type': '',
        }

        m = re.search(r'A/C\s*No[:\s]+(\d+)', raw_text)
        if m:
            info['account_number'] = m.group(1)
        else:
            m = re.search(r'Account\s*Number[:\s]+(\d+)', raw_text)
            if m:
                info['account_number'] = m.group(1)

        m = re.search(r'Name:\s*(.+?)(?:\s+A/C|\s+Branch)', raw_text, re.DOTALL)
        if m:
            name = re.sub(r'\s+', ' ', m.group(1).strip())
            info['account_holder'] = name

        m = re.search(r'IFSC\s*Code[:\s]+(ICIC\d+)', raw_text)
        if m:
            info['ifsc'] = m.group(1)

        m = re.search(r'(?:A/C\s+)?Branch[:\s]+(.+?)(?:\n|Branch Address)', raw_text)
        if m:
            info['branch'] = m.group(1).strip()

        m = re.search(r'A/C\s*Type[:\s]+(\w+)', raw_text)
        if m:
            info['type'] = m.group(1)

        return info

    def _extract_period(self, raw_text: str) -> str:
        """Extract statement period."""
        m = re.search(
            r'(?:Transaction\s+)?Period[:\s]*(?:From\s+)?(\d{2}/\d{2}/\d{4})\s+(?:To\s+)?(\d{2}/\d{2}/\d{4})',
            raw_text
        )
        if m:
            return f"{m.group(1)} to {m.group(2)}"

        m = re.search(r'From\s+(\d{2}/\d{2}/\d{4})\s+To\s+(\d{2}/\d{2}/\d{4})', raw_text)
        if m:
            return f"{m.group(1)} to {m.group(2)}"

        m = re.search(r'date\s*:\s*From\s+(\d{2}/\d{2}/\d{4})\s+To\s+(\d{2}/\d{2}/\d{4})',
                       raw_text, re.IGNORECASE)
        if m:
            return f"{m.group(1)} to {m.group(2)}"

        return 'N/A'

    def _parse_detailed(self, raw_text: str) -> Dict:
        """
        Parse "Detailed Statement" format.
        Handles two sub-variants:
        - TULASHI style: DD-Mon-YYYY dates, Tran ID on same line
        - WETECH/AZU style: DD/Mon/YYYY dates, Tran ID split across lines, Posted Date column
        """
        account_info = self._extract_account_info(raw_text)
        period = self._extract_period(raw_text)

        lines = raw_text.split('\n')
        transactions = []
        current_txn = None

        # Detect sub-variant
        is_variant_b = bool(re.search(r'\d{2}/\w{3}/\d{2,4}', raw_text[:2000]))

        # Skip patterns for header/footer/metadata
        skip_patterns = [
            r'^\s*Detailed\s*$', r'^\s*Statement\s*$', r'^\s*Name:',
            r'^\s*Address:', r'^\s*A/C\s*No:', r'^\s*Jt\.\s*Holder:',
            r'^\s*Transaction\s+Date\s+from:', r'^\s*Transaction\s+Period:',
            r'^\s*Request/Download', r'^\s*Date:\s*$', r'^\s*Advanced\s+Search',
            r'^\s*Amount\s+from:', r'^\s*Cheque\s+number',
            r'^\s*from:\s+NA', r'^\s*Transaction\s+Type:',
            r'^\s*Transaction\s+remarks:', r'^\s*Transaction\s+type:',
            r'^\s*Sl\s+Tran', r'^\s*Sr\s+Tran', r'^\s*No\s+I[Dd]',
            r'^\s*Date$', r'^A/C\s+Branch:', r'^\s*Branch\s+Code:',
            r'^\s*IFSC\s+Code:', r'^\s*Account\s+Currency:',
            r'^\s*Page\s+Total', r'^\s*Opening\s+Bal:',
            r'^\s*Withdrawls:', r'^\s*Deposits:', r'^\s*Closing\s+Bal:',
            r'^\s*Legends\s+Used', r'^\s*\d+\.\s+\w+\s+-\s+',  # Legend items
            r'^\s*Page\s+\d+\s+of', r'^-+End\s+Of\s+Statement',
            r'^\s*Statement\s+\d', r'^\s*Cust\s+ID:',
            r'^\s*Branch\s+Address:', r'^\s*Branch\s+Code:',
        ]

        # Amount patterns at end of line
        amount_3_pattern = re.compile(
            r'([\d,]+\.\d{2}|NA)\s+([\d,]+\.\d{2}|NA)\s+(-?[\d,]+\.\d{2})\s*$'
        )

        if is_variant_b:
            transactions = self._parse_detailed_variant_b(lines, skip_patterns, amount_3_pattern)
        else:
            transactions = self._parse_detailed_variant_a(lines, skip_patterns, amount_3_pattern)

        # Clean up narrations
        for txn in transactions:
            txn['particulars'] = re.sub(r'\s+', ' ', txn['particulars']).strip()
            txn['particulars'] = re.sub(
                r'\s+(?:NA\s+)?[\d,]+\.\d{2}\s*$', '', txn['particulars']
            ).strip()

        return {
            'bank_name': self.BANK_NAME,
            'bank_code': self.BANK_CODE,
            'account_info': account_info,
            'period': period,
            'transactions': transactions,
        }

    def _parse_detailed_variant_a(self, lines, skip_patterns, amount_3_pattern):
        """
        TULASHI style: Sr No TranID DD-Mon-YYYY DD-Mon-YYYY ... amounts
        """
        transactions = []
        current_txn = None

        txn_pattern = re.compile(
            r'^\s*(\d+)\s+'
            r'([A-Z]\d+|M\d+|S\d+)\s+'
            r'(\d{2}-\w{3}-\s*\d{4})\s+'
            r'(\d{2}-\w{3}-\d{4})\s+'
            r'(.+)$'
        )

        for i, line in enumerate(lines):
            stripped = line.strip()
            if not stripped:
                continue

            if self._should_skip(stripped, skip_patterns):
                continue

            m = txn_pattern.match(stripped)
            if m:
                if current_txn:
                    transactions.append(current_txn)

                tran_id = m.group(2)
                txn_date = m.group(4).strip()
                rest = m.group(5).strip()

                am = amount_3_pattern.search(rest)
                if am:
                    narration = rest[:am.start()].strip()
                    withdrawal = am.group(1)
                    deposit = am.group(2)
                    balance = am.group(3)
                else:
                    narration = rest
                    withdrawal = ''
                    deposit = ''
                    balance = ''

                current_txn = {
                    'date': self._normalize_date(txn_date),
                    'particulars': narration,
                    'chq_ref': tran_id,
                    'withdrawal': self._clean_amount(withdrawal),
                    'deposit': self._clean_amount(deposit),
                    'balance': balance.replace(',', '') if balance else '',
                }
                continue

            # Continuation line
            if current_txn:
                if (not re.match(r'^\s*Page\s+\d+', stripped, re.IGNORECASE)
                    and 'system-generated' not in stripped.lower()
                    and 'This is a' not in stripped):
                    current_txn['particulars'] += ' ' + stripped

        if current_txn:
            transactions.append(current_txn)

        return transactions

    def _parse_detailed_variant_b(self, lines, skip_patterns, amount_3_pattern):
        """
        WETECH/AZU style:
        Line format (transaction split across 2+ lines):
        Ln1: "1 S8459 07/Mar/2 07/Mar/2024 07/03/2024 BIL/INFT/... 60,000.00 60,000.00"
        Ln2: "7152 024 03:48:40 PM 69/Capital Money/"
        Ln3: "UMMAR HUSSAIN R"
        
        Or:
        Ln1: "1 S5905 29/Jun/20 29/Jun/2023 29/06/2023 MMT/IMPS/... 1.00 1.00"  
        Ln2: "1658 23 10:27:14 PM 06085/FTTransferP2"
        """
        transactions = []
        current_txn = None

        # Pattern for line starting with Sl No + partial Tran ID + partial date
        # e.g. "1 S8459 07/Mar/2" or "1 S5905 29/Jun/20"
        txn_start_pattern = re.compile(
            r'^\s*(\d+)\s+'          # Sl No
            r'([A-Z]\d+)\s+'         # Partial Tran ID (e.g. S8459)
            r'(\d{2}/\w{3}/\d{1,4})' # Partial Value Date (could be incomplete)
            r'\s+(.+)$'              # Rest of line
        )

        # Full date pattern: DD/Mon/YYYY
        date_pattern = re.compile(r'(\d{2}/\w{3}/\d{4})')
        
        # Posted date pattern: DD/MM/YYYY followed by timestamp
        posted_date_pattern = re.compile(r'(\d{2}/\d{2}/\d{4})\s+(\d{2}:\d{2}:\d{2}\s+(?:AM|PM))')

        # Amount pattern: amounts at end of line (2 values for withdrawal/deposit + balance)
        # or 1 value + 1 value
        amt_2_pattern = re.compile(r'([\d,]+\.\d{2})\s+([\d,]+\.\d{2})\s*$')
        amt_1_pattern = re.compile(r'([\d,]+\.\d{2})\s*$')

        # Continuation line pattern: starts with digits (rest of Tran ID) + digits (rest of year)
        continuation_pattern = re.compile(
            r'^(\d+)\s+'   # Rest of Tran ID
            r'(\d{2,4})\s+'  # Rest of year/date
            r'(.*)$'       # Rest (timestamp + narration)
        )

        i = 0
        while i < len(lines):
            stripped = lines[i].strip()
            i += 1

            if not stripped:
                continue

            if self._should_skip(stripped, skip_patterns):
                continue

            m = txn_start_pattern.match(stripped)
            if m:
                if current_txn:
                    transactions.append(current_txn)

                sl_no = m.group(1)
                partial_tran_id = m.group(2)
                partial_value_date = m.group(3)
                rest = m.group(4).strip()

                # Extract full dates and amounts from rest
                dates = date_pattern.findall(rest)
                txn_date = dates[0] if dates else partial_value_date
                
                # Find amounts at end
                am2 = amt_2_pattern.search(rest)
                am1 = amt_1_pattern.search(rest)

                narration = rest
                withdrawal = ''
                deposit = ''
                balance = ''

                if am2:
                    narration = rest[:am2.start()].strip()
                    amt_val1 = am2.group(1).replace(',', '')
                    amt_val2 = am2.group(2).replace(',', '')
                    
                    # Determine withdrawal/deposit
                    prev_bal = float(transactions[-1]['balance']) if transactions and transactions[-1]['balance'] else 0
                    curr = float(amt_val2)
                    if curr > prev_bal:
                        deposit = amt_val1
                    else:
                        withdrawal = amt_val1
                    balance = amt_val2
                elif am1:
                    narration = rest[:am1.start()].strip()
                    balance = am1.group(1).replace(',', '')

                # Clean narration: remove dates and timestamps
                narration = re.sub(r'\d{2}/\w{3}/\d{4}', '', narration)
                narration = re.sub(r'\d{2}/\d{2}/\d{4}', '', narration)
                narration = re.sub(r'\d{2}:\d{2}:\d{2}\s+(?:AM|PM)', '', narration)
                narration = narration.strip()

                current_txn = {
                    'date': self._normalize_date_slash(txn_date),
                    'particulars': narration,
                    'chq_ref': partial_tran_id,
                    'withdrawal': withdrawal,
                    'deposit': deposit,
                    'balance': balance,
                }

                # Look at the next line - likely continuation with rest of Tran ID
                if i < len(lines):
                    next_stripped = lines[i].strip()
                    cm = continuation_pattern.match(next_stripped)
                    if cm:
                        rest_tran_id = cm.group(1)
                        current_txn['chq_ref'] += rest_tran_id
                        # Rest could be timestamp + narration continuation
                        cont_rest = cm.group(3).strip()
                        # Remove timestamp
                        cont_rest = re.sub(r'\d{2}:\d{2}:\d{2}\s+(?:AM|PM)', '', cont_rest).strip()
                        if cont_rest:
                            current_txn['particulars'] += ' ' + cont_rest
                        i += 1

                continue

            # Pure continuation line (narration wraps)
            if current_txn:
                if (not re.match(r'^\s*Page\s+\d+', stripped, re.IGNORECASE)
                    and 'system-generated' not in stripped.lower()
                    and not re.match(r'^-+End', stripped)):
                    current_txn['particulars'] += ' ' + stripped

        if current_txn:
            transactions.append(current_txn)

        return transactions

    def _should_skip(self, line: str, patterns: list) -> bool:
        """Check if a line matches any skip pattern."""
        for pat in patterns:
            if re.match(pat, line, re.IGNORECASE):
                return True
        return False

    def _parse_simple(self, raw_text: str) -> Dict:
        """
        Parse simple ICICI savings account statement format.
        Columns: Date | Description | Amount | Type (CR/DR)
        """
        account_info = self._extract_account_info(raw_text)
        period = self._extract_period(raw_text)

        lines = raw_text.split('\n')
        transactions = []
        current_txn = None

        txn_date_pattern = re.compile(r'^(\d{2}-\d{2}-\d{4})\s+(.+)')
        amount_type_pattern = re.compile(r'([\d,]+\.\d{2})\s*$')
        cr_dr_pattern = re.compile(r'\b(CR|DR)\b')

        for i, line in enumerate(lines):
            stripped = line.strip()
            if not stripped:
                continue

            if any(kw in stripped for kw in [
                'Account Number:', 'Transaction date', 'Date Description',
                'system-generated', 'Page '
            ]):
                if 'Account Number:' in stripped and not account_info['account_number']:
                    m = re.search(r'Account\s+Number[:\s]+(\d+)', stripped)
                    if m:
                        account_info['account_number'] = m.group(1)
                continue

            m = txn_date_pattern.match(stripped)
            if m:
                if current_txn:
                    transactions.append(current_txn)

                date_str = m.group(1)
                rest = m.group(2).strip()

                cr_dr_m = cr_dr_pattern.search(rest)
                txn_type = cr_dr_m.group(1) if cr_dr_m else ''

                if cr_dr_m:
                    rest = rest[:cr_dr_m.start()].strip()

                current_txn = {
                    'date': date_str,
                    'particulars': rest,
                    'chq_ref': '',
                    'withdrawal': '',
                    'deposit': '',
                    'balance': '',
                    '_type': txn_type,
                }
                continue

            if current_txn:
                amt_m = amount_type_pattern.search(stripped)
                cr_dr_m = cr_dr_pattern.search(stripped)

                if amt_m and not current_txn.get('_amount_set'):
                    amount = amt_m.group(1).replace(',', '')
                    desc_part = stripped[:amt_m.start()].strip()
                    if desc_part:
                        current_txn['particulars'] += ' ' + desc_part

                    txn_type = current_txn.get('_type', '')
                    if not txn_type and cr_dr_m:
                        txn_type = cr_dr_m.group(1)

                    if txn_type == 'DR':
                        current_txn['withdrawal'] = amount
                    else:
                        current_txn['deposit'] = amount
                    current_txn['_amount_set'] = True
                elif cr_dr_m and not current_txn.get('_type'):
                    current_txn['_type'] = cr_dr_m.group(1)
                    desc_part = stripped.replace(cr_dr_m.group(0), '').strip()
                    if desc_part:
                        current_txn['particulars'] += ' ' + desc_part
                else:
                    current_txn['particulars'] += ' ' + stripped

        if current_txn:
            transactions.append(current_txn)

        for txn in transactions:
            txn.pop('_type', None)
            txn.pop('_amount_set', None)
            txn['particulars'] = re.sub(r'\s+', ' ', txn['particulars']).strip()

        return {
            'bank_name': self.BANK_NAME,
            'bank_code': self.BANK_CODE,
            'account_info': account_info,
            'period': period,
            'transactions': transactions,
        }

    def _normalize_date(self, date_str: str) -> str:
        """Convert DD-Mon-YYYY to DD-MM-YYYY."""
        months = {
            'Jan': '01', 'Feb': '02', 'Mar': '03', 'Apr': '04',
            'May': '05', 'Jun': '06', 'Jul': '07', 'Aug': '08',
            'Sep': '09', 'Oct': '10', 'Nov': '11', 'Dec': '12',
        }
        m = re.match(r'(\d{2})-(\w{3})-?\s*(\d{4})', date_str)
        if m:
            day = m.group(1)
            month = months.get(m.group(2), m.group(2))
            year = m.group(3)
            return f"{day}-{month}-{year}"
        return date_str

    def _normalize_date_slash(self, date_str: str) -> str:
        """Convert DD/Mon/YYYY to DD-MM-YYYY."""
        months = {
            'Jan': '01', 'Feb': '02', 'Mar': '03', 'Apr': '04',
            'May': '05', 'Jun': '06', 'Jul': '07', 'Aug': '08',
            'Sep': '09', 'Oct': '10', 'Nov': '11', 'Dec': '12',
        }
        m = re.match(r'(\d{2})/(\w{3})/(\d{4})', date_str)
        if m:
            day = m.group(1)
            month = months.get(m.group(2), m.group(2))
            year = m.group(3)
            return f"{day}-{month}-{year}"
        return date_str

    def _clean_amount(self, val: str) -> str:
        """Clean amount value: remove commas, handle NA."""
        if not val or val.strip().upper() == 'NA':
            return ''
        return val.strip().replace(',', '')
