"""
Base Parser
===========
Abstract base class that all bank parsers must implement.
This ensures a consistent interface across all bank-specific parsers.

Detection uses a weighted scoring system:
  - ANCHOR (weight 10): IFSC prefix regex, unique domain/email — one match is enough
  - STRONG (weight 3): bank name variants, distinctive header strings
  - WEAK   (weight 1): generic column headers, generic phrases
  - NEGATIVE (weight -8): tokens that argue *against* this bank (e.g. another bank's IFSC)

Threshold: score >= 10 (has anchor) OR score >= 4 (multiple strong matches).
"""

import re
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Dict, List, Optional, Tuple


# Month name lookup for date normalization
_MONTH_MAP = {
    'jan': '01', 'feb': '02', 'mar': '03', 'apr': '04',
    'may': '05', 'jun': '06', 'jul': '07', 'aug': '08',
    'sep': '09', 'oct': '10', 'nov': '11', 'dec': '12',
}


class BaseBankParser(ABC):
    """
    Abstract base class for bank statement parsers.
    
    Every bank parser must:
    1. Define a unique BANK_CODE (e.g., 'jk_bank')
    2. Define a BANK_NAME (e.g., 'Jammu & Kashmir Bank')
    3. Define DETECTION_RULES — weighted detection patterns
    4. Implement the parse() method
    """
    
    BANK_CODE: str = ""
    BANK_NAME: str = ""
    
    # New weighted detection system
    # Each entry: (pattern_or_substr, weight, is_regex)
    # Weights: 10 = ANCHOR, 3 = STRONG, 1 = WEAK
    DETECTION_RULES: List[Tuple] = []
    
    # Anti-patterns that argue against this bank (weight should be negative)
    NEGATIVE_RULES: List[Tuple] = []
    
    # Legacy keyword list (kept for backward compatibility fallback)
    DETECTION_KEYWORDS: List[str] = []
    
    @abstractmethod
    def parse(self, raw_text: str) -> Dict:
        """
        Parse raw text into structured transaction data.
        
        Args:
            raw_text: The complete raw text extracted from the statement file.
        
        Returns:
            A dictionary with the following structure:
            {
                'bank_name': str,           # Human-readable bank name
                'bank_code': str,           # Internal bank code
                'account_info': {           # Account metadata
                    'account_number': str,
                    'account_holder': str,
                    'branch': str,
                    'ifsc': str,
                    'type': str,
                    'opening_balance': str,  # Optional — raw balance string
                    'closing_balance': str,  # Optional — raw balance string
                },
                'period': str,              # Statement period
                'transactions': [           # List of parsed transactions
                    {
                        'date': str,            # DD-MM-YYYY
                        'particulars': str,     # Full description (joined from multi-line)
                        'chq_ref': str,         # Cheque/Reference number
                        'withdrawal': str,      # Amount or empty
                        'deposit': str,         # Amount or empty
                        'balance': str,         # Balance with Dr/Cr suffix
                    },
                    ...
                ]
            }
        """
        pass
    
    @classmethod
    def detection_score(cls, raw_text: str) -> Tuple[int, int]:
        """
        Compute weighted detection score for this parser against given text.
        
        Returns:
            (score, anchor_hits) tuple for ranking.
        """
        text = raw_text or ""
        upper = text.upper()
        score = 0
        anchor_hits = 0
        
        # Score from positive detection rules
        for token, weight, is_regex in cls.DETECTION_RULES:
            matched = False
            if is_regex:
                if re.search(token, text, re.IGNORECASE):
                    matched = True
            else:
                if token.upper() in upper:
                    matched = True
            
            if matched:
                score += weight
                if weight >= 10:
                    anchor_hits += 1
        
        # Score from negative rules (anti-patterns)
        for token, weight, is_regex in cls.NEGATIVE_RULES:
            if is_regex:
                if re.search(token, text, re.IGNORECASE):
                    score += weight  # weight is negative
            else:
                if token.upper() in upper:
                    score += weight
        
        return score, anchor_hits
    
    @classmethod
    def can_parse(cls, raw_text: str) -> bool:
        """
        Check if this parser can handle the given text.
        Uses weighted scoring: needs an anchor match (score>=10) or
        multiple strong matches (score>=4).
        
        Falls back to legacy DETECTION_KEYWORDS if no DETECTION_RULES defined.
        """
        # Use new weighted system if rules are defined
        if cls.DETECTION_RULES:
            score, anchors = cls.detection_score(raw_text)
            return anchors >= 1 or score >= 4
        
        # Legacy fallback: flat keyword counting
        text_upper = raw_text.upper()
        matches = sum(1 for kw in cls.DETECTION_KEYWORDS if kw.upper() in text_upper)
        return matches >= 2

    # ── Shared Utilities ──

    @staticmethod
    def parse_balance_value(balance_str) -> Optional[float]:
        """
        Parse balance string to signed float. Handles:
          - Trailing Dr/Cr suffixes (Dr = negative/overdrawn, Cr = positive)
          - Prefix Dr/Cr (e.g., "Dr. 25,000.00")
          - Parenthetical negatives (e.g., "(25,000.00)")
          - (-) prefix
          - Currency symbols (₹, INR, Rs.)
          - Indian numbering (1,23,456.78)
          - Non-breaking spaces
        """
        if balance_str is None:
            return None
        s = str(balance_str).strip()
        if not s:
            return None

        neg = False

        # Currency symbols / codes
        s = re.sub(r"(?i)\b(?:INR|RS)\b\.?", "", s)
        s = s.replace("\u20b9", "").replace("\u00a0", " ").strip()  # ₹, nbsp

        # Parenthetical negative: (25,000.00)
        if s.startswith("(") and s.endswith(")"):
            neg = not neg
            s = s[1:-1].strip()

        # (-) prefix
        if s.startswith("(-)"):
            neg = not neg
            s = s[3:].strip()

        # Prefix Dr./Cr.
        m = re.match(r"(?i)^(dr|cr)\.?\s+", s)
        if m:
            if m.group(1).lower() == "dr":
                neg = not neg
            s = s[m.end():].strip()

        # Suffix Dr./Cr.
        m = re.search(r"(?i)\s*(dr|cr)\.?\s*$", s)
        if m:
            if m.group(1).lower() == "dr":
                neg = not neg
            s = s[:m.start()].strip()

        # Leading sign
        if s.startswith("-"):
            neg = not neg
            s = s[1:].strip()
        elif s.startswith("+"):
            s = s[1:].strip()

        # Strip grouping commas (handles both 1,00,000 and 100,000)
        s = s.replace(",", "").strip()

        try:
            val = float(s)
        except (ValueError, TypeError):
            return None
        return -val if neg else val

    @staticmethod
    def normalize_date(date_str: str) -> str:
        """
        Normalize various date formats to DD-MM-YYYY.
        
        Handles:
          - DD-MM-YYYY, DD/MM/YYYY (passthrough with separator fix)
          - DD-Mon-YYYY, DD Mon YYYY (month name)
          - DD/MM/YY, DD-MM-YY (2-digit year, pivot on 2000)
          - D Mon YYYY (single-digit day)
        """
        if not date_str:
            return date_str
        s = date_str.strip()

        # DD-MM-YYYY or DD/MM/YYYY
        m = re.match(r'^(\d{2})[/-](\d{2})[/-](\d{4})$', s)
        if m:
            return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"

        # DD/MM/YY or DD-MM-YY (2-digit year)
        m = re.match(r'^(\d{2})[/-](\d{2})[/-](\d{2})$', s)
        if m:
            yr = int(m.group(3))
            year = 2000 + yr if yr < 80 else 1900 + yr
            return f"{m.group(1)}-{m.group(2)}-{year}"

        # DD-Mon-YYYY or DD/Mon/YYYY or DD Mon YYYY or D Mon YYYY
        m = re.match(r'^(\d{1,2})[/-\s]+([A-Za-z]{3})[/-\s]+(\d{4})$', s)
        if m:
            day = m.group(1).zfill(2)
            month = _MONTH_MAP.get(m.group(2).lower()[:3], m.group(2))
            year = m.group(3)
            return f"{day}-{month}-{year}"

        return s  # return as-is if no pattern matches

    @staticmethod
    def join_wrapped_dates(lines: List[str]) -> List[str]:
        """
        Pre-pass to re-join Finacle-style split dates.
        
        Finacle banks (SBI, BoB, Union) sometimes emit:
          Line 1: "31 Jan"
          Line 2: "2024 UPI/CR/... 5000.00 25000.00"
        
        This joins them back into a single line.
        """
        out = []
        i = 0
        while i < len(lines):
            cur = lines[i]
            # Check if line ends with "D Mon" or "DD Mon" pattern
            if (re.search(r'\d{1,2}\s+[A-Za-z]{3}\s*$', cur) 
                    and i + 1 < len(lines)
                    and re.match(r'^\s*\d{4}\b', lines[i + 1])):
                cur = cur.rstrip() + " " + lines[i + 1].lstrip()
                i += 1
            out.append(cur)
            i += 1
        return out

    def validate(self, result: Dict) -> List[str]:
        """
        Post-parse validation. Returns a list of warning codes.
        
        Warnings:
          - NO_TRANSACTIONS: parse returned 0 transactions
          - MANY_EMPTY_BALANCES: >50% of transactions have no balance
          - ROWS_NO_AMOUNT: rows with neither withdrawal nor deposit
          - DATES_NOT_MONOTONIC: dates jump around (not sorted in either direction)
        """
        warns = []
        txns = result.get('transactions', [])
        
        if not txns:
            warns.append("NO_TRANSACTIONS")
            return warns
        
        # Empty balances
        empty_bal = sum(1 for t in txns if not t.get('balance'))
        if empty_bal > len(txns) * 0.5:
            warns.append(f"MANY_EMPTY_BALANCES:{empty_bal}/{len(txns)}")
        
        # Rows with no amount
        both_blank = sum(
            1 for t in txns
            if not t.get('withdrawal') and not t.get('deposit')
            and t.get('particulars', '').strip() not in ('Opening Balance', 'Closing Balance', '')
        )
        if both_blank:
            warns.append(f"ROWS_NO_AMOUNT:{both_blank}")
        
        # Date monotonicity
        dates = []
        for t in txns:
            d = t.get('date', '').strip()
            if d:
                try:
                    for fmt in ('%d-%m-%Y', '%d/%m/%Y', '%d-%m-%y'):
                        try:
                            dates.append(datetime.strptime(d, fmt))
                            break
                        except ValueError:
                            continue
                except Exception:
                    pass
        
        if len(dates) >= 3:
            asc = all(dates[i] <= dates[i+1] for i in range(len(dates)-1))
            desc = all(dates[i] >= dates[i+1] for i in range(len(dates)-1))
            if not asc and not desc:
                warns.append("DATES_NOT_MONOTONIC")
        
        return warns

    @staticmethod
    def _get_prev_balance(transactions: list) -> Optional[float]:
        """Get the balance from the last transaction that has one (legacy helper)."""
        for txn in reversed(transactions):
            bal_str = txn.get('balance', '')
            if bal_str:
                try:
                    return float(str(bal_str).replace(',', ''))
                except (ValueError, TypeError):
                    pass
        return None

    # ── Column-merge & amount extraction utilities ──

    # Regex matching Indian-format amounts (both 1,00,000.00 and 100,000.00)
    _AMOUNT_RE = re.compile(r'-?\(?\d{1,3}(?:,\d{2,3})*(?:\.\d{1,2})?\)?')

    @classmethod
    def split_trailing_amounts(cls, row_text: str) -> tuple:
        """
        Extract trailing amounts from a row where columns may have merged.
        
        When pdfplumber merges adjacent W/D/Balance columns into one text blob,
        this extracts all numeric tokens from the right side and assigns:
          - rightmost = balance
          - second-from-right = transaction amount
        
        Returns:
            (amount_str, balance_str) — both cleaned, or ('', '') if not found.
        """
        # Find all amount-like tokens
        amts = cls._AMOUNT_RE.findall(row_text)
        if not amts:
            return ('', '')
        if len(amts) == 1:
            return (amts[0], amts[0])  # could be either
        # Rightmost = balance, second = amount
        return (amts[-2], amts[-1])

    @staticmethod
    def group_rows(lines: List[str], date_re) -> List[str]:
        """
        Group physical lines into logical transaction rows.
        
        Lines starting with a date are new transactions; lines without dates
        are continuations of the previous transaction's narration.
        
        Args:
            lines: List of text lines
            date_re: Compiled regex that matches a date at the start of a line
        
        Returns:
            List of joined rows (one per transaction)
        """
        rows = []
        current = None
        for ln in lines:
            stripped = ln.strip()
            if not stripped:
                continue
            if date_re.match(stripped):
                if current is not None:
                    rows.append(current)
                current = stripped
            elif current is not None:
                current += ' ' + stripped
        if current is not None:
            rows.append(current)
        return rows

    # ── Finacle family helpers (SBI, BoB, Union Bank, Bank of India) ──

    # Skip patterns common in Finacle statements
    _FINACLE_SKIP_RE = re.compile(
        r'^\s*(?:OPENING BALANCE|CLOSING BALANCE|BALANCE AS ON|'
        r'B/?F\b|BROUGHT FORWARD|CARRIED FORWARD|TOTAL\b|'
        r'Page\s+\d|Statement|Account\s+Statement|'
        r'IFS\s+Code|MICR\s+Code|Branch\s+Code|CIF\s+No|'
        r'S\.?\s*No\.?|Sr\.?\s*No\.?|Sl\.?\s*No\.?)',
        re.IGNORECASE
    )

    @staticmethod
    def finacle_parse_direction(narration: str) -> str:
        """
        Determine transaction direction from Finacle narration prefix.
        
        Finacle banks (SBI, BoB, Union) encode direction in the narration:
          - "BY TRANSFER" / "BY " prefix = Credit (deposit)
          - "TO TRANSFER" / "TO " prefix = Debit (withdrawal)
          - "/CR/" in UPI = Credit
          - "/DR/" in UPI = Debit
        
        Returns: 'credit', 'debit', or 'unknown'
        """
        up = narration.upper().strip()
        
        # BY/TO TRANSFER patterns
        if up.startswith('BY TRANSFER') or up.startswith('BY CLG') or up.startswith('BY CASH'):
            return 'credit'
        if up.startswith('TO TRANSFER') or up.startswith('TO CLG') or up.startswith('TO CASH'):
            return 'debit'
        
        # UPI direction markers
        if '/CR/' in up:
            return 'credit'
        if '/DR/' in up:
            return 'debit'
        
        # General BY/TO prefix (less reliable, but common)
        if re.match(r'^BY\s+', up):
            return 'credit'
        if re.match(r'^TO\s+', up):
            return 'debit'
        
        # Keyword hints
        credit_kw = ['DEPOSIT', 'CREDIT', 'INTEREST', 'REFUND', 'REVERSAL', 'CASHBACK',
                      'NEFT CR', 'RTGS CR', 'IMPS CR', 'INT.PD', 'INT PD']
        debit_kw = ['WITHDRAWAL', 'DEBIT', 'CHARGE', 'FEE', 'GST', 'TAX', 'ATM',
                     'NEFT DR', 'RTGS DR', 'EMI', 'LOAN', 'POS']
        
        for kw in credit_kw:
            if kw in up:
                return 'credit'
        for kw in debit_kw:
            if kw in up:
                return 'debit'
        
        return 'unknown'

    @staticmethod
    def finacle_extract_ref(text: str) -> str:
        """Extract reference/cheque number from Finacle transaction text."""
        # Common ref patterns: standalone number sequences, UPI ref numbers
        m = re.search(r'\b(\d{6,18})\b', text)
        if m:
            return m.group(1)
        # UTR / Ref No patterns
        m = re.search(r'(?:Ref|UTR|RRN)[:\s]*(\S+)', text, re.IGNORECASE)
        if m:
            return m.group(1)
        return ''

    @staticmethod
    def finacle_clean_narration(text: str, date_str: str = '') -> str:
        """
        Clean a Finacle narration by removing the date, amounts, and extra whitespace.
        
        Args:
            text: Raw row text
            date_str: Date string to strip from the beginning
        
        Returns:
            Cleaned narration string
        """
        narration = text
        # Remove date from start
        if date_str:
            narration = narration.replace(date_str, '', 1).strip()
        # Remove trailing amount tokens (they're extracted separately)
        narration = re.sub(r'\s+[\d,]+\.\d{2}(?:\s+[\d,]+\.\d{2})*\s*$', '', narration)
        # Clean up whitespace
        narration = re.sub(r'\s+', ' ', narration).strip()
        return narration
