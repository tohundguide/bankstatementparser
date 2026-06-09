"""
Punjab National Bank (PNB) Statement Parser
=============================================
Parses PNB statement PDFs.

Format characteristics:
  - Header: "Statement of Account No: XXXX"
  - Customer Name, Address, Branch, IFSC, MICR info in header
  - Column Header: Date | Withdrawal | Deposit | Balance | Alpha | CHQ. NO. | Narration
  - Peculiar layout: amounts come BEFORE description
  - Multi-line narrations (description wraps to next line without date)
  - Page totals at bottom of each page
  - "Page X of Y" markers
  - Balance has "Cr." or "Dr." suffix
  - Some transactions have cheque numbers in "Alpha CHQ. NO." columns
"""

import re
from typing import Dict, List, Optional
from parsers.base_parser import BaseBankParser


class PNBParser(BaseBankParser):
    """Parser for Punjab National Bank statements."""

    BANK_CODE = "pnb"
    BANK_NAME = "Punjab National Bank"
    DETECTION_KEYWORDS = [
        "PUNB0",
        "Punjab National",
        "Statement of Account No:",
        "Alpha",
        "CHQ. NO.",
        "Cash Withdrawal At Br",
        "Cash Deposit At",
        "Electronically Generated Statement",
    ]
    DETECTION_RULES = [
        (r"\bPUNB0\w{6}\b", 10, True),
        ("pnbindia.in", 10, False),
        ("Punjab National Bank", 3, False),
        ("Electronically Generated Statement", 3, False),
        ("Cash Withdrawal At Br", 1, False),
        ("Cash Deposit At", 1, False),
        ("CHQ. NO.", 1, False),
    ]


    def parse(self, raw_text: str) -> Dict:
        """Parse PNB statement text into structured data."""
        account_info = self._extract_account_info(raw_text)
        period = self._extract_period(raw_text)

        lines = raw_text.split('\n')
        transactions = []
        current_txn = None

        # Skip patterns
        skip_patterns = [
            r'^Statement\s+of\s+Account',
            r'^Printed\s+By',
            r'^DATE:',
            r'^Customer\s+Name',
            r'^CKYC',
            r'^Customer\s+Address',
            r'^Branch\s+Address',
            r'^Branch\s+Contact',
            r'^Customer\s+Care',
            r'^IFSC\s+Code',
            r'^Acct\s+Currency',
            r'^Statement\s+for\s+Period',
            r'^Date\s+Withdrawal\s+Deposit',
            r'^Page\s+Total',
            r'^Page\s+\d+\s+of',
            r'^Grand\s+\d',
            r'^Disclaimer',
            r'^JAMMU',
            r'^BARAMULA',
            r'^SOPORE',
        ]

        # Transaction pattern: DD-MM-YYYY followed by amounts
        # PNB format: Date Withdrawal Deposit Balance Alpha CHQ.NO. Narration
        # The amounts are right after the date, then balance, then optional cheque, then narration
        txn_pattern = re.compile(
            r'^(\d{2}-\d{2}-\d{4})\s+'
            r'([\d,.]+)\s+'         # First amount (withdrawal or deposit)
            r'([\d,.]+)\s+'         # Second amount (balance)
            r'(Cr\.|Dr\.)\s*'       # Balance type
            r'(.*)?$'               # Optional cheque info + narration
        )

        # Alternative: Date with withdrawal AND deposit
        txn_pattern_3amt = re.compile(
            r'^(\d{2}-\d{2}-\d{4})\s+'
            r'([\d,.]+)\s+'         # Amount 1
            r'([\d,.]+)\s+'         # Amount 2
            r'([\d,.]+)\s+'         # Amount 3 (balance)
            r'(Cr\.|Dr\.)\s*'
            r'(.*)?$'
        )

        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue

            # Skip headers
            skip = False
            for pat in skip_patterns:
                if re.match(pat, stripped, re.IGNORECASE):
                    skip = True
                    break
            if skip:
                continue

            # Try matching a transaction (with 3 amounts: withdrawal, deposit, balance)
            # This is rare - most have either withdrawal or deposit
            m3 = txn_pattern_3amt.match(stripped)
            m2 = txn_pattern.match(stripped)

            if m3:
                if current_txn:
                    transactions.append(current_txn)

                date_str = m3.group(1)
                amt1 = m3.group(2).replace(',', '')
                amt2 = m3.group(3).replace(',', '')
                balance = m3.group(4).replace(',', '')
                balance_type = m3.group(5)
                rest = (m3.group(6) or '').strip()

                # Determine which is withdrawal/deposit
                prev_bal = float(transactions[-1]['balance'].replace('Cr', '').replace('Dr', '').strip()) if transactions and transactions[-1]['balance'] else 0
                curr_bal = float(balance)

                # Parse cheque number from rest
                chq_ref = ''
                narration = rest
                chq_m = re.match(r'(UNI|ILQ)?\s*(\d{5,})\s*(.*)', rest)
                if chq_m:
                    chq_ref = (chq_m.group(1) or '') + ' ' + chq_m.group(2)
                    chq_ref = chq_ref.strip()
                    narration = chq_m.group(3).strip()

                current_txn = {
                    'date': date_str,
                    'particulars': narration,
                    'chq_ref': chq_ref,
                    'withdrawal': amt1,
                    'deposit': amt2,
                    'balance': f"{balance}{balance_type}",
                }
                continue

            elif m2:
                if current_txn:
                    transactions.append(current_txn)

                date_str = m2.group(1)
                amount = m2.group(2).replace(',', '')
                balance = m2.group(3).replace(',', '')
                balance_type = m2.group(4)
                rest = (m2.group(5) or '').strip()

                # Determine withdrawal vs deposit by comparing balances
                prev_bal = float(transactions[-1]['balance'].replace('Cr.', '').replace('Dr.', '').strip()) if transactions and transactions[-1]['balance'] else 0
                curr_bal = float(balance)

                withdrawal = ''
                deposit = ''
                if curr_bal < prev_bal:
                    withdrawal = amount
                else:
                    deposit = amount

                # Parse cheque number from rest
                chq_ref = ''
                narration = rest
                chq_m = re.match(r'(?:(UNI|ILQ)\s+)?(\d{5,})\s*(.*)', rest)
                if chq_m:
                    chq_ref = ((chq_m.group(1) or '') + ' ' + chq_m.group(2)).strip()
                    narration = chq_m.group(3).strip()

                current_txn = {
                    'date': date_str,
                    'particulars': narration,
                    'chq_ref': chq_ref,
                    'withdrawal': withdrawal,
                    'deposit': deposit,
                    'balance': f"{balance}{balance_type}",
                }
                continue

            # Continuation line
            if current_txn:
                # Skip page-related lines
                if re.match(r'^:?\d{13,}', stripped):
                    # Reference number on separate line
                    if not current_txn['chq_ref']:
                        current_txn['chq_ref'] = stripped.lstrip(':')
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

    def _extract_account_info(self, raw_text: str) -> Dict:
        info = {
            'account_number': '',
            'account_holder': '',
            'branch': '',
            'ifsc': '',
            'type': '',
        }

        m = re.search(r'Statement\s+of\s+Account\s+No[:\s]+(\d+)', raw_text)
        if m:
            info['account_number'] = m.group(1)

        m = re.search(r'Customer\s+Name[:\s]+(.+)', raw_text)
        if m:
            info['account_holder'] = m.group(1).strip()

        m = re.search(r'IFSC\s+Code[:\s]+(\w+)', raw_text)
        if m:
            info['ifsc'] = m.group(1)

        m = re.search(r'Branch\s+Address[:\s]+(.+)', raw_text)
        if m:
            info['branch'] = m.group(1).strip()

        return info

    def _extract_period(self, raw_text: str) -> str:
        m = re.search(
            r'Statement\s+for\s+Period\s*:\s*(\d{2}-\d{2}-\d{4})\s+to\s+(\d{2}-\d{2}-\d{4})',
            raw_text
        )
        if m:
            return f"{m.group(1)} to {m.group(2)}"
        return 'N/A'
