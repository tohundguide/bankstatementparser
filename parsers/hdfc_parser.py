"""
HDFC Bank Statement Parser
===========================
Parses HDFC Bank statement PDFs.

Format characteristics:
  - Multi-page layout with full header block repeated on each page
  - Header: "Page No .: X" + account info block + column headers
  - Column Header: Date Narration Chq./Ref.No. ValueDt WithdrawalAmt. DepositAmt. ClosingBalance
  - Dates in DD/MM/YY format
  - Multi-line narrations (narration wraps across several physical lines)
  - Each transaction row carries a value-date (DD/MM/YY) immediately before the
    money columns, then exactly one of Withdrawal/Deposit, then Closing Balance.
  - Amounts use Indian grouping and 2 decimal places (e.g. 1,23,456.78 / 123,456.78)

Robustness notes (why this parser is written the way it is)
-----------------------------------------------------------
PDF text extraction (pdfplumber / PyPDF2 / OCR) does NOT produce stable spacing
for HDFC's tightly-packed table. Across real files we observe three variants:

  1. Words separated by single spaces (the "clean" case).
  2. Header labels concatenated  ("Account No" -> "AccountNo").
  3. Adjacent numeric columns merged ("1,754.65169,445.52") because the
     Deposit and Closing-Balance columns sit right next to each other, and the
     date column occasionally glued to the first narration word
     ("01/05/26NEFT...").

Earlier versions assumed exactly one of these layouts and silently failed on the
others (0 transactions, or empty amounts/balances). This parser is deliberately
spacing-agnostic:

  * Header/footer detection matches against a space-stripped copy of each line.
  * Transaction detection allows zero-or-more spaces after the leading date.
  * Money columns are recovered with ``re.findall`` of amount tokens, so merged
    columns like ``1,754.65169,445.52`` still split into two values.
  * Debit vs. credit is inferred from the running balance delta (seeded from the
    statement's Opening Balance), which is reliable even when column position is
    lost during extraction.
"""

import re
from typing import Dict, List, Optional, Tuple
from parsers.base_parser import BaseBankParser


# A single amount token: 1,754.65 / 123,456.78 / 12,34,567.89 / 0.00
_AMOUNT_TOKEN = r'\d[\d,]*\.\d{2}'
# A value-date token in the table body
_VDATE_TOKEN = r'\d{2}/\d{2}/\d{2}'
# A reference/cheque token (rightmost long alnum on the first line)
_REF_TOKEN = r'[A-Z]{0,6}\d{6,}'


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
    DETECTION_RULES = [
        (r"\bHDFC0\w{6}\b", 10, True),
        ("HDFCBANKLIMITED", 10, False),
        ("hdfcbank.com", 10, False),
        ("Statementof account", 3, False),
        ("PageNo.:", 1, False),
        ("WithdrawalAmt", 1, False),
        ("DepositAmt", 1, False),
        ("ClosingBalance", 1, False),
    ]
    NEGATIVE_RULES = [
        (r"\bINDB0\w{6}\b", -8, True),
    ]

    # Header/footer markers. These are matched against a SPACE-STRIPPED copy of
    # each line, so they must themselves be written without spaces. This makes
    # detection work whether or not the PDF extractor preserved inter-word spaces.
    _SKIP_COMPACT = [
        r'^PageNo\.?:?',
        r'^AccountBranch',
        r'^Address:',
        r'^City:',
        r'^M/S\.',
        r'^State:',
        r'^C/O',
        r'^Currency:',
        r'^Email:',
        r'^CustID',
        r'^AccountNo',
        r'^A/COpenDate',
        r'^AccountStatus',
        r'^AccountType',
        r'^JOINTHOLDERS',
        r'^RTGS/NEFT',
        r'^BranchCode',
        r'^MICR',
        r'^Nomination',
        r'^From:',
        r'^DateNarration',
        r'^Narration',
        r'^Statementofaccount',
        r'^HDFCBANK(?:LIMITED)?$',
        r'^Weunderstand',
        r'^\*Closingbalance',
        r'^Contentsofthis',
        r'^thisstatement',
        r'^StateaccountbranchGSTN',
        r'^HDFCBankGSTIN',
        r'^RegisteredOfficeAddress',
        r'^Phoneno\.?',
        r'^ODLimit',
        r'^ProductCode',
        r'^GeneratedOn',
        r'^GeneratedBy',
        r'^RequestingBranch',
        r'^Thisisacomputergenerated',
        r'^STATEMENTSUMMARY',
        r'^OpeningBalance',
    ]

    def parse(self, raw_text: str) -> Dict:
        """Parse HDFC Bank statement text into structured data."""
        account_info = self._extract_account_info(raw_text)
        period = self._extract_period(raw_text)
        opening_balance = self._extract_opening_balance(raw_text)
        if opening_balance is not None:
            account_info['opening_balance'] = f"{opening_balance:.2f}"

        skip_res = [re.compile(p, re.IGNORECASE) for p in self._SKIP_COMPACT]

        # Transaction line: starts with DD/MM/YY (space after the date is OPTIONAL,
        # because some extractions glue the date to the first narration word).
        txn_date_re = re.compile(r'^(\d{2}/\d{2}/\d{2})\s*(.+)')

        transactions: List[Dict] = []
        current_txn: Optional[Dict] = None
        running_balance: Optional[float] = opening_balance

        for raw_line in raw_text.split('\n'):
            stripped = self._clean_line(raw_line)
            if not stripped:
                continue

            # Space-insensitive header/footer skip.
            compact = re.sub(r'\s+', '', stripped)
            if any(r.match(compact) for r in skip_res):
                continue

            m = txn_date_re.match(stripped)
            if m:
                if current_txn:
                    transactions.append(current_txn)

                current_txn, running_balance = self._build_txn(
                    m.group(1), m.group(2).strip(), running_balance
                )
                continue

            # Continuation line for the current transaction's narration.
            if current_txn:
                ref_only = re.match(rf'^({_REF_TOKEN})$', compact)
                if ref_only and not current_txn['chq_ref']:
                    current_txn['chq_ref'] = ref_only.group(1)
                else:
                    current_txn['particulars'] += ' ' + stripped

        if current_txn:
            transactions.append(current_txn)

        # Collapse whitespace in narrations.
        for txn in transactions:
            txn['particulars'] = re.sub(r'\s+', ' ', txn['particulars']).strip()

        return {
            'bank_name': self.BANK_NAME,
            'bank_code': self.BANK_CODE,
            'account_info': account_info,
            'period': period,
            'transactions': transactions,
        }

    @staticmethod
    def _clean_line(raw_line: str) -> str:
        """
        Normalise a physical line before parsing.

        Scanned HDFC statements go through OCR, which sprinkles in artefacts that
        would otherwise break column detection:
          * vertical table gridlines read as pipe characters ("01/05/26 | NEFT")
          * stray commas appended to amounts ("5,255.45," instead of "5,255.45")

        We drop pipes and any comma that is not flanked by digits (i.e. not an
        Indian-grouping separator), then collapse whitespace and trim leading
        punctuation left over from the gridline column.
        """
        s = raw_line.replace('|', ' ')
        # Remove commas that aren't part of a number group (e.g. trailing "45,").
        s = re.sub(r',(?!\d)', '', s)
        s = re.sub(r'[ \t]+', ' ', s).strip()
        return s

    # ── Transaction row builder ──

    def _build_txn(self, date_str: str, rest: str,
                   running_balance: Optional[float]) -> Tuple[Dict, Optional[float]]:
        """
        Build a single transaction dict from its first line.

        ``rest`` looks like:
            "<narration> <ref> <value-date> <amount> <closing-balance>"
        but any of those separating spaces may be missing, and the two trailing
        amounts may be glued together. We recover the trailing money block with a
        findall so merged columns still split correctly.

        Returns (txn_dict, updated_running_balance).
        """
        narration = rest
        chq_ref = ''
        withdrawal = ''
        deposit = ''
        balance = ''

        # Isolate the trailing money block: an optional value-date followed by
        # 1..3 amount tokens that run to end of line (spaces between them optional).
        money_re = re.compile(
            rf'({_VDATE_TOKEN})?\s*((?:{_AMOUNT_TOKEN})(?:\s*{_AMOUNT_TOKEN})*)\s*$'
        )
        mm = money_re.search(rest)
        if mm:
            before = rest[:mm.start()].strip()
            amounts = re.findall(_AMOUNT_TOKEN, mm.group(2))

            # Rightmost amount = closing balance; the one before it = txn amount.
            if amounts:
                balance = amounts[-1].replace(',', '')
            txn_amount = amounts[-2].replace(',', '') if len(amounts) >= 2 else ''

            # Reference number: the rightmost long alnum token in ``before``.
            refs = re.findall(rf'\b({_REF_TOKEN})\b', before)
            if refs:
                chq_ref = refs[-1]
                # Strip the ref (and anything after it) from the narration.
                cut = before.rfind(chq_ref)
                narration = before[:cut].strip()
            else:
                narration = before

            # Direction via running-balance delta (robust to column merging).
            withdrawal, deposit, running_balance = self._classify(
                txn_amount, balance, running_balance
            )
        else:
            # No money on the first line — keep whole text as narration.
            narration = rest

        txn = {
            'date': self._normalize_date(date_str),
            'particulars': narration,
            'chq_ref': chq_ref,
            'withdrawal': withdrawal,
            'deposit': deposit,
            'balance': balance,
        }
        return txn, running_balance

    @staticmethod
    def _classify(txn_amount: str, balance: str,
                  running_balance: Optional[float]) -> Tuple[str, str, Optional[float]]:
        """
        Decide withdrawal vs. deposit from the change in balance.

        If the running balance is known, the sign of (new_balance - prev_balance)
        tells us the direction unambiguously, even when extraction destroyed the
        column layout. Falls back to "deposit if balance went up from 0" only when
        no prior balance is available.
        """
        try:
            bal_val = float(balance) if balance else None
        except (ValueError, TypeError):
            bal_val = None

        if not txn_amount:
            return '', '', bal_val if bal_val is not None else running_balance

        if bal_val is None:
            return '', '', running_balance

        prev = running_balance if running_balance is not None else 0.0
        # Closer to prev+amt -> deposit; closer to prev-amt -> withdrawal.
        try:
            amt = float(txn_amount)
        except (ValueError, TypeError):
            amt = 0.0
        up = abs((prev + amt) - bal_val)
        down = abs((prev - amt) - bal_val)
        if up <= down:
            return '', txn_amount, bal_val
        return txn_amount, '', bal_val

    # ── Header / metadata extraction (all space-tolerant) ──

    def _extract_account_info(self, raw_text: str) -> Dict:
        """Extract account information from the repeated header block."""
        info = {
            'account_number': '',
            'account_holder': '',
            'branch': '',
            'ifsc': '',
            'type': '',
        }

        m = re.search(r'Account\s*No\s*:?\s*(\d{6,})', raw_text, re.IGNORECASE)
        if m:
            info['account_number'] = m.group(1)

        m = re.search(r'Account\s*Branch\s*:?\s*(.+)', raw_text, re.IGNORECASE)
        if m:
            info['branch'] = m.group(1).strip()

        m = re.search(r'RTGS\s*/\s*NEFT\s*IFSC\s*:?\s*([A-Z]{4}0[A-Z0-9]{6})',
                      raw_text, re.IGNORECASE)
        if m:
            info['ifsc'] = m.group(1).upper()

        m = re.search(r'M/S\.?\s*(.+)', raw_text)
        if m:
            holder = m.group(1).strip()
            # On scanned statements the adjacent header column can bleed into the
            # M/S line via OCR ("... PRIVATE LIMITED sy, NE ey 10091"). Trim at the
            # company suffix when present.
            sm = re.search(
                r'^(.*?\b(?:PRIVATE LIMITED|PVT\.?\s*LTD\.?|LIMITED|LTD\.?|LLP|'
                r'& SONS|ENTERPRISES|TRADERS|INDUSTRIES|CORPORATION))\b',
                holder, re.IGNORECASE
            )
            if sm:
                holder = sm.group(1)
            info['account_holder'] = holder.strip()

        m = re.search(r'Account\s*Type\s*:?\s*(.+)', raw_text, re.IGNORECASE)
        if m:
            info['type'] = m.group(1).strip()

        return info

    def _extract_period(self, raw_text: str) -> str:
        """Extract statement period (From / To)."""
        m = re.search(
            r'From\s*:?\s*(\d{2}/\d{2}/\d{4})\s+To\s*:?\s*(\d{2}/\d{2}/\d{4})',
            raw_text, re.IGNORECASE
        )
        if m:
            return f"{m.group(1)} to {m.group(2)}"
        return 'N/A'

    def _extract_opening_balance(self, raw_text: str) -> Optional[float]:
        """
        Pull the opening balance from the statement summary so the first row's
        debit/credit direction can be classified correctly.
        """
        m = re.search(r'Opening\s*Balance[\s\S]{0,400}?(' + _AMOUNT_TOKEN + r')',
                      raw_text, re.IGNORECASE)
        if m:
            try:
                return float(m.group(1).replace(',', ''))
            except (ValueError, TypeError):
                return None
        return None

    def _normalize_date(self, date_str: str) -> str:
        """Convert DD/MM/YY to DD-MM-YYYY."""
        m = re.match(r'(\d{2})/(\d{2})/(\d{2})', date_str)
        if m:
            day, month, year = m.group(1), m.group(2), m.group(3)
            year_full = f"20{year}" if int(year) < 80 else f"19{year}"
            return f"{day}-{month}-{year_full}"
        return date_str
