"""
Kotak Mahindra Bank Statement Parser
======================================
Parses Kotak Mahindra Bank statement PDFs.

Format characteristics:
  - Header: "Account Statement" (plain)
  - Account holder name, address, Cust. Reln. No., Account No.
  - Period: "Period From DD/MM/YYYY To DD/MM/YYYY"
  - Column Header: Sl. No. | Date | Description | Chq / Ref number | Amount | Dr / Cr | Balance | Dr / Cr
  - UNUSUAL: Description comes BEFORE Date on the same line, or Date and Sl No are on separate lines
  - Multi-line descriptions
  - Opening/Closing balance lines at bottom
  - Footer with phone number and address
"""

import re
from typing import Dict, List, Optional
from parsers.base_parser import BaseBankParser


class KotakParser(BaseBankParser):
    """Parser for Kotak Mahindra Bank statements."""

    BANK_CODE = "kotak_bank"
    BANK_NAME = "Kotak Mahindra Bank"
    DETECTION_KEYWORDS = [
        "Kotak Mahindra",
        "KKBK",
        "Cust. Reln. No.",
        "Dr / Cr",
        "Sl. No.",
        "1860 266 2666",
    ]
    DETECTION_RULES = [
        (r"\bKKBK0\w{6}\b", 10, True),
        ("kotak.com", 10, False),
        ("Kotak Mahindra", 3, False),
        ("Cust. Reln. No.", 3, False),
        ("1860 266 2666", 3, False),
        ("Dr / Cr", 1, False),
        ("Sl. No.", 1, False),
    ]


    def parse(self, raw_text: str) -> Dict:
        """Parse Kotak Bank statement text into structured data."""
        account_info = self._extract_account_info(raw_text)
        period = self._extract_period(raw_text)

        lines = raw_text.split('\n')
        transactions = []
        current_txn = None

        # Skip patterns
        skip_patterns = [
            r'^Account Statement',
            r'^Sl\.\s*No\.',
            r'^Opening balance',
            r'^Closing balance',
            r'^You may call',
            r'^Write to us',
            r'^Post Box',
        ]

        # Kotak format is tricky - dates and amounts are on diff lines
        # Pattern: Description RefNo Amount DR/CR Balance DR/CR
        # followed by: Sl.No DD/MM/YYYY
        # or: Sl.No  DD/MM/YYYY continuation...

        # Look for lines with Sl No + Date
        sl_date_pattern = re.compile(r'^\s*(\d+)\s+(\d{2}/\d{2}/\d{4})\s*$')
        
        # Look for amount lines: description ref_no amount DR/CR balance DR/CR
        amount_cr_dr_pattern = re.compile(
            r'(.+?)\s+'                    # description
            r'(\S+-\S+|\S+)\s+'            # ref number (e.g. NEFTINW-0814871191)
            r'([\d,]+\.\d{2})\s+'          # amount
            r'(CR|DR)\s+'                  # type
            r'([\d,]+\.\d{2})\s+'          # balance
            r'(CR|DR)\s*$'                 # balance type
        )

        i = 0
        while i < len(lines):
            stripped = lines[i].strip()
            i += 1
            
            if not stripped:
                continue

            # Skip headers/footers
            skip = False
            for pat in skip_patterns:
                if re.match(pat, stripped, re.IGNORECASE):
                    skip = True
                    break
            if skip:
                continue

            # Skip customer info lines
            if any(kw in stripped for kw in [
                'Cust. Reln.', 'Account No.', 'Period From',
                'Currency', 'Branch', 'Nomination', 'Nominee',
                'KARNATAKA', 'INDIA', 'Bangalore'
            ]):
                continue

            # Try matching amount line
            am = amount_cr_dr_pattern.match(stripped)
            if am:
                description = am.group(1).strip()
                ref_no = am.group(2).strip()
                amount = am.group(3).replace(',', '')
                txn_type = am.group(4)
                balance = am.group(5).replace(',', '')
                bal_type = am.group(6)

                withdrawal = amount if txn_type == 'DR' else ''
                deposit = amount if txn_type == 'CR' else ''

                # Look for the Sl No + Date on the next line
                date_str = ''
                if i < len(lines):
                    next_stripped = lines[i].strip()
                    sl_m = sl_date_pattern.match(next_stripped)
                    if sl_m:
                        date_str = sl_m.group(2)
                        i += 1  # consume that line

                # Save previous transaction
                if current_txn:
                    transactions.append(current_txn)

                current_txn = {
                    'date': self._normalize_date(date_str) if date_str else '',
                    'particulars': description,
                    'chq_ref': ref_no,
                    'withdrawal': withdrawal,
                    'deposit': deposit,
                    'balance': f"{balance} {bal_type}",
                }
                continue

            # If this is a Sl No + Date line following a partial transaction
            sl_m = sl_date_pattern.match(stripped)
            if sl_m and current_txn and not current_txn['date']:
                current_txn['date'] = self._normalize_date(sl_m.group(2))
                continue

            # Continuation line for current transaction's description
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

        # Account number
        m = re.search(r'Account\s+No\.\s+(\d+)', raw_text)
        if m:
            info['account_number'] = m.group(1)

        # Account holder - line 2 of the statement
        lines = raw_text.split('\n')
        if len(lines) > 1:
            info['account_holder'] = lines[1].strip()

        # Branch
        m = re.search(r'Branch\s+(\w+)', raw_text)
        if m:
            info['branch'] = m.group(1)

        # Cust Reln No
        m = re.search(r'Cust\.\s+Reln\.\s+No\.\s+(\d+)', raw_text)
        if m:
            info['type'] = f"Cust ID: {m.group(1)}"

        return info

    def _extract_period(self, raw_text: str) -> str:
        m = re.search(
            r'Period\s+From\s+(\d{2}/\d{2}/\d{4})\s+To\s+(\d{2}/\d{2}/\d{4})',
            raw_text
        )
        if m:
            return f"{m.group(1)} to {m.group(2)}"
        return 'N/A'

    def _normalize_date(self, date_str: str) -> str:
        """Convert DD/MM/YYYY to DD-MM-YYYY."""
        return date_str.replace('/', '-') if date_str else ''
