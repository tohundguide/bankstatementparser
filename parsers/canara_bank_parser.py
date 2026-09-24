"""
Canara Bank Statement Parser
===============================
Parses Canara Bank statement PDFs.

Format characteristics:
  - Header: "CANARA BANK", "Canara Bank"
  - IFSC: CNRB0... prefix
  - Account info: Account No, Customer Name, Branch
  - Column Header variations:
    1) Transaction Date | Value Date | Description | Ref No./Cheque No. | Debit | Credit | Balance
    2) Date | Particulars | Chq No | Withdrawal | Deposit | Balance
  - Dates in DD/MM/YYYY, DD-MM-YYYY, or DD-MMM-YYYY format
  - Multi-line narrations
  - Balance may have Dr/Cr suffix
  - Page headers repeat on each page
"""

import re
from typing import Dict, List, Optional
from parsers.base_parser import BaseBankParser


class CanaraBankParser(BaseBankParser):
    """Parser for Canara Bank statements."""

    BANK_CODE = "canara_bank"
    BANK_NAME = "Canara Bank"
    DETECTION_KEYWORDS = [
        "CNRB0",
        "CANARA BANK",
        "Canara Bank",
        "canara",
        "Statement of Account",
        "Syndicate Bank",  # Merged with Canara
    ]
    DETECTION_RULES = [
        (r"\bCNRB0\w{6}\b", 10, True),
        ("canarabank.com", 10, False),
        ("CANARA BANK", 3, False),
        ("Syndicate Bank", 3, False),
        ("Statement of Account", 1, False),
    ]


    def parse(self, raw_text: str) -> Dict:
        """Parse Canara Bank statement text into structured data."""
        account_info = self._extract_account_info(raw_text)
        period = self._extract_period(raw_text)

        lines = raw_text.split('\n')
        transactions = []
        current_txn = None

        # Skip patterns
        skip_patterns = [
            r'^\s*CANARA BANK',
            r'^\s*Canara Bank',
            r'^\s*Statement of Account',
            r'^\s*Account\s*(No|Number|Statement)',
            r'^\s*Customer\s*(Name|ID|Code|Id)',
            r'^\s*Branch\s*(Name|Code|Address)',
            r'^\s*IFSC',
            r'^\s*MICR',
            r'^\s*Address',
            r'^\s*Currency',
            r'^\s*Nomination',
            r'^\s*Transaction\s+Date\s+Value',
            r'^\s*Date\s+Particulars',
            r'^\s*Date\s+Description',
            r'^\s*Date\s+Narration',
            r'^\s*Sl\.?\s*No',
            r'^\s*Sr\.?\s*No',
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
            r'^\s*Head\s+Office',
            r'^\s*Disclaimer',
            r'^\s*Note:',
            r'^\s*\*+\s*(End|This)',
            r'^\s*Total\s*$',
        ]

        # Transaction date patterns
        txn_date_slash = re.compile(r'^\s*(\d{2}/\d{2}/\d{4})\s+(.*)')
        txn_date_dash = re.compile(r'^\s*(\d{2}-\d{2}-\d{4})\s+(.*)')
        # DD-MON-YY too: the branch "STATEMENT OF ACCOUNT" prints "01-APR-25".
        # With only 4-digit years accepted, no real row matched and wrapped
        # UPI timestamps ("09/09/2025 12:09:34") became the only "rows".
        txn_date_mon = re.compile(r'^\s*(\d{2}-[A-Za-z]{3}-\d{2}(?:\d{2})?)\s+(.*)')
        time_re = re.compile(r'^\d{1,2}:\d{2}')

        # Layout with a BRANCH column before REF/CHQ.NO:
        #   TRANS DATE | VALUE DATE | BRANCH | REF/CHQ.NO | DESCRIPTION | WITHDRAWS | DEPOSIT | BALANCE
        has_branch_col = bool(re.search(r'BRANCH\s+REF/CHQ', raw_text, re.IGNORECASE))
        page_header_re = re.compile(r'^\s*(TRANS\s+VALUE\s+BRANCH\b|DATE\s+DATE\s*$)', re.IGNORECASE)
        stop_re = re.compile(r'^\s*Statement\s+Summary\b', re.IGNORECASE)
        # A bare page number sits right before each repeated column header.
        # (Bare numbers elsewhere are narration: a UPI time wraps as "12:39:" + "57".)
        lines = [l for i, l in enumerate(lines)
                 if not (re.fullmatch(r'\s*\d{1,3}\s*', l)
                         and i + 1 < len(lines) and page_header_re.match(lines[i + 1]))]
        opening_seen = False

        # Amount patterns at end of line
        amounts_3 = re.compile(
            r'([\d,]+\.\d{2})\s+([\d,]+\.\d{2})\s+([\d,]+\.\d{2})\s*(Dr\.?|Cr\.?)?\s*$'
        )
        amounts_2 = re.compile(
            r'([\d,]+\.\d{2})\s+([\d,]+\.\d{2})\s*(Dr\.?|Cr\.?)?\s*$'
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

            if stop_re.match(stripped):
                break
            if page_header_re.match(stripped):
                continue

            # Try matching transaction line (all date formats)
            m = txn_date_mon.match(stripped) or txn_date_slash.match(stripped) or txn_date_dash.match(stripped)
            if m and time_re.match(m.group(2).strip()):
                m = None   # "27/06/2025 19:04:47" inside a UPI narration, not a row
            if m:
                if current_txn:
                    transactions.append(current_txn)

                date_str = m.group(1)
                rest = m.group(2).strip()

                withdrawal = ''
                deposit = ''
                balance = ''
                balance_suffix = ''
                chq_ref = ''
                narration = rest

                # Check for a second date (value date) at start of rest
                val_date_m = re.match(r'(\d{2}[/-]\d{2}[/-]\d{4}|\d{2}-[A-Za-z]{3}-\d{2}(?:\d{2})?)\s+(.*)', rest)
                if val_date_m:
                    rest = val_date_m.group(2).strip()
                    narration = rest
                branch_code = ''
                if has_branch_col:
                    bm = re.match(r'(\d{1,5})\s+(.*)', rest)
                    if bm:
                        branch_code, rest = bm.group(1), bm.group(2).strip()
                        narration = rest

                am3 = amounts_3.search(rest)
                am2 = amounts_2.search(rest)

                if am3:
                    narration_part = rest[:am3.start()].strip()
                    amt1 = am3.group(1).replace(',', '')
                    amt2 = am3.group(2).replace(',', '')
                    balance = am3.group(3).replace(',', '')
                    balance_suffix = am3.group(4) or ''

                    if float(amt1) > 0 and float(amt2) == 0:
                        withdrawal = amt1
                    elif float(amt2) > 0 and float(amt1) == 0:
                        deposit = amt2
                    elif float(amt1) > 0:
                        prev_bal = self._get_prev_balance(transactions)
                        curr_bal = float(balance)
                        if 'Dr' in balance_suffix:
                            curr_bal = -curr_bal
                        if curr_bal > prev_bal:
                            deposit = amt2
                        else:
                            withdrawal = amt1

                    # Extract ref number
                    ref_m = re.match(r'(\S+)\s+(.*)', narration_part)
                    if has_branch_col:
                        ref_m = re.match(r'(\d{6,})\s+(.*)', narration_part)
                    if ref_m and re.search(r'\d{4,}', ref_m.group(1)):
                        chq_ref = ref_m.group(1)
                        narration = ref_m.group(2).strip()
                    else:
                        narration = narration_part

                elif am2:
                    narration_part = rest[:am2.start()].strip()
                    amount = am2.group(1).replace(',', '')
                    balance = am2.group(2).replace(',', '')
                    balance_suffix = am2.group(3) or ''

                    prev_bal = self._get_prev_balance(transactions)
                    curr_bal = float(balance)
                    if 'Dr' in balance_suffix:
                        curr_bal = -curr_bal

                    if curr_bal > prev_bal:
                        deposit = amount
                    else:
                        withdrawal = amount

                    ref_m = re.match(r'(\S+)\s+(.*)', narration_part)
                    if ref_m and re.search(r'\d{4,}', ref_m.group(1)):
                        chq_ref = ref_m.group(1)
                        narration = ref_m.group(2).strip()
                    else:
                        narration = narration_part

                bal_str = f"{balance}{balance_suffix}" if balance_suffix else balance
                date_normalized = self._normalize_date(date_str)

                if re.match(r'^B/F\b', narration) and not opening_seen and not transactions and not current_txn:
                    # Brought-forward balance: seeds the running balance, not a deposit.
                    opening_seen = True
                    current_txn = None
                    transactions.append({'date': date_normalized, 'particulars': '__BF__', 'chq_ref': '',
                                         'withdrawal': '', 'deposit': '', 'balance': bal_str})
                    continue

                current_txn = {
                    'date': date_normalized,
                    'particulars': narration,
                    'chq_ref': chq_ref,
                    'withdrawal': withdrawal,
                    'deposit': deposit,
                    'balance': bal_str,
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

        transactions = [t for t in transactions if t['particulars'] != '__BF__']

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

    def _get_prev_balance(self, transactions: List[Dict]) -> float:
        """Get the previous transaction's balance as a numeric value."""
        if not transactions or not transactions[-1].get('balance'):
            return 0.0
        bal_str = transactions[-1]['balance']
        s = bal_str.strip()
        is_dr = False
        for suffix in ['Dr.', 'DR.', 'Dr', 'DR']:
            if s.endswith(suffix):
                is_dr = True
                s = s[:-len(suffix)].strip()
                break
        else:
            for suffix in ['Cr.', 'CR.', 'Cr', 'CR']:
                if s.endswith(suffix):
                    s = s[:-len(suffix)].strip()
                    break
        s = s.replace(',', '')
        try:
            val = float(s)
            return -val if is_dr else val
        except (ValueError, TypeError):
            return 0.0

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

        # "Customer Name :" first — a bare "Name" also matches "Product Name : CURRENT ACCOUNT".
        m = (re.search(r'Customer\s*Name\s*:\s*(.+?)\s*$', raw_text, re.IGNORECASE | re.MULTILINE)
             or re.search(r'Account\s*Title\s*:\s*(.+?)\s*$', raw_text, re.IGNORECASE | re.MULTILINE)
             or re.search(r'(?:Customer\s*Name|Name)[:\s]+(.+?)(?:\n|Account|Branch|IFSC)', raw_text, re.IGNORECASE))
        if m:
            info['account_holder'] = m.group(1).strip()

        m = re.search(r'IFSC[:\s]+(CNRB\w+)', raw_text, re.IGNORECASE)
        if m:
            info['ifsc'] = m.group(1)

        m = re.search(r'Branch\s*(?:Name)?[:\s]+(.+?)(?:\n|IFSC|MICR|Address)', raw_text, re.IGNORECASE)
        if m:
            info['branch'] = m.group(1).strip()

        m = re.search(r'(?:Account\s*Type|Product(?:\s*Name)?)\s*[:\s]+(.+?)(?:\n|Currency)', raw_text, re.IGNORECASE)
        if m:
            info['type'] = m.group(1).strip()

        return info

    def _extract_period(self, raw_text: str) -> str:
        """Extract statement period."""
        m = re.search(
            r'(?:From|Period|Statement\s+for)[:\s]*([\d/\-\w]+\d{4})\s+(?:To|to)\s+([\d/\-\w]+\d{4})',
            raw_text, re.IGNORECASE
        )
        if m:
            return f"{m.group(1)} to {m.group(2)}"
        return 'N/A'

    def _normalize_date(self, date_str: str) -> str:
        """Convert various date formats to DD-MM-YYYY."""
        months = {
            'Jan': '01', 'Feb': '02', 'Mar': '03', 'Apr': '04',
            'May': '05', 'Jun': '06', 'Jul': '07', 'Aug': '08',
            'Sep': '09', 'Oct': '10', 'Nov': '11', 'Dec': '12',
        }

        # DD-Mon-YYYY or DD-MMM-YYYY
        m = re.match(r'(\d{2})-([A-Za-z]{3})-(\d{2}(?:\d{2})?)$', date_str)
        if m:
            day = m.group(1)
            month = months.get(m.group(2).title(), m.group(2))
            year = m.group(3) if len(m.group(3)) == 4 else '20' + m.group(3)
            return f"{day}-{month}-{year}"

        # DD/MM/YYYY -> DD-MM-YYYY
        return date_str.replace('/', '-') if '/' in date_str else date_str
