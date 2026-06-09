"""
IDFC FIRST Bank Statement Parser
==================================
Parses IDFC FIRST Bank statement PDFs.

Format characteristics (from real sample analysis):
  - Header: "STATEMENT OF ACCOUNT"
  - Customer info: CUSTOMER ID, ACCOUNT NO, CUSTOMER NAME, ACCOUNT BRANCH
  - IFSC prefix: IDFB0...
  - Period: "STATEMENT PERIOD : YYYY-MM-DD TO YYYY-MM-DD"
  - Summary row: Opening Balance | Total Debit | Total Credit | Closing Balance
  - Column Header (spans 2 lines):
    Line 1: "Transaction Value Date Particulars Cheque Debit Credit Balance"
    Line 2: "Date No"
  - "Opening Balance" marker row with just the balance
  - Transaction lines: DD-Mon-YYYY DD-Mon-YYYY Description Debit Credit Balance
  - Debit and Credit are separate columns; one is filled, the other is blank/0
  - Multi-line narrations (description wraps to the next line)
  - Footer: REGISTERED OFFICE, IMPORTANT MESSAGE, disclaimers, abbreviations
  - "------- End of the statement -------" terminator
"""

import re
from typing import Dict, List, Optional
from parsers.base_parser import BaseBankParser


class IDFCFirstBankParser(BaseBankParser):
    """Parser for IDFC FIRST Bank statements."""

    BANK_CODE = "idfc_first_bank"
    BANK_NAME = "IDFC FIRST Bank"
    DETECTION_KEYWORDS = [
        "IDFB0",
        "IDFC FIRST BANK",
        "IDFC FIRST",
        "idfcfirstbank",
        "IDFC",
        "STATEMENT OF ACCOUNT",
        "banker@idfcfirstbank.com",
    ]
    DETECTION_RULES = [
        (r"\bIDFB0\w{6}\b", 10, True),
        ("banker@idfcfirstbank.com", 10, False),
        ("idfcfirstbank.com", 10, False),
        ("IDFC FIRST BANK", 3, False),
        ("IDFC FIRST", 3, False),
    ]


    # Month abbreviation to number
    MONTHS = {
        'Jan': '01', 'Feb': '02', 'Mar': '03', 'Apr': '04',
        'May': '05', 'Jun': '06', 'Jul': '07', 'Aug': '08',
        'Sep': '09', 'Oct': '10', 'Nov': '11', 'Dec': '12',
    }

    def parse(self, raw_text: str) -> Dict:
        """Parse IDFC FIRST Bank statement text into structured data."""
        account_info = self._extract_account_info(raw_text)
        period = self._extract_period(raw_text)

        lines = raw_text.split('\n')
        transactions = []
        current_txn = None
        in_transactions = False

        # Patterns to skip (headers, footers, page markers, disclaimers)
        skip_patterns = [
            r'^\s*STATEMENT\s+OF\s+ACCOUNT',
            r'^\s*CUSTOMER\s+(ID|NAME)',
            r'^\s*ACCOUNT\s+(NO|BRANCH|OPENING|STATUS|TYPE)',
            r'^\s*COMMUNICATION',
            r'^\s*ADDRESS',
            r'^\s*EMAIL\s+ID',
            r'^\s*PHONE\s+NO',
            r'^\s*Entity\s+CKYC',
            r'^\s*NOMINATION',
            r'^\s*CURRENCY',
            r'^\s*IFSC\s*:',
            r'^\s*MICR\s*:',
            r'^\s*PANCHKULA',       # Address continuation
            r'^\s*INDIA$',
            r'^\s*Sector\s+\d+',
            r'^\s*Panchkula',
            r'^\s*Ground\s+Floor',
            r'^\s*Opening\s+Balance\s+Total\s+Debit',
            r'^\s*[\d,]+\.\d{2}\s+[\d,]+\.\d{2}\s+[\d,]+\.\d{2}\s+[\d,]+\.\d{2}\s*$',
            r'^\s*Transaction\s+Value\s+Date\s+Particulars',
            r'^\s*Date\s+No\s*$',
            r'^\s*Date\s*$',
            r'^\s*REGISTERED\s+OFFICE',
            r'^\s*Page\s+\d+\s+of\s+\d+',
            r'^\s*IMPORTANT\s+(MESSAGE|SAFETY)',
            r'^\s*CONTACT\s+US',
            r'^\s*GRIEVANCE\s+REDRESSAL',
            r'^\s*COMMONLY\s+USED\s+ABBREVIATIONS',
            r'^\s*-{3,}\s*End\s+of\s+the\s+statement',
            r'^\s*[A-Z][A-Z0-9/-]{1,15}\s{2,}\w',  # Abbreviation table rows (KEY  definition)
            r'^\s*Unless\s+the\s+constituent',
            r'^\s*The\s+closing\s+balance',
            r'^\s*also\s+funds',
            r'^\s*balance\s+displayed',
            r'^\s*the\s+Branch',
            r'^\s*Value\s+date.*is\s+the\s+effective',
            r'^\s*Bank\s+does\s+not\s+send',
            r'^\s*account\s+numbers',
            r'^\s*that\s+appears',
            r'^\s*the\s+message',
            r'^\s*This\s+is\s+a\s+system\s+generated',
            r'^\s*Your\s+Deposit\s+accounts',
            r'^\s*www\.dicgc',
            r'^\s*Your\s+debit\s+card',
            r'^\s*eligibility\s+criteria',
            r'^\s*visit\s+https?://',
            r'^\s*Do\s+not\s+transact',
            r'^\s*Never\s+sign',
            r'^\s*Never\s+share',
            r'^\s*caller\s+claims',
            r'^\s*Reach\s+our\s+Bank',
            r'^\s*If\s+you\s+are\s+not\s+satisfied',
            r'^\s*Officer\s+via\s+email',
            r'^\s*2nd\s+and\s+4th',
            r'^\s*Mr\.\s+',
            r'^\s*Maharashtra',
            r'^\s*Email\s+-\s+pno@',
            r'^\s*[•●]\s*',
        ]

        # Transaction date pattern: DD-Mon-YYYY (e.g., "24-Jun-2025")
        txn_line_re = re.compile(
            r'^\s*(\d{1,2}-\w{3}-\d{4})\s+(\d{1,2}-\w{3}-\d{4})\s+(.*)'
        )

        # Opening Balance line (just "Opening Balance" followed by balance amount)
        opening_bal_re = re.compile(
            r'^\s*Opening\s+Balance\s+([\d,]+\.\d{2})\s*$', re.IGNORECASE
        )

        # Amounts at end of line — three numbers: debit credit balance
        # But in practice, only one of debit/credit has a value
        # Pattern: amount amount (debit+balance or credit+balance)
        amounts_2_re = re.compile(
            r'([\d,]+\.\d{2})\s+([\d,]+\.\d{2})\s*$'
        )

        # Section terminators — these ALWAYS end the transaction section
        section_terminators = re.compile(
            r'^\s*(REGISTERED\s+OFFICE|IMPORTANT\s+(MESSAGE|SAFETY)|CONTACT\s+US|'
            r'GRIEVANCE\s+REDRESSAL|COMMONLY\s+USED\s+ABBREVIATIONS|'
            r'-{3,}\s*End\s+of\s+the\s+statement)',
            re.IGNORECASE
        )

        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue

            # Check for end of statement
            if re.match(r'^\s*-{3,}\s*End\s+of\s+the\s+statement', stripped, re.IGNORECASE):
                if current_txn:
                    transactions.append(current_txn)
                    current_txn = None
                in_transactions = False
                continue

            # Section terminators always flush and stop
            if section_terminators.match(stripped):
                if current_txn:
                    transactions.append(current_txn)
                    current_txn = None
                in_transactions = False
                continue

            # Detect start of transaction section via column header
            if re.match(r'^\s*Transaction\s+Value\s+Date\s+Particulars', stripped, re.IGNORECASE):
                in_transactions = True
                continue

            # Skip "Date No" sub-header
            if re.match(r'^\s*Date\s+No\s*$', stripped, re.IGNORECASE):
                continue

            # Opening Balance marker row
            ob_m = opening_bal_re.match(stripped)
            if ob_m:
                # This is just the opening balance marker, not a transaction
                continue

            # Skip page markers
            if re.match(r'^\s*Page\s+\d+\s+of\s+\d+', stripped, re.IGNORECASE):
                continue

            if not in_transactions:
                # Skip header patterns when not in transaction section
                is_skip = False
                for pat in skip_patterns:
                    if re.match(pat, stripped, re.IGNORECASE):
                        is_skip = True
                        break
                if is_skip:
                    continue
                continue

            # Try to match a transaction line
            m = txn_line_re.match(stripped)
            if m:
                # Save previous transaction
                if current_txn:
                    transactions.append(current_txn)

                txn_date_str = m.group(1)    # "24-Jun-2025"
                # val_date_str = m.group(2)  # "24-Jun-2025" (not stored)
                rest = m.group(3).strip()

                withdrawal = ''
                deposit = ''
                balance = ''
                chq_ref = ''
                narration = rest

                # Extract amounts from end of line
                ab = amounts_2_re.search(rest)
                if ab:
                    amount = ab.group(1).replace(',', '')
                    balance = ab.group(2).replace(',', '')
                    narration_part = rest[:ab.start()].strip()

                    # Determine W vs D by comparing balance to previous
                    prev_bal = self._get_prev_balance(transactions)
                    curr_bal = float(balance)

                    if prev_bal is not None:
                        if curr_bal > prev_bal:
                            deposit = amount
                        else:
                            withdrawal = amount
                    else:
                        # First transaction — try to infer from opening balance in header
                        ob_match = re.search(
                            r'Opening\s+Balance\s+([\d,]+\.\d{2})',
                            raw_text, re.IGNORECASE
                        )
                        if ob_match:
                            opening_bal = float(ob_match.group(1).replace(',', ''))
                            if curr_bal > opening_bal:
                                deposit = amount
                            else:
                                withdrawal = amount
                        else:
                            withdrawal = amount

                    # Check if there's a cheque number in the narration
                    # Cheque numbers are typically standalone numeric sequences
                    ref_m = re.match(r'(.+?)\s+(\d{6,})\s*$', narration_part)
                    if ref_m:
                        narration = ref_m.group(1).strip()
                        chq_ref = ref_m.group(2)
                    else:
                        narration = narration_part

                else:
                    # No amounts found — rest might just be the narration start
                    narration = rest

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

            # Continuation line (multi-line narration, no date prefix)
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

        # Account number: "ACCOUNT NO : 10102743775"
        m = re.search(r'ACCOUNT\s+NO\s*:\s*(\S+)', raw_text, re.IGNORECASE)
        if m:
            info['account_number'] = m.group(1)

        # Customer name: "CUSTOMER NAME : ADZDB PRIVATE LIMITED"
        # Name ends before "ACCOUNT BRANCH" on same line
        m = re.search(
            r'CUSTOMER\s+NAME\s*:\s*(.+?)(?:\s+ACCOUNT\s+BRANCH|$)',
            raw_text, re.IGNORECASE
        )
        if m:
            info['account_holder'] = m.group(1).strip()

        # IFSC: "IFSC : IDFB0021351"
        m = re.search(r'IFSC\s*:\s*(IDFB\w+)', raw_text, re.IGNORECASE)
        if m:
            info['ifsc'] = m.group(1)

        # Branch: "ACCOUNT BRANCH : Panchkula Branch"
        m = re.search(r'ACCOUNT\s+BRANCH\s*:\s*(.+?)(?:\n|$)', raw_text, re.IGNORECASE)
        if m:
            info['branch'] = m.group(1).strip()

        # Account type: "ACCOUNT TYPE : New Business Account"
        m = re.search(r'ACCOUNT\s+TYPE\s*:\s*(.+?)(?:\n|$)', raw_text, re.IGNORECASE)
        if m:
            info['type'] = m.group(1).strip()

        return info

    def _extract_period(self, raw_text: str) -> str:
        """Extract statement period."""
        # "STATEMENT PERIOD : 2025-04-01 TO 2026-03-31"
        m = re.search(
            r'STATEMENT\s+PERIOD\s*:\s*(\d{4}-\d{2}-\d{2})\s+TO\s+(\d{4}-\d{2}-\d{2})',
            raw_text, re.IGNORECASE
        )
        if m:
            return f"{m.group(1)} to {m.group(2)}"

        # Fallback: DD-Mon-YYYY to DD-Mon-YYYY
        m = re.search(
            r'(?:From|Period)\s*:\s*(\d{1,2}-\w{3}-\d{4})\s+(?:To|to)\s+(\d{1,2}-\w{3}-\d{4})',
            raw_text, re.IGNORECASE
        )
        if m:
            return f"{m.group(1)} to {m.group(2)}"

        return 'N/A'

    def _normalize_date(self, date_str: str) -> str:
        """Convert 'DD-Mon-YYYY' to 'DD-MM-YYYY'."""
        # "24-Jun-2025" -> "24-06-2025"
        m = re.match(r'(\d{1,2})-(\w{3})-(\d{4})', date_str)
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
