"""
Equitas Small Finance Bank Statement Parser
=============================================
Parses Equitas SFB statement PDFs.

Format characteristics:
  - Header: "Account Statement" + datetime
  - Customer Info / Branch Info side by side
  - Statement period: "Statement for the Period from DD-Mon-YYYY to DD-Mon-YYYY"
  - Column Header: Date | Reference No. / Cheque No. | Narration | Withdrawal INR | Deposit INR | ClosingBalance INR
  - Multi-line narrations
  - "*** End of the Statement ***" marker
  - Footer with bank address and phone numbers
"""

import re
from typing import Dict, List, Optional
from parsers.base_parser import BaseBankParser


class EquitasParser(BaseBankParser):
    """Parser for Equitas Small Finance Bank statements."""

    BANK_CODE = "equitas_sfb"
    BANK_NAME = "Equitas Small Finance Bank"
    DETECTION_KEYWORDS = [
        "Equitas",
        "ESFB0",
        "Small Finance Bank",
        "equitasbank.com",
        "Business Prime Current",
    ]
    DETECTION_RULES = [
        (r"\bESFB0\w{6}\b", 10, True),
        ("equitasbank.com", 10, False),
        ("Equitas Small Finance Bank", 3, False),
        ("Business Prime Current", 3, False),
        ("Equitas", 1, False),
    ]


    def parse(self, raw_text: str) -> Dict:
        """Parse Equitas SFB statement text into structured data."""
        account_info = self._extract_account_info(raw_text)
        period = self._extract_period(raw_text)

        lines = raw_text.split('\n')
        transactions = []
        current_txn = None
        in_transactions = False

        # Skip patterns
        skip_patterns = [
            r'^Account Statement',
            r'^Customer Information',
            r'^Customer Name',
            r'^Nominee Name',
            r'^Mobile',
            r'^Email',
            r'^Product Name',
            r'^Address',
            r'^MULBAGAL',
            r'^KARNATAKA',
            r'^KOLAR',
            r'^Joint Holder',
            r'^NA$',
            r'^NOTE:',
            r'^holder names',
            r'^Statement for the Period',
            r'^Date\s+Reference',
            r'^INR\s+INR',
            r'^\*{3}\s+End',
            r'^Page\s+\d+',
            r'^Equitas Small Finance',
            r'^4th Floor',
            r'^T:\s+\+91',
            r'^Toll Free',
            r'^Branch/Account',
            r'^Branch Name',
            r'^Branch Address',
            r'^Available Balance',
        ]

        # Transaction pattern: DD-Mon-YYYY Reference Narration Amounts
        txn_pattern = re.compile(
            r'^(\d{2}-\w{3}-\d{4})\s+(.+)'
        )

        # Amount at end of line
        amount_pattern = re.compile(
            r'([\d,]+\.\d{2})\s+([\d,]+\.\d{2})\s*$'  # withdrawal/deposit + balance
        )
        single_amount_pattern = re.compile(
            r'([\d,]+\.\d{2})\s*$'
        )

        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue

            # Check end of statement
            if '*** End of the Statement ***' in stripped:
                break

            # Skip headers
            skip = False
            for pat in skip_patterns:
                if re.match(pat, stripped, re.IGNORECASE):
                    skip = True
                    break
            if skip:
                continue

            # Try matching transaction
            m = txn_pattern.match(stripped)
            if m:
                if current_txn:
                    transactions.append(current_txn)

                date_str = m.group(1)
                rest = m.group(2).strip()

                # Try to find amounts at end
                withdrawal = ''
                deposit = ''
                balance = ''
                ref_no = ''
                narration = rest

                am2 = amount_pattern.search(rest)
                am1 = single_amount_pattern.search(rest)

                if am2:
                    narration_part = rest[:am2.start()].strip()
                    amount1 = am2.group(1).replace(',', '')
                    amount2 = am2.group(2).replace(',', '')
                    
                    # Check if there's a 3rd amount (withdrawal + deposit + balance)
                    # Usually for Equitas it's: withdrawal balance OR deposit balance
                    # Determine by checking if previous balance exists
                    prev_bal = float(transactions[-1]['balance']) if transactions and transactions[-1]['balance'] else 0
                    curr_bal = float(amount2)
                    
                    if curr_bal < prev_bal:
                        withdrawal = amount1
                    else:
                        deposit = amount1
                    balance = amount2
                    
                    # Extract ref number from narration part
                    ref_m = re.match(r'(\S+)\s+(.*)', narration_part)
                    if ref_m:
                        ref_no = ref_m.group(1)
                        narration = ref_m.group(2).strip()
                    else:
                        narration = narration_part
                elif am1:
                    narration_part = rest[:am1.start()].strip()
                    balance = am1.group(1).replace(',', '')
                    narration = narration_part

                # Normalize date
                date_normalized = self._normalize_date(date_str)

                current_txn = {
                    'date': date_normalized,
                    'particulars': narration,
                    'chq_ref': ref_no,
                    'withdrawal': withdrawal,
                    'deposit': deposit,
                    'balance': balance,
                }
                continue

            # Continuation line
            if current_txn:
                current_txn['particulars'] += ' ' + stripped

        if current_txn:
            transactions.append(current_txn)

        # Cleanup
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
        info = {
            'account_number': '',
            'account_holder': '',
            'branch': '',
            'ifsc': '',
            'type': '',
        }

        m = re.search(r'Account\s+Number\s+(\d+)', raw_text)
        if m:
            info['account_number'] = m.group(1)

        m = re.search(r'Customer\s+Name\s+(.+?)(?:\s+Branch)', raw_text)
        if m:
            info['account_holder'] = m.group(1).strip()

        m = re.search(r'IFSC\s+Code\s+(\w+)', raw_text)
        if m:
            info['ifsc'] = m.group(1)

        m = re.search(r'Branch\s+Name\s+(.+)', raw_text)
        if m:
            info['branch'] = m.group(1).strip()

        m = re.search(r'Account\s+Type\s+(\w+)', raw_text)
        if m:
            info['type'] = m.group(1)

        return info

    def _extract_period(self, raw_text: str) -> str:
        m = re.search(
            r'Period\s+from\s+(\d{2}-\w{3}-\d{4})\s+to\s+(\d{2}-\w{3}-\d{4})',
            raw_text
        )
        if m:
            return f"{self._normalize_date(m.group(1))} to {self._normalize_date(m.group(2))}"
        return 'N/A'

    def _normalize_date(self, date_str: str) -> str:
        """Convert DD-Mon-YYYY to DD-MM-YYYY."""
        months = {
            'Jan': '01', 'Feb': '02', 'Mar': '03', 'Apr': '04',
            'May': '05', 'Jun': '06', 'Jul': '07', 'Aug': '08',
            'Sep': '09', 'Oct': '10', 'Nov': '11', 'Dec': '12',
        }
        m = re.match(r'(\d{2})-(\w{3})-(\d{4})', date_str)
        if m:
            day = m.group(1)
            month = months.get(m.group(2), m.group(2))
            year = m.group(3)
            return f"{day}-{month}-{year}"
        return date_str
