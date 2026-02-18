"""
HDFC Bank Statement Parser
===========================
Parses HDFC Bank statement PDFs.

Format characteristics:
  - Text is tightly packed (spaces between words often missing in PDF extraction)
  - Multi-page layout with full header block repeated on each page
  - Header: "PageNo.:X" + account info block + column headers
  - Column Header: Date Narration Chq./Ref.No. ValueDt WithdrawalAmt. DepositAmt. ClosingBalance
  - Page footer: "HDFCBANKLIMITED" + disclaimer block
  - Dates in DD/MM/YY format
  - Multi-line narrations
  - Amounts have commas and 2 decimal places
"""

import re
from typing import Dict, List, Optional
from parsers.base_parser import BaseBankParser


class HDFCBankParser(BaseBankParser):
    """Parser for HDFC Bank statements."""

    BANK_CODE = "hdfc_bank"
    BANK_NAME = "HDFC Bank"
    DETECTION_KEYWORDS = [
        "HDFC",
        "HDFCBANKLIMITED",
        "HDFC0",
        "Statementof account",
        "WithdrawalAmt",
        "DepositAmt",
        "ClosingBalance",
        "PageNo.:",
    ]

    def parse(self, raw_text: str) -> Dict:
        """Parse HDFC Bank statement text into structured data."""
        account_info = self._extract_account_info(raw_text)
        period = self._extract_period(raw_text)

        lines = raw_text.split('\n')
        transactions = []
        current_txn = None
        in_transactions = False

        # Lines that indicate we should skip (headers, footers)
        skip_patterns = [
            r'^PageNo\.\:',
            r'^AccountBranch',
            r'^Address\s*:',
            r'^City\s*:',
            r'^M/S\.',
            r'^State\s*:',
            r'^C/O',
            r'^Currency\s*:',
            r'^Email\s*:',
            r'^CustID',
            r'^A/COpenDate',
            r'^JOINTHOLDERS',
            r'^RTGS/NEFT',
            r'^BranchCode',
            r'^Nomination',
            r'^From\s*:',
            r'^Date\s+Narration',
            r'^HDFCBANKLIMITED',
            r'^\*Closingbalance',
            r'^Contentsofthis',
            r'^thisstatement',
            r'^StateaccountbranchGSTN',
            r'^HDFCBankGSTIN',
            r'^RegisteredOfficeAddress',
            r'^Phoneno\.',
            r'^ODLimit',
            r'^ProductCode',
            r'^AccountStatus',
            r'^JAMMUANDKASHMIR',
        ]

        # Transaction line: starts with DD/MM/YY
        txn_date_pattern = re.compile(r'^(\d{2}/\d{2}/\d{2})\s+(.+)')
        
        # Amount pattern at end of line: withdrawal deposit closing_balance
        # Some lines have 2 amounts (deposit+balance or withdrawal+balance)
        # Some lines have 3 amounts
        amounts_3_pattern = re.compile(
            r'([\d,]+\.\d{2})\s+([\d,]+\.\d{2})\s+(-?[\d,]+\.\d{2})\s*$'
        )
        amounts_2_pattern = re.compile(
            r'([\d,]+\.\d{2})\s+(-?[\d,]+\.\d{2})\s*$'
        )

        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue

            # Skip header/footer lines
            skip = False
            for pat in skip_patterns:
                if re.match(pat, stripped, re.IGNORECASE):
                    skip = True
                    break
            if skip:
                continue

            # Try matching a transaction line
            m = txn_date_pattern.match(stripped)
            if m:
                # Save previous transaction
                if current_txn:
                    transactions.append(current_txn)

                date_str = m.group(1)
                rest = m.group(2).strip()

                # Try to extract amounts from end of line
                withdrawal = ''
                deposit = ''
                balance = ''
                narration = rest
                chq_ref = ''

                # Try 3-amount pattern: narration ref_no date withdrawal deposit balance
                # or: narration ref_no date amount balance
                am3 = amounts_3_pattern.search(rest)
                am2 = amounts_2_pattern.search(rest)

                if am3:
                    narration_part = rest[:am3.start()].strip()
                    # The 3 amounts could be: ref_date withdrawal deposit balance
                    # or: ref_date deposit balance (with withdrawal in amount1 position)
                    
                    # Parse ref_no and value_date from the narration part
                    ref_m = re.search(r'(\d{16}|\w+-\d+|0{15,}|\w+\d{10,})\s+(\d{2}/\d{2}/\d{2})', narration_part)
                    if ref_m:
                        chq_ref = ref_m.group(1)
                        narration = narration_part[:ref_m.start()].strip()
                    else:
                        narration = narration_part
                    
                    amt1 = am3.group(1).replace(',', '')
                    amt2 = am3.group(2).replace(',', '')
                    balance = am3.group(3).replace(',', '')
                    
                    # Determine withdrawal vs deposit by comparing with balance
                    prev_balance = float(transactions[-1]['balance']) if transactions and transactions[-1]['balance'] else 0
                    curr_balance = float(balance)
                    
                    if curr_balance > prev_balance:
                        # Money came in - amt1 could be withdrawal, amt2 deposit
                        # or amt1 is deposit amount
                        deposit = amt1
                        withdrawal = ''
                    else:
                        withdrawal = amt1
                        deposit = ''

                elif am2:
                    narration_part = rest[:am2.start()].strip()
                    
                    ref_m = re.search(r'(\d{16}|\w+-\d+|0{15,}|\w+\d{10,})\s+(\d{2}/\d{2}/\d{2})', narration_part)
                    if ref_m:
                        chq_ref = ref_m.group(1)
                        narration = narration_part[:ref_m.start()].strip()
                    else:
                        narration = narration_part

                    amount = am2.group(1).replace(',', '')
                    balance = am2.group(2).replace(',', '')
                    
                    prev_balance = float(transactions[-1]['balance']) if transactions and transactions[-1]['balance'] else 0
                    curr_balance = float(balance)
                    
                    if curr_balance > prev_balance:
                        deposit = amount
                    else:
                        withdrawal = amount

                # Normalize date from DD/MM/YY to DD-MM-YYYY
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
                # Check if this is a ref number line
                ref_m = re.match(r'^(\d{16}|\w+-\d{10,}|0{15,})\s*$', stripped)
                if ref_m and not current_txn['chq_ref']:
                    current_txn['chq_ref'] = ref_m.group(1)
                else:
                    current_txn['particulars'] += ' ' + stripped

        # Don't forget the last transaction
        if current_txn:
            transactions.append(current_txn)

        # Clean up narrations
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

        m = re.search(r'AccountNo\s*:\s*(\d+)', raw_text)
        if m:
            info['account_number'] = m.group(1)

        m = re.search(r'AccountBranch\s*:\s*(.+)', raw_text)
        if m:
            info['branch'] = m.group(1).strip()

        m = re.search(r'RTGS/NEFTIFSC:\s*(\w+)', raw_text)
        if m:
            info['ifsc'] = m.group(1)

        m = re.search(r'M/S\.\s*(.+)', raw_text)
        if m:
            info['account_holder'] = m.group(1).strip()

        m = re.search(r'ProductCode:(\d+)', raw_text)
        if m:
            code = m.group(1)
            info['type'] = 'Current Account' if code.startswith('11') else 'Account'

        return info

    def _extract_period(self, raw_text: str) -> str:
        """Extract statement period."""
        m = re.search(r'From\s*:\s*(\d{2}/\d{2}/\d{4})\s+To\s*:\s*(\d{2}/\d{2}/\d{4})', raw_text)
        if m:
            return f"{m.group(1)} to {m.group(2)}"
        return 'N/A'

    def _normalize_date(self, date_str: str) -> str:
        """Convert DD/MM/YY to DD-MM-YYYY."""
        m = re.match(r'(\d{2})/(\d{2})/(\d{2})', date_str)
        if m:
            day, month, year = m.group(1), m.group(2), m.group(3)
            year_full = f"20{year}" if int(year) < 50 else f"19{year}"
            return f"{day}-{month}-{year_full}"
        return date_str
