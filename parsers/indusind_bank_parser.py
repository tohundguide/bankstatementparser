"""
IndusInd Bank Statement Parser
================================
Parses IndusInd Bank statement PDFs.

Format characteristics:
  - Header: "IndusInd Bank", "INDUSIND BANK"
  - IFSC: INDB0... prefix
  - Account info: Account No, Customer Name, Branch
  - Column Header: Date | Narration | Chq./Ref.No. | Withdrawal Amt. | Deposit Amt. | Closing Balance
  - Alternative: Transaction Date | Value Date | Description | Debit | Credit | Balance
  - Dates in DD/MM/YYYY or DD-MM-YYYY format
  - Multi-line narrations
  - "Opening Balance" and "Closing Balance" marker rows
  - Amounts with commas and 2 decimal places
"""

import re
from typing import Dict, List, Optional
from parsers.base_parser import BaseBankParser


class IndusIndBankParser(BaseBankParser):
    """Parser for IndusInd Bank statements."""

    BANK_CODE = "indusind_bank"
    BANK_NAME = "IndusInd Bank"
    DETECTION_KEYWORDS = [
        "INDB0",
        "IndusInd",
        "INDUSIND",
        "IndusInd Bank",
        "Withdrawal Amt",
        "Deposit Amt",
        "Closing Balance",
    ]
    DETECTION_RULES = [
        (r"\bINDB0\w{6}\b", 10, True),
        ("indusind.com", 10, False),
        ("IndusInd Bank", 3, False),
        ("Withdrawal Amt", 1, False),
        ("Deposit Amt", 1, False),
        ("Closing Balance", 1, False),
        # Column header of the net-banking "Account Statement" download. That
        # layout prints no IFSC and has the bank name only in its logo image,
        # so without this anchor a counterparty IFSC in a narration
        # ("N/SBIN125251974151/SBIN0000576/...") routed it to the SBI parser.
        (r"Bank\s+Reference\s+Value\s+Date\b.*\bPayment\s+Narration\b.*\bAvailable\s+Balance", 10, True),
    ]
    NEGATIVE_RULES = [
        (r"\bHDFC0\w{6}\b", -8, True),
    ]

    # ── Net-banking "Account Statement" layout ──────────────────────────
    #   Bank Reference Value Date Type Payment Narration Debit Credit Available Balance
    #   S46173898 02-Apr-2025 02-Apr-2025 00:00:0 Debit UPI/100098381620/88392...@ibl 520 33435.36
    #   IMPS/P2A/509623169839/IDFB/PERFIOS                   <- narration line ABOVE
    #   S17303809 06-Apr-2025 06-Apr-2025 00:00:0 Credit 1 26336.36
    #   SOFTWARE SOL                                         <- narration line BELOW
    # Amounts carry no decimals when whole ("520"); the Type column says
    # Debit/Credit outright. A two-line narration is centred on the row, so
    # its first line precedes the row line and its second follows it.
    NB_ROW_RE = re.compile(
        r'^(\S+)\s+(\d{1,2}-[A-Za-z]{3}-\d{4})\s+(\d{1,2}-[A-Za-z]{3}-\d{4})\s+\d{1,2}:\d{2}(?::\d{1,2})?\s+'
        r'(Debit|Credit)\b\s*(.*?)\s*(-?[\d,]*\.?\d+)\s+(-?[\d,]*\.?\d+)\s*$',
        re.IGNORECASE,
    )
    NB_HEADER_RE = re.compile(r'Bank\s+Reference\s+Value\s+Date\b', re.IGNORECASE)
    NB_NARRATION_START_RE = re.compile(
        r'^(UPI/|IMPS/|NEFT|RTGS|N/|R/|ATM\b|INDUS\b|POS\b|ACH\b|NACH\b|ECS\b|MMT/|IB/|INT\b|'
        r'CHQ\b|CLG\b|TRF\b|CHARGES\b|SMS\b|CASH\b)',
        re.IGNORECASE,
    )
    MON = {m: f'{i:02d}' for i, m in enumerate(
        ['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'], 1)}

    def _is_netbanking_format(self, raw_text: str) -> bool:
        return bool(self.NB_HEADER_RE.search(raw_text)) and \
            any(self.NB_ROW_RE.match(l.strip()) for l in raw_text.split('\n'))

    def _parse_netbanking_format(self, raw_text: str) -> Dict:
        lines = [l.strip() for l in raw_text.split('\n')]

        def iso(d: str) -> str:
            dd, mon, yyyy = d.split('-')
            return f"{dd.zfill(2)}-{self.MON.get(mon.lower(), mon)}-{yyyy}"

        acct = re.search(r'Account\s+Number\s*:\s*(\d+)', raw_text, re.IGNORECASE)
        per = re.search(r'From\s+Date\s*:\s*(\S+)\s+To\s+Date\s*:\s*(\S+)', raw_text, re.IGNORECASE)
        # The name cell wraps around its "Customer Name (Account Name)" label.
        holder_parts = []
        for l in lines[1:12]:
            if re.match(r'^From\s+Date', l, re.IGNORECASE):
                break
            l = re.sub(r'Account\s+Number\s*:\s*\d+', '', l, flags=re.IGNORECASE)
            l = re.sub(r'^(Customer\s+Name|\(Account\s+Name\))\s*', '', l, flags=re.IGNORECASE).strip()
            if l:
                holder_parts.append(l)
        account_info = {
            'account_number': acct.group(1) if acct else '',
            'account_holder': ' '.join(holder_parts),
            'branch': '',
            'ifsc': '',
            'type': '',
        }

        noise_re = re.compile(
            r'^(Account\s+Statement|Transaction\s+Date\s*&?|Time|Page\s+\d+\s+of\s+\d+.*|Bank\s+Reference\b.*)$',
            re.IGNORECASE,
        )
        transactions: List[Dict] = []
        pending: List[str] = []
        prev_inline_empty = False
        started = False

        def split_pending(new_inline_empty: bool):
            """Divide the lines between two rows into (tail of prev, head of new)."""
            if not transactions:
                return [], pending[:]
            k = next((i for i, t in enumerate(pending) if self.NB_NARRATION_START_RE.match(t)), None)
            if k is not None:
                return pending[:k], pending[k:]
            if prev_inline_empty and new_inline_empty:
                half = len(pending) // 2
                return pending[:half], pending[half:]
            if new_inline_empty and not prev_inline_empty:
                return [], pending[:]
            return pending[:], []

        for s in lines:
            if not s:
                continue
            if self.NB_HEADER_RE.search(s):
                started = True
                continue
            if not started or noise_re.match(s):
                continue
            m = self.NB_ROW_RE.match(s)
            if not m:
                pending.append(s)
                continue
            inline = m.group(5).strip()
            tail, head = split_pending(not inline)
            if transactions and tail:
                transactions[-1]['particulars'] += ' ' + ' '.join(tail)
            pending = []
            amount = m.group(6).replace(',', '').lstrip('-')
            amount = f"{float(amount):.2f}"
            balance = f"{float(m.group(7).replace(',', '')):.2f}"
            is_credit = m.group(4).lower() == 'credit'
            transactions.append({
                'date': iso(m.group(2)),
                'particulars': ' '.join(head + ([inline] if inline else [])),
                'chq_ref': m.group(1),
                'withdrawal': '' if is_credit else amount,
                'deposit': amount if is_credit else '',
                'balance': balance,
            })
            prev_inline_empty = not inline
        if transactions and pending:
            transactions[-1]['particulars'] += ' ' + ' '.join(pending)
        for t in transactions:
            t['particulars'] = re.sub(r'\s+', ' ', t['particulars']).strip()

        return {
            'bank_name': self.BANK_NAME,
            'bank_code': self.BANK_CODE,
            'account_info': account_info,
            'period': f"{per.group(1)} to {per.group(2)}" if per else 'N/A',
            'transactions': transactions,
        }

    def parse(self, raw_text: str) -> Dict:
        """Parse IndusInd Bank statement text into structured data."""
        if self._is_netbanking_format(raw_text):
            return self._parse_netbanking_format(raw_text)

        account_info = self._extract_account_info(raw_text)
        period = self._extract_period(raw_text)

        lines = raw_text.split('\n')
        transactions = []
        current_txn = None

        # Skip patterns
        skip_patterns = [
            r'^\s*IndusInd\s+Bank',
            r'^\s*INDUSIND',
            r'^\s*Statement of Account',
            r'^\s*Account Statement',
            r'^\s*Account\s*(No|Number)',
            r'^\s*Customer\s*(Name|ID|Id|Code)',
            r'^\s*CIF\s*(No|Number|ID)',
            r'^\s*Branch\s*(Name|Code|Address)',
            r'^\s*IFSC',
            r'^\s*MICR',
            r'^\s*Address',
            r'^\s*Currency',
            r'^\s*Nomination',
            r'^\s*Date\s+Narration',
            r'^\s*Date\s+Particulars',
            r'^\s*Date\s+Description',
            r'^\s*Transaction\s+Date\s+Value',
            r'^\s*Sr\.?\s*No',
            r'^\s*Sl\.?\s*No',
            r'^\s*Opening\s+Balance',
            r'^\s*Closing\s+Balance',
            r'^\s*Page\s+\d+',
            r'^\s*Page\s+Total',
            r'^\s*Grand\s+Total',
            r'^\s*This is a (system|computer)',
            r'^\s*Electronically\s+Generated',
            r'^\s*-{5,}',
            r'^\s*={5,}',
            r'^\s*Registered\s+Office',
            r'^\s*Corporate\s+Office',
            r'^\s*Disclaimer',
            r'^\s*Note:',
            r'^\s*\*+\s*(End|This)',
            r'^\s*Total\s*$',
            r'^\s*Withdrawal\s+Amt',
        ]

        # Transaction date patterns
        txn_date_slash = re.compile(r'^\s*(\d{2}/\d{2}/\d{4})\s+(.*)')
        txn_date_dash = re.compile(r'^\s*(\d{2}-\d{2}-\d{4})\s+(.*)')

        # Amount patterns at end of line
        amounts_3 = re.compile(
            r'([\d,]+\.\d{2})\s+([\d,]+\.\d{2})\s+([\d,]+\.\d{2})\s*$'
        )
        amounts_2 = re.compile(
            r'([\d,]+\.\d{2})\s+([\d,]+\.\d{2})\s*$'
        )

        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue

            # Skip header/footer
            skip = False
            for pat in skip_patterns:
                if re.match(pat, stripped, re.IGNORECASE):
                    skip = True
                    break
            if skip:
                continue

            # Try matching transaction line
            m = txn_date_slash.match(stripped) or txn_date_dash.match(stripped)
            if m:
                if current_txn:
                    transactions.append(current_txn)

                date_str = m.group(1)
                rest = m.group(2).strip()

                withdrawal = ''
                deposit = ''
                balance = ''
                chq_ref = ''
                narration = rest

                # Check for value date at start of rest
                val_date_m = re.match(r'(\d{2}[/-]\d{2}[/-]\d{4})\s+(.*)', rest)
                if val_date_m:
                    rest = val_date_m.group(2).strip()
                    narration = rest

                am3 = amounts_3.search(rest)
                am2 = amounts_2.search(rest)

                if am3:
                    narration_part = rest[:am3.start()].strip()
                    amt1 = am3.group(1).replace(',', '')
                    amt2 = am3.group(2).replace(',', '')
                    balance = am3.group(3).replace(',', '')

                    # IndusInd: withdrawal, deposit, closing balance
                    if float(amt1) > 0 and float(amt2) == 0:
                        withdrawal = amt1
                    elif float(amt2) > 0 and float(amt1) == 0:
                        deposit = amt2
                    elif float(amt1) > 0:
                        prev_bal = float(transactions[-1]['balance']) if transactions and transactions[-1]['balance'] else 0
                        curr_bal = float(balance)
                        if curr_bal > prev_bal:
                            deposit = amt2
                        else:
                            withdrawal = amt1

                    # Extract ref number from narration
                    ref_m = re.match(r'(.+?)\s+(\S*\d{6,}\S*)\s*$', narration_part)
                    if ref_m:
                        narration = ref_m.group(1).strip()
                        chq_ref = ref_m.group(2).strip()
                    else:
                        ref_m2 = re.match(r'(\S*\d{6,}\S*)\s+(.*)', narration_part)
                        if ref_m2:
                            chq_ref = ref_m2.group(1)
                            narration = ref_m2.group(2).strip()
                        else:
                            narration = narration_part

                elif am2:
                    narration_part = rest[:am2.start()].strip()
                    amount = am2.group(1).replace(',', '')
                    balance = am2.group(2).replace(',', '')

                    prev_bal = float(transactions[-1]['balance']) if transactions and transactions[-1]['balance'] else 0
                    curr_bal = float(balance)

                    if curr_bal > prev_bal:
                        deposit = amount
                    else:
                        withdrawal = amount

                    ref_m = re.match(r'(.+?)\s+(\S*\d{6,}\S*)\s*$', narration_part)
                    if ref_m:
                        narration = ref_m.group(1).strip()
                        chq_ref = ref_m.group(2).strip()
                    else:
                        narration = narration_part

                date_normalized = self._normalize_date(date_str)

                current_txn = {
                    'date': date_normalized,
                    'particulars': narration,
                    'chq_ref': chq_ref,
                    'withdrawal': withdrawal,
                    'deposit': deposit,
                    'balance': balance,
                }
                continue

            # Continuation line
            if current_txn:
                chq_m = re.match(r'^(\d{6,})$', stripped)
                if chq_m and not current_txn['chq_ref']:
                    current_txn['chq_ref'] = chq_m.group(1)
                elif not re.match(r'^\s*Page\s+\d+', stripped, re.IGNORECASE):
                    current_txn['particulars'] += ' ' + stripped

        if current_txn:
            transactions.append(current_txn)

        # Clean up
        for txn in transactions:
            txn['particulars'] = re.sub(r'\s+', ' ', txn['particulars']).strip()

        return {
            'bank_name': self.BANK_NAME,
            'bank_code': self.BANK_CODE,
            'account_info': account_info,
            'period': period,
            'transactions': transactions,
        }

    def _extract_account_info(self, raw_text: str) -> Dict:
        """Extract account information from header."""
        info = {
            'account_number': '',
            'account_holder': '',
            'branch': '',
            'ifsc': '',
            'type': '',
        }

        m = re.search(r'Account\s*(?:No|Number)[:\s]+(\d+)', raw_text, re.IGNORECASE)
        if m:
            info['account_number'] = m.group(1)

        m = re.search(r'(?:Customer\s*Name|Name)[:\s]+(.+?)(?:\n|Account|Branch|CIF|IFSC)', raw_text, re.IGNORECASE)
        if m:
            info['account_holder'] = m.group(1).strip()

        m = re.search(r'IFSC[:\s]+(INDB\w+)', raw_text, re.IGNORECASE)
        if m:
            info['ifsc'] = m.group(1)

        m = re.search(r'Branch\s*(?:Name)?[:\s]+(.+?)(?:\n|IFSC|Address|MICR)', raw_text, re.IGNORECASE)
        if m:
            info['branch'] = m.group(1).strip()

        m = re.search(r'(?:Account\s*Type|Product)[:\s]+(.+?)(?:\n|Currency)', raw_text, re.IGNORECASE)
        if m:
            info['type'] = m.group(1).strip()

        return info

    def _extract_period(self, raw_text: str) -> str:
        """Extract statement period."""
        m = re.search(
            r'(?:From|Period|Statement)[:\s]*([\d/\-]+\d{4})\s+(?:To|to)\s+([\d/\-]+\d{4})',
            raw_text, re.IGNORECASE
        )
        if m:
            return f"{m.group(1)} to {m.group(2)}"
        return 'N/A'

    def _normalize_date(self, date_str: str) -> str:
        """Convert DD/MM/YYYY to DD-MM-YYYY."""
        return date_str.replace('/', '-') if '/' in date_str else date_str
