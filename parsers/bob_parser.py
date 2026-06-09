"""
Bank of Baroda Statement Parser
=================================
Parses Bank of Baroda (BoB) statement PDFs.

Format characteristics:
  - Header: "Bank of Baroda", "BANK OF BARODA"
  - IFSC: BARB0... prefix
  - Account info: Account No, Customer Name, Branch
  - Column Header: Date | Particulars | Chq.No. | Withdrawal | Deposit | Balance
  - Balance may have Dr./Cr. suffix
  - Dates in DD/MM/YYYY or DD-MM-YYYY format
  - Multi-line narrations
  - Page headers repeat on each page
  - "Statement of Account" title
"""

import re
from typing import Dict, List, Optional
from parsers.base_parser import BaseBankParser


class BankOfBarodaParser(BaseBankParser):
    """Parser for Bank of Baroda statements."""

    BANK_CODE = "bob"
    BANK_NAME = "Bank of Baroda"
    DETECTION_KEYWORDS = [
        "BARB0",
        "Bank of Baroda",
        "BANK OF BARODA",
        "BARODA",
        "Statement of Account",
        "bob",
        "bankofbaroda",
    ]
    DETECTION_RULES = [
        (r"\bBARB0\w{6}\b", 10, True),
        ("bankofbaroda.in", 10, False),
        ("BANK OF BARODA", 3, False),
        ("Baroda Connect", 3, False),
        ("Statement of Account", 1, False),
    ]
    NEGATIVE_RULES = [
        (r"\bSBIN0\w{6}\b", -8, True),
        (r"\bUBIN0\w{6}\b", -8, True),
    ]


    def parse(self, raw_text: str) -> Dict:
        """Parse Bank of Baroda statement text into structured data."""
        account_info = self._extract_account_info(raw_text)
        period = self._extract_period(raw_text)

        lines = raw_text.split('\n')
        transactions = []
        current_txn = None

        # Skip patterns
        skip_patterns = [
            r'^\s*Bank of Baroda',
            r'^\s*BANK OF BARODA',
            r'^\s*Statement of Account',
            r'^\s*Account\s*(No|Number|Statement)',
            r'^\s*Customer\s*(Name|ID|Code)',
            r'^\s*Branch\s*(Name|Code|Address)',
            r'^\s*IFSC',
            r'^\s*MICR',
            r'^\s*Address',
            r'^\s*Currency',
            r'^\s*Nomination',
            r'^\s*Date\s+Particulars',
            r'^\s*Date\s+Description',
            r'^\s*Date\s+Narration',
            r'^\s*Date\s+Value\s+Date',
            r'^\s*Opening\s+Balance',
            r'^\s*Closing\s+Balance',
            r'^\s*Page\s+\d+',
            r'^\s*This is a (system|computer)',
            r'^\s*Electronically\s+Generated',
            r'^\s*-{5,}',
            r'^\s*={5,}',
            r'^\s*Registered\s+Office',
            r'^\s*Page\s+Total',
            r'^\s*Grand\s+Total',
            r'^\s*Disclaimer',
            r'^\s*Note:',
            r'^\s*\*+\s*(End|This)',
        ]

        # Transaction date patterns
        txn_date_slash = re.compile(r'^\s*(\d{2}/\d{2}/\d{4})\s+(.*)')
        txn_date_dash = re.compile(r'^\s*(\d{2}-\d{2}-\d{4})\s+(.*)')

        # Amount patterns
        amounts_3 = re.compile(
            r'([\d,]+\.\d{2})\s+([\d,]+\.\d{2})\s+([\d,]+\.\d{2})\s*(Dr\.?|Cr\.?)?\s*$'
        )
        amounts_2 = re.compile(
            r'([\d,]+\.\d{2})\s+([\d,]+\.\d{2})\s*(Dr\.?|Cr\.?)?\s*$'
        )
        amounts_1_bal = re.compile(
            r'([\d,]+\.\d{2})\s*(Dr\.?|Cr\.?)?\s*$'
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
                balance_suffix = ''
                chq_ref = ''
                narration = rest

                am3 = amounts_3.search(rest)
                am2 = amounts_2.search(rest)

                if am3:
                    narration_part = rest[:am3.start()].strip()
                    amt1 = am3.group(1).replace(',', '')
                    amt2 = am3.group(2).replace(',', '')
                    balance = am3.group(3).replace(',', '')
                    balance_suffix = am3.group(4) or ''

                    # Determine W/D: amt1=withdrawal, amt2=deposit typically
                    # If one is 0.00, the other is the transaction amount
                    if float(amt1) > 0 and float(amt2) == 0:
                        withdrawal = amt1
                    elif float(amt2) > 0 and float(amt1) == 0:
                        deposit = amt2
                    else:
                        withdrawal = amt1
                        deposit = amt2

                    # Extract chq ref
                    ref_m = re.match(r'(\S+)\s+(.*)', narration_part)
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

                    prev_bal = float(transactions[-1]['balance'].replace('Cr', '').replace('Dr', '').replace('.', '', 1).replace(',', '').strip() if transactions and transactions[-1]['balance'] else '0') if transactions else 0
                    # Re-parse properly
                    try:
                        prev_bal_str = transactions[-1]['balance'] if transactions else '0'
                        prev_bal = self._parse_bal_numeric(prev_bal_str)
                    except:
                        prev_bal = 0

                    curr_bal = float(balance)
                    if balance_suffix and 'Dr' in balance_suffix:
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

                # Build balance string
                bal_str = balance
                if balance_suffix:
                    bal_str = f"{balance}{balance_suffix}"

                date_normalized = self._normalize_date(date_str)

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
                if not re.match(r'^\s*Page\s+\d+', stripped, re.IGNORECASE):
                    # Check if it's a cheque number on its own line
                    chq_m = re.match(r'^(\d{6,})$', stripped)
                    if chq_m and not current_txn['chq_ref']:
                        current_txn['chq_ref'] = chq_m.group(1)
                    else:
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

    def _parse_bal_numeric(self, bal_str: str) -> float:
        """Parse balance string to numeric value."""
        if not bal_str:
            return 0.0
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

        m = re.search(r'Customer\s*Name[:\s]+(.+?)(?:\n|Account|Branch|IFSC)', raw_text, re.IGNORECASE)
        if m:
            info['account_holder'] = m.group(1).strip()

        m = re.search(r'IFSC[:\s]+(BARB\w+)', raw_text, re.IGNORECASE)
        if m:
            info['ifsc'] = m.group(1)

        m = re.search(r'Branch\s*(?:Name)?[:\s]+(.+?)(?:\n|IFSC|MICR|Address)', raw_text, re.IGNORECASE)
        if m:
            info['branch'] = m.group(1).strip()

        m = re.search(r'(?:Account\s*Type|Product)[:\s]+(.+?)(?:\n|Currency)', raw_text, re.IGNORECASE)
        if m:
            info['type'] = m.group(1).strip()

        return info

    def _extract_period(self, raw_text: str) -> str:
        """Extract statement period."""
        m = re.search(
            r'(?:From|Period|Statement\s+for)[:\s]*([\d/\-]+\d{4})\s+(?:To|to)\s+([\d/\-]+\d{4})',
            raw_text, re.IGNORECASE
        )
        if m:
            return f"{m.group(1)} to {m.group(2)}"
        return 'N/A'

    def _normalize_date(self, date_str: str) -> str:
        """Convert DD/MM/YYYY to DD-MM-YYYY."""
        return date_str.replace('/', '-') if '/' in date_str else date_str
