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

        # Column header. pdfplumber emits it on one line
        # ("Transaction Value Date Particulars Cheque Debit Credit Balance")
        # or, when the header cells wrap, as three lines:
        #   "Transaction Cheque" / "Value Date Particulars Debit Credit Balance" / "Date No"
        # Requiring the one-line form gave every wrapped-header statement
        # 0 transactions, so match the line that carries the column names.
        header_re = re.compile(r'Value\s+Date\s+Particulars\b.*\bBalance\b', re.IGNORECASE)
        header_fragment_re = re.compile(
            r'^\s*(Transaction(\s+Cheque)?|Date(\s+No)?|Cheque(\s+No)?)\s*$', re.IGNORECASE
        )

        # Where a narration STARTS. The date/amount row is vertically centred
        # in its table cell, so a wrapped narration has lines ABOVE the date
        # line as well as below it:
        #     UPI/CR/525170555819/                         <- start (above)
        #     08-Sep-2025 08-Sep-2025 MAHIPAL /PUNB/ 1.00 50,001.00
        #     wegyane/UPI                                  <- tail (below)
        # The lines between two date rows are therefore split: those before
        # the first narration-start line finish the previous transaction, the
        # rest open the next one.
        narration_start_re = re.compile(
            r'^(UPI/|NEFT/|IMPS|RTGS/|BB/|IFT/|MMT/|ACH/|NACH|ECS/|CASH\s|ATM[/\s]|POS[/\s]|'
            r'MB/|IB/|INT\.?\s|INTEREST\b|CHRG|CHG/|CHARGES\b|SI/|TPT/|FT/|CLG\b|CHQ\b|'
            r'CHEQUE\b|REV[:/\s]|REVERSAL\b|TRF/|SWEEP\b|GST\b|TDS\b|BIL/|DEBIT\s+CARD\b)',
            re.IGNORECASE,
        )

        pending: List[str] = []   # lines seen since the last date row

        def attach(txn: Dict, extra: List[str]) -> None:
            for text in extra:
                # A row whose amounts wrapped below its date line.
                if not txn['balance']:
                    ab2 = amounts_2_re.search(text)
                    if ab2:
                        self._set_amounts(txn, ab2.group(1), ab2.group(2), transactions, raw_text)
                        text = text[:ab2.start()].strip()
                if text:
                    txn['particulars'] += ' ' + text

        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue

            # Section terminators (page footer, disclaimers, end marker) stop
            # reading until the next page's column header. Pending lines are
            # KEPT: a narration can continue at the top of the next page
            # ("NIKHIL YALLUSA" / "BAKALE/BARB0G" before that page's first row).
            if section_terminators.match(stripped):
                in_transactions = False
                continue

            if header_re.search(stripped):
                in_transactions = True
                continue

            if header_fragment_re.match(stripped):
                continue

            # Opening Balance marker row (not a transaction; rows follow it)
            if opening_bal_re.match(stripped):
                in_transactions = True
                continue

            if re.match(r'^\s*Page\s+\d+\s+of\s+\d+', stripped, re.IGNORECASE):
                continue

            if not in_transactions:
                continue

            m = txn_line_re.match(stripped)
            if not m:
                pending.append(stripped)
                continue

            # A date row: split what came before it between the previous
            # transaction's tail and this one's head.
            split = next((k for k, t in enumerate(pending) if narration_start_re.match(t)), None)
            if current_txn is None:
                head, tail = pending, []
            elif split is None:
                head, tail = [], pending
            else:
                head, tail = pending[split:], pending[:split]
            if current_txn:
                attach(current_txn, tail)
                transactions.append(current_txn)
            pending = []

            rest = m.group(3).strip()
            current_txn = {
                'date': self._normalize_date(m.group(1)),
                'particulars': '',
                'chq_ref': '',
                'withdrawal': '',
                'deposit': '',
                'balance': '',
            }

            narration = rest
            ab = amounts_2_re.search(rest)
            if ab:
                self._set_amounts(current_txn, ab.group(1), ab.group(2), transactions, raw_text)
                narration = rest[:ab.start()].strip()
                # Cheque numbers sit just before the amounts ("... 000048 50,000.00 ...")
                # It is its own token: a UPI reference glued to a slash
                # ("UPI/CR/525170555819") is narration, not a cheque number.
                ref_m = re.match(r'^(?:(.*?)\s+)?(\d{6,})\s*$', narration)
                if ref_m:
                    narration = (ref_m.group(1) or '').strip()
                    current_txn['chq_ref'] = ref_m.group(2)

            current_txn['particulars'] = ' '.join(head + ([narration] if narration else []))

        # Don't forget last transaction
        if current_txn:
            attach(current_txn, pending)
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

    def _set_amounts(self, txn: Dict, amount_s: str, balance_s: str,
                     transactions: List[Dict], raw_text: str) -> None:
        """Fill amount + balance; debit vs credit comes from the balance movement."""
        amount = amount_s.replace(',', '')
        balance = balance_s.replace(',', '')
        txn['balance'] = balance
        prev_bal = self._get_prev_balance(transactions)
        if prev_bal is None:
            # First transaction: compare with the "Opening Balance" marker.
            ob = re.search(r'Opening\s+Balance\s+([\d,]+\.\d{2})', raw_text, re.IGNORECASE)
            prev_bal = float(ob.group(1).replace(',', '')) if ob else None
        if prev_bal is not None and float(balance) > prev_bal:
            txn['deposit'] = amount
        else:
            txn['withdrawal'] = amount

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
