"""
Yes Bank Statement Parser
==========================
Parses Yes Bank statement PDFs.

Format characteristics (from real sample analysis):
  - Header: "Statement of account: XXXXXXX"
  - Period: "Period: 01 Apr 2025 - 31 Mar 2026"
  - Customer info block with branch details, IFSC (YESB0...), Customer ID
  - Account info: "Transaction details for your account number XXX (CURRENT/SAVINGS)"
  - Column Header (spans 2 lines):
    Line 1: "Transaction"
    Line 2: "Value Date Cheque No/Reference No Description Withdrawals Deposits Running Balance"
    Then: "Date"
  - Transaction lines: DD Mon YYYY DD Mon YYYY RefNo Description Amount Balance
  - Each line has ONE amount (either W or D) and the Running Balance
  - W/D classification: compare current balance to previous — if balance went up, it's a deposit
  - Multi-page: page headers (Customer Id, Primary Account Holder, column headers) repeat
  - Summary at bottom: "Opening Balance: X Total Withdrawals: Y Total Deposits: Z Closing Balance: W"
  - Multi-line narrations (description wraps to lines without date)
  - Footer: disclaimers, transaction codes, nominee info
"""

import re
from typing import Dict, List, Optional
from parsers.base_parser import BaseBankParser


class YesBankParser(BaseBankParser):
    """Parser for Yes Bank statements."""

    BANK_CODE = "yes_bank"
    BANK_NAME = "Yes Bank"
    DETECTION_KEYWORDS = [
        "YESB0",
        "YES BANK",
        "YES BANK LIMITED",
        "Yes Bank Ltd",
        "YES TOUCH",
        "YesRewardz",
        "Statement of account",
        "YES BANK LTD",
    ]

    # Month name to number
    MONTHS = {
        'Jan': '01', 'Feb': '02', 'Mar': '03', 'Apr': '04',
        'May': '05', 'Jun': '06', 'Jul': '07', 'Aug': '08',
        'Sep': '09', 'Oct': '10', 'Nov': '11', 'Dec': '12',
    }

    def parse(self, raw_text: str) -> Dict:
        """Parse Yes Bank statement text into structured data."""
        account_info = self._extract_account_info(raw_text)
        period = self._extract_period(raw_text)

        lines = raw_text.split('\n')
        transactions = []
        current_txn = None
        in_transactions = False
        opening_balance = None
        closing_balance = None

        # Page header patterns to skip (repeat on each page)
        page_header_patterns = [
            r'^\s*Customer\s+Id:',
            r'^\s*Primary\s+Account\s+Holder',
            r'^\s*Transaction\s+details\s+for',
            r'^\s*Primary\s+Holder:',
            r'^\s*Nominee\s+Details:',
            r'^\s*Transaction$',
            r'^\s*Value\s+Date\s+Cheque',
            r'^\s*Date$',
        ]

        # Footer/disclaimer patterns
        footer_patterns = [
            r'^\s*Opening\s+Balance:',
            r'^\s*OD\s+Limit:',
            r'^\s*For\s+(Non-)?Resident',
            r'^\s*necessary\s+details',
            r'^\s*can\s+click\s+here',
            r'^\s*update\s+your\s+mobile',
            r'^\s*Have\s+you\s+registered',
            r'^\s*Benefits\s+of\s+Nomination',
            r'^\s*\*Please\s+ignore',
            r'^\s*Mandatory\s+disclaimer',
            r'^\s*Closing\s+Balance\s+figure',
            r'^\s*Under\s+Goods',
            r'^\s*please\s+contact',
            r'^\s*Transaction\s+codes',
            r'^\s*ATW/CSW',
            r'^\s*AFD\s*/\s*AFC',
            r'^\s*R-\s*RET',
            r'^\s*Please\s+check',
            r'^\s*toll\s+free',
            r'^\s*be\s+correct',
            r'^\s*\*\s*Reward\s+points',
            r'^\s*To\s+redeem',
        ]

        # Transaction date pattern: DD Mon YYYY (e.g., "21 Mar 2026")
        # A line starts with: TxnDate ValueDate RefNo Description Amount Balance
        # Example: "21 Mar 2026 21 Mar 2026 SCREF01280417082 NS_LU_MPOS... 499.00 181,483.50"
        txn_line_re = re.compile(
            r'^\s*(\d{1,2}\s+\w{3}\s+\d{4})\s+(\d{1,2}\s+\w{3}\s+\d{4})\s+(.*)'
        )

        # Amount + balance at end of line: "amount balance"
        amount_balance_re = re.compile(
            r'([\d,]+\.\d{2})\s+([\d,]+\.\d{2})\s*$'
        )

        # Opening/Closing balance summary line
        summary_re = re.compile(
            r'Opening\s+Balance:\s+([\d,]+\.\d{2})\s+'
            r'Total\s+Withdrawals:\s+([\d,]+\.\d{2})\s+'
            r'Total\s+Deposits:\s+([\d,]+\.\d{2})\s+'
            r'Closing\s+Balance:\s+([\d,]+\.\d{2})',
            re.IGNORECASE
        )

        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue

            # Check for the summary line (marks end of all transactions)
            sm = summary_re.search(stripped)
            if sm:
                opening_balance = sm.group(1).replace(',', '')
                closing_balance = sm.group(4).replace(',', '')
                if current_txn:
                    transactions.append(current_txn)
                    current_txn = None
                in_transactions = False
                continue

            # Check for column header "Value Date" — marks start of transaction section
            if re.match(r'^\s*Value\s+Date\s+Cheque', stripped, re.IGNORECASE):
                in_transactions = True
                continue

            # Skip page header lines
            is_page_header = False
            for pat in page_header_patterns:
                if re.match(pat, stripped, re.IGNORECASE):
                    is_page_header = True
                    break
            if is_page_header:
                continue

            # Skip footer lines
            is_footer = False
            for pat in footer_patterns:
                if re.match(pat, stripped, re.IGNORECASE):
                    is_footer = True
                    in_transactions = False
                    if current_txn:
                        transactions.append(current_txn)
                        current_txn = None
                    break
            if is_footer:
                continue

            # Skip non-transaction area
            if not in_transactions:
                continue

            # Try to match a transaction line
            m = txn_line_re.match(stripped)
            if m:
                # Save previous transaction
                if current_txn:
                    transactions.append(current_txn)

                txn_date_str = m.group(1)   # "21 Mar 2026"
                val_date_str = m.group(2)   # "21 Mar 2026"
                rest = m.group(3).strip()   # "SCREF... Description Amount Balance"

                withdrawal = ''
                deposit = ''
                balance = ''
                chq_ref = ''
                narration = rest

                # Extract amounts from end of line
                ab = amount_balance_re.search(rest)
                if ab:
                    amount = ab.group(1).replace(',', '')
                    balance = ab.group(2).replace(',', '')
                    narration_part = rest[:ab.start()].strip()

                    # Split narration_part into ref + description
                    # Ref is typically the first non-space token
                    ref_m = re.match(r'(\S+)\s+(.*)', narration_part)
                    if ref_m:
                        chq_ref = ref_m.group(1)
                        narration = ref_m.group(2).strip()
                    else:
                        narration = narration_part

                    # Determine W vs D by comparing balance to previous
                    prev_bal = self._get_prev_balance(transactions)
                    curr_bal = float(balance)

                    if prev_bal is not None:
                        if curr_bal > prev_bal:
                            deposit = amount
                        else:
                            withdrawal = amount
                    else:
                        # No previous balance — can't determine, default withdrawal
                        withdrawal = amount
                else:
                    # No amount on this line — might be a continuation
                    ref_m = re.match(r'(\S+)\s+(.*)', rest)
                    if ref_m:
                        chq_ref = ref_m.group(1)
                        narration = ref_m.group(2).strip()

                date_normalized = self._normalize_date(txn_date_str)

                current_txn = {
                    'date': date_normalized,
                    'particulars': narration,
                    'chq_ref': chq_ref,
                    'withdrawal': withdrawal,
                    'deposit': deposit,
                    'balance': balance,
                }
                continue

            # Continuation line (no date prefix)
            if current_txn:
                current_txn['particulars'] += ' ' + stripped

        # Don't forget last transaction
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

    def _get_prev_balance(self, transactions: List[Dict]) -> Optional[float]:
        """Get the numeric balance from the last transaction."""
        if not transactions:
            return None
        bal = transactions[-1].get('balance', '')
        if not bal:
            return None
        try:
            return float(str(bal).replace(',', ''))
        except (ValueError, TypeError):
            return None

    def _extract_account_info(self, raw_text: str) -> Dict:
        """Extract account information from header."""
        info = {
            'account_number': '',
            'account_holder': '',
            'branch': '',
            'ifsc': '',
            'type': '',
        }

        # Account number: "Statement of account: 001363400002725"
        m = re.search(r'Statement\s+of\s+account:\s*(\S+)', raw_text, re.IGNORECASE)
        if m:
            info['account_number'] = m.group(1)
        else:
            # Fallback: "account number XXXX (TYPE)"
            m = re.search(r'account\s+number\s+(\S+)\s*\(', raw_text, re.IGNORECASE)
            if m:
                info['account_number'] = m.group(1)

        # Account holder: "Primary Holder: WABISABI STORES..."
        m = re.search(r'Primary\s+Holder:\s*(.+?)(?:A/C|$)', raw_text, re.IGNORECASE)
        if m:
            info['account_holder'] = m.group(1).strip()

        # IFSC
        m = re.search(r'IFSC\s+Code:\s*(YESB\w+)', raw_text, re.IGNORECASE)
        if m:
            info['ifsc'] = m.group(1)

        # Branch: "Name: YES BANK LTD-SRINAGAR"
        m = re.search(r'Name:\s*(YES BANK[^\n]*)', raw_text)
        if m:
            info['branch'] = m.group(1).strip()

        # Account type: "(CURRENT)" or "(SAVINGS)"
        m = re.search(r'\((\w+)\)\s*\(Currency', raw_text)
        if m:
            info['type'] = m.group(1)

        return info

    def _extract_period(self, raw_text: str) -> str:
        """Extract statement period."""
        # "Period: 01 Apr 2025 - 31 Mar 2026"
        m = re.search(
            r'Period:\s*(\d{1,2}\s+\w{3}\s+\d{4})\s*-\s*(\d{1,2}\s+\w{3}\s+\d{4})',
            raw_text, re.IGNORECASE
        )
        if m:
            return f"{m.group(1)} to {m.group(2)}"

        # Fallback: DD/MM/YYYY to DD/MM/YYYY
        m = re.search(
            r'(?:From|Period)[:\s]*([\d/\-]+\d{4})\s+(?:To|to|-)\s+([\d/\-]+\d{4})',
            raw_text, re.IGNORECASE
        )
        if m:
            return f"{m.group(1)} to {m.group(2)}"

        return 'N/A'

    def _normalize_date(self, date_str: str) -> str:
        """Convert 'DD Mon YYYY' to 'DD-MM-YYYY'."""
        # "21 Mar 2026" -> "21-03-2026"
        m = re.match(r'(\d{1,2})\s+(\w{3})\s+(\d{4})', date_str)
        if m:
            day = m.group(1).zfill(2)
            month = self.MONTHS.get(m.group(2), m.group(2))
            year = m.group(3)
            return f"{day}-{month}-{year}"

        # DD/MM/YYYY -> DD-MM-YYYY
        m = re.match(r'(\d{2})/(\d{2})/(\d{4})', date_str)
        if m:
            return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"

        return date_str
