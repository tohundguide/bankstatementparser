"""
Jammu & Kashmir Bank Statement Parser
======================================
Parses the fixed-width text format used by J&K Bank statements.

Column positions (from header analysis):
  Header text positions:
    DATE:          col 6
    PARTICULARS:   col 15
    CHQ.NO./REF.NO: col 33
    WITHDRAWALS:   col 51  
    DEPOSITS:      col 65
    BALANCE:       col 81

  Data alignment:
    Date:         cols 2-11  (DD-MM-YYYY)
    Particulars:  cols 14-32 (left-aligned text)
    Chq/Ref:      cols 32-50 (left/center aligned)
    Withdrawals:  cols 51-64 (right-aligned numbers)
    Deposits:     cols 65-76 (right-aligned numbers)
    Balance:      cols 77+   (number followed by Dr/Cr)

Format characteristics:
  - Each page has a header block (~20 lines: bank name, address, account info)
  - Transactions are between header and "Page Total:" line
  - Multi-line transactions: Particulars wrap to 2-3 continuation lines
  - Continuation lines have NO date (blank date area)
  - Transaction may span page breaks (continuation on next page)
  - Page total lines appear between dashed separators
  - Footer disclaimer text appears at bottom of each page
"""

import re
from typing import Dict, List, Optional
from parsers.base_parser import BaseBankParser


class JKBankParser(BaseBankParser):
    """Parser for Jammu & Kashmir Bank statements."""
    
    BANK_CODE = "jk_bank"
    BANK_NAME = "Jammu & Kashmir Bank"
    DETECTION_KEYWORDS = [
        "JAMMU AND KASHMIR BANK",
        "JAKA0",
        "JAKA",
        "MICR Code",
        "cKYC Id",
        "STATEMENT OF ACCOUNT FOR THE PERIOD",
    ]
    DETECTION_RULES = [
        (r"\bJAKA0\w{6}\b", 10, True),
        ("jkbank.com", 10, False),
        # Newer "DETAILED ACCOUNT STATEMENT" export (no JAKA0/IFSC label on the
        # page). Its pipe-delimited account header — "Account: <16digits>|NAME|
        # <PRODUCT>|<branch>" — is J&K-specific and always present, so it anchors.
        (r"Account:\s*\d{11,}\|[^|]+\|[A-Z]{2,4}\|\d{3,4}", 10, True),
        ("Within J&K Bank", 10, False),
        ("JAMMU AND KASHMIR BANK", 3, False),
        ("DETAILED ACCOUNT STATEMENT", 3, False),
        ("cKYC Id", 3, False),
        ("STATEMENT OF ACCOUNT FOR THE PERIOD", 1, False),
    ]

    
    # Date pattern: "  DD-MM-YYYY  " at start of line
    DATE_RE = re.compile(r'^\s{0,4}(\d{2}-\d{2}-\d{4})\s')
    
    # Balance pattern: number followed by Dr or Cr at end of line
    BALANCE_RE = re.compile(r'([\d,]+\.\d{2})\s*(Dr|Cr)\s*$')
    
    # Amount pattern
    AMOUNT_RE = re.compile(r'([\d,]+\.\d{2})')
    
    # B/F line
    BF_RE = re.compile(r'^\s+B/F\s+(.+)$')
    
    # Lines to skip (headers, footers, page structure)
    SKIP_PATTERNS = [
        re.compile(r'^\s*$'),
        re.compile(r'^-{10,}'),
        re.compile(r'JAMMU AND KASHMIR BANK', re.I),
        re.compile(r'KHONMOH', re.I),
        re.compile(r'NEAR JAMIA MASJID', re.I),
        re.compile(r'IFSC Code', re.I),
        re.compile(r'PHONE Code', re.I),
        re.compile(r'TYPE:.*SCHEME', re.I),
        re.compile(r'A/C NO:', re.I),
        re.compile(r'Printed By', re.I),
        re.compile(r'^\s*TO:\s*$', re.I),
        re.compile(r'M/S\.', re.I),
        re.compile(r'S/O\s+SHRI', re.I),
        re.compile(r'C/O\s+FOOD', re.I),
        re.compile(r'SRINAGAR,JAMMU', re.I),
        re.compile(r'^\s*\d{6}\s*$'),
        re.compile(r'cKYC Id', re.I),
        re.compile(r'No Nomination', re.I),
        re.compile(r'STATEMENT OF ACCOUNT FOR', re.I),
        re.compile(r'DATE\s+PARTICULARS\s+CHQ', re.I),
        re.compile(r'Page Total:', re.I),
        re.compile(r'Unless the constituent', re.I),
        re.compile(r'immediately of any discrepancy', re.I),
        re.compile(r'by him in this statement', re.I),
        re.compile(r'it will be\s+taken', re.I),
        re.compile(r'the account correct', re.I),
        re.compile(r'Date Stamp\s+Manager', re.I),
        re.compile(r'Grand Total:', re.I),
        re.compile(r'Funds in clearing:', re.I),
        re.compile(r'Total available Amount:', re.I),
        re.compile(r'Effective Available Amount', re.I),
        re.compile(r'FFD Contribution', re.I),
    ]
    
    def parse(self, raw_text: str) -> Dict:
        """Parse J&K Bank statement text into structured transaction data."""
        if self._is_detailed_format(raw_text):
            return self._parse_detailed(raw_text)

        account_info = self._extract_account_info(raw_text)
        period = self._extract_period(raw_text)
        transactions = self._parse_transactions(raw_text)

        return {
            'bank_name': self.BANK_NAME,
            'bank_code': self.BANK_CODE,
            'account_info': account_info,
            'period': period,
            'transactions': transactions,
        }

    # ──────────────────────────────────────────────────────────────────
    # "DETAILED ACCOUNT STATEMENT" format
    # ──────────────────────────────────────────────────────────────────
    #
    # A newer J&K Bank export, totally unlike the fixed-width Dr/Cr layout
    # above. Columns:
    #   Value Date | Transaction Date | Cheque No | Transaction Remarks |
    #   Withdrawal(INR) | Deposit(INR) | Account Balance(INR) | Txn Ref No
    #
    # Every row's numbers live on ONE line (two slash-dates, then the
    # cheque/remarks text, then the three money columns, then the ref):
    #   26/06/2020 26/06/2020 - 484.35 0.0 99,676.05 S96199091
    # The remark text wraps onto separate lines ABOVE (head) and BELOW
    # (tail) that numeric line, in pdfplumber's reading order. Dates,
    # amounts and balances are always exact; only the remark join is
    # best-effort (head/tail attribution can occasionally misorder a
    # wrapped fragment — it never affects a number).

    _DTL_ROW = re.compile(
        r'^(\d{2}/\d{2}/\d{4})\s+(\d{2}/\d{2}/\d{4})\s+(.*?)\s+'
        r'([\d,]+\.\d+)\s+([\d,]+\.\d+)\s+([\d,]+\.\d+)\s+(\S+)\s*$'
    )
    # A wrapped remark line is a "head" (belongs to the row below) when it
    # opens a new narration: a POS/ref run of 5+ digits after a slash, or a
    # known transfer keyword. Otherwise it's a "tail" of the row above
    # (e.g. "20:25:48/SWT", "2020 11:55:15/SWT", "Y MO", "PVT LTD").
    _DTL_HEAD = re.compile(
        r'/\d{5,}|^(?:NEFT|RTGS|IMPS|UPI|INFT|BPAY|BY|TO|CASH|CHQ|ATM)\b',
        re.IGNORECASE
    )

    def _is_detailed_format(self, raw_text: str) -> bool:
        """True for the 'DETAILED ACCOUNT STATEMENT' column layout."""
        return (
            'Withdrawal(INR)' in raw_text
            and 'Deposit(INR)' in raw_text
            and 'Account Balance(INR)' in raw_text
        )

    def _dtl_skip(self, line: str) -> bool:
        s = line.strip()
        if s in ('No', 'Transaction Ref'):
            return True
        return bool(re.match(
            r'^(DETAILED ACCOUNT STATEMENT|Account:|Transaction Date From|'
            r'Transaction Period:|Last N Transactions:|Category:|'
            r'Transactions List|Value Date\s+Transaction Date)',
            s, re.IGNORECASE
        ))

    def _parse_detailed(self, raw_text: str) -> Dict:
        account_info = self._extract_detailed_account_info(raw_text)
        period = self._extract_detailed_period(raw_text)

        transactions: List[Dict] = []
        head_buf: List[str] = []
        ended = False

        for raw_line in raw_text.split('\n'):
            s = raw_line.strip()
            if not s:
                continue
            if re.match(r'^Legends Used', s, re.IGNORECASE):
                ended = True
                continue
            if ended or self._dtl_skip(s):
                continue

            m = self._DTL_ROW.match(s)
            if m:
                mid = m.group(3).strip()
                # Leading token is the Cheque No column ("-" when none)
                if mid == '-':
                    mid = ''
                elif mid.startswith('- '):
                    mid = mid[2:].strip()

                def nz(v: str) -> str:
                    v = v.replace(',', '')
                    try:
                        return '' if float(v) == 0 else v
                    except ValueError:
                        return v

                transactions.append({
                    'date': m.group(2),
                    'particulars': ' '.join([*head_buf, mid]).strip(),
                    'chq_ref': m.group(7),
                    'withdrawal': nz(m.group(4)),
                    'deposit': nz(m.group(5)),
                    'balance': m.group(6).replace(',', ''),
                })
                head_buf = []
            elif self._DTL_HEAD.search(s):
                head_buf.append(s)
            elif transactions:
                transactions[-1]['particulars'] = (
                    transactions[-1]['particulars'] + ' ' + s).strip()
            else:
                head_buf.append(s)

        for txn in transactions:
            txn['particulars'] = re.sub(r'\s{2,}', ' ', txn['particulars']).strip()

        return {
            'bank_name': self.BANK_NAME,
            'bank_code': self.BANK_CODE,
            'account_info': account_info,
            'period': period,
            'transactions': transactions,
        }

    def _extract_detailed_account_info(self, raw_text: str) -> Dict:
        info = {'account_number': '', 'account_holder': '', 'branch': '', 'ifsc': '', 'type': ''}
        m = re.search(r'Account:\s*(\d+)\|([^|]+)\|([^|]+)\|(\S+)', raw_text)
        if m:
            info['account_number'] = m.group(1)
            info['account_holder'] = m.group(2).strip()
            info['type'] = m.group(3).strip()
            info['branch'] = m.group(4).strip()
        return info

    def _extract_detailed_period(self, raw_text: str) -> str:
        m = re.search(
            r'From:\s*\(dd/MM/yyyy\):\s*(\d{2}/\d{2}/\d{4})\s*To\s*(\d{2}/\d{2}/\d{4})',
            raw_text, re.IGNORECASE
        )
        return f"{m.group(1)} to {m.group(2)}" if m else "N/A"
    
    def _extract_account_info(self, text: str) -> Dict:
        """Extract account metadata from the first page header."""
        info = {
            'account_number': '',
            'account_holder': '',
            'branch': '',
            'ifsc': '',
            'type': '',
        }
        
        for line in text.split('\n')[:30]:
            line_s = line.strip()
            
            m = re.search(r'A/C NO:\s*([\d]+)', line_s)
            if m:
                info['account_number'] = m.group(1)
            
            m = re.search(r'IFSC Code\s*:\s*(\S+)', line_s)
            if m:
                info['ifsc'] = m.group(1).rstrip(',')
            
            m = re.search(r'TYPE:\s*(.+?)DATE:', line_s)
            if m:
                info['type'] = m.group(1).strip()
            
            if line_s.startswith('M/S'):
                info['account_holder'] = re.sub(r'^M/S\.+\s*', '', line_s).strip()
            
            if 'KHONMOH' in line_s and 'SRINAGAR' in line_s:
                info['branch'] = line_s
        
        return info
    
    def _extract_period(self, text: str) -> str:
        """Extract the statement period string."""
        m = re.search(
            r'STATEMENT OF ACCOUNT FOR THE PERIOD OF\s+(\d{2}-\d{2}-\d{4})\s+to\s+(\d{2}-\d{2}-\d{4})',
            text
        )
        return f"{m.group(1)} to {m.group(2)}" if m else "N/A"
    
    def _should_skip(self, line: str) -> bool:
        """Return True if line is a header/footer/separator (not transaction data)."""
        for pat in self.SKIP_PATTERNS:
            if pat.search(line):
                return True
        return False
    
    def _detect_format(self, raw_text: str) -> str:
        """
        Detect whether this is a wide-format (90+ cols) or narrow-format statement.
        
        Narrow format appears in CC (Cash Credit) statements where pdfplumber
        extracts shorter lines (~40-50 chars) with amounts at variable positions.
        Wide format is the standard savings/current account format with fixed columns.
        """
        lines = raw_text.split('\n')
        # Check the average length of data lines (non-header, non-empty)
        data_lengths = []
        for line in lines[:200]:
            stripped = line.rstrip()
            if stripped and not self._should_skip(stripped) and len(stripped) > 15:
                data_lengths.append(len(stripped))
        
        if not data_lengths:
            return 'wide'
        
        avg_len = sum(data_lengths) / len(data_lengths)
        return 'narrow' if avg_len < 65 else 'wide'
    
    def _parse_line(self, line: str, fmt: str = 'wide') -> Dict:
        """
        Parse a single line from a JK Bank statement.
        
        Supports two formats:
          - 'wide': Fixed column positions (standard savings/current account)
          - 'narrow': Variable positions (CC/Cash Credit accounts)
        
        Returns dict with: date, particulars, chq_ref, withdrawal, deposit, balance, _amount
        """
        result = {
            'date': '',
            'particulars': '',
            'chq_ref': '',
            'withdrawal': '',
            'deposit': '',
            'balance': '',
            '_amount': '',  # Raw amount (classified later for narrow format)
        }
        
        if not line or not line.strip():
            return result
        
        # 1. Extract date (if present)
        dm = self.DATE_RE.match(line)
        if dm:
            result['date'] = dm.group(1)
        
        # 2. Extract balance (last amount ending with Dr/Cr)
        bm = self.BALANCE_RE.search(line)
        if bm:
            result['balance'] = bm.group(1) + bm.group(2)
            # Remove balance from line for cleaner parsing
            line_no_bal = line[:bm.start()].rstrip()
        else:
            line_no_bal = line.rstrip()
        
        # 3. Find all amounts in the line (excluding balance)
        amounts = []
        for am in self.AMOUNT_RE.finditer(line_no_bal):
            # Skip if this is part of the date
            if dm and am.start() < dm.end():
                continue
            amounts.append((am.start(), am.group()))
        
        # 4. Classify amounts
        if fmt == 'wide':
            # Wide format: fixed column positions
            # Withdrawals: cols 51-63, Deposits: cols 64+
            for pos, val in amounts:
                if pos >= 64:
                    result['deposit'] = val
                elif pos >= 51:
                    result['withdrawal'] = val
        else:
            # Narrow format: amounts at variable positions
            # Store the first non-balance amount as _amount
            # W vs D will be determined later by comparing balances
            if amounts:
                result['_amount'] = amounts[0][1]
        
        # 5. Extract text regions
        if dm:
            text_start = dm.end()
        else:
            text_start = 0
        
        # Find where the first amount starts (or end of no-balance line)
        first_amount_pos = len(line_no_bal)
        for pos, val in amounts:
            if pos < first_amount_pos:
                first_amount_pos = pos
        
        # The text region is everything from text_start to first_amount_pos
        text_region = line_no_bal[text_start:first_amount_pos]
        
        # Split text region into particulars and chq/ref using column boundary
        chq_col_start = 32
        
        if fmt == 'wide' and text_start < chq_col_start and first_amount_pos > chq_col_start:
            # Wide format: split at fixed column boundary
            part_text = line_no_bal[text_start:chq_col_start].strip()
            chq_text = line_no_bal[chq_col_start:first_amount_pos].strip()
            
            # Validate chq_ref: should contain digits to be a reference number
            if chq_text and re.search(r'\d', chq_text):
                result['particulars'] = part_text
                result['chq_ref'] = chq_text
            else:
                result['particulars'] = text_region.strip()
        else:
            result['particulars'] = text_region.strip()
        
        return result
    
    def _parse_transactions(self, raw_text: str) -> List[Dict]:
        """
        Parse all transactions from the raw text.
        
        Algorithm:
        1. Detect format (wide vs narrow)
        2. Split into lines, filter headers/footers
        3. Group consecutive lines into transactions
        4. Merge each group into a single transaction
        5. Handle page-break continuations
        6. For narrow format: classify amounts as W/D using balance comparison
        """
        fmt = self._detect_format(raw_text)
        all_lines = raw_text.split('\n')
        
        # Filter to transaction data lines only
        data_lines = []
        for line in all_lines:
            line = line.rstrip('\r\n')
            if not self._should_skip(line):
                data_lines.append(line)
        
        # Group into transaction blocks
        groups = []  # List of (is_dated, [parsed_lines])
        current_group = []
        current_has_date = False
        
        for line in data_lines:
            parsed = self._parse_line(line, fmt)
            has_date = bool(parsed['date'])
            is_bf = bool(self.BF_RE.match(line))
            
            if has_date or is_bf:
                # Save previous group
                if current_group:
                    groups.append((current_has_date, current_group))
                current_group = [parsed]
                current_has_date = has_date or is_bf
                
                if is_bf:
                    # Parse B/F specially
                    bm = self.BF_RE.match(line)
                    bal_m = self.BALANCE_RE.search(bm.group(1))
                    current_group = [{
                        'date': '',
                        'particulars': 'B/F (Brought Forward)',
                        'chq_ref': '',
                        'withdrawal': '',
                        'deposit': '',
                        'balance': bal_m.group(0).strip() if bal_m else bm.group(1).strip(),
                        '_amount': '',
                    }]
            else:
                # Continuation line
                current_group.append(parsed)
        
        if current_group:
            groups.append((current_has_date, current_group))
        
        # Merge each group into a single transaction
        transactions = []
        for has_date, group in groups:
            txn = self._merge_group(group, has_date)
            if txn:
                transactions.append(txn)
        
        # Handle orphan groups (no date = continuation from previous page)
        merged = []
        for txn in transactions:
            if not txn['_has_date'] and merged:
                # Merge into previous transaction
                prev = merged[-1]
                if txn['particulars']:
                    prev['particulars'] = (prev['particulars'] + ' ' + txn['particulars']).strip()
                if txn['chq_ref']:
                    prev['chq_ref'] = (prev['chq_ref'] + ' ' + txn['chq_ref']).strip() if prev['chq_ref'] else txn['chq_ref']
                if txn['withdrawal'] and not prev['withdrawal']:
                    prev['withdrawal'] = txn['withdrawal']
                if txn['deposit'] and not prev['deposit']:
                    prev['deposit'] = txn['deposit']
                if txn.get('_amount') and not prev.get('_amount'):
                    prev['_amount'] = txn['_amount']
                if txn['balance']:
                    prev['balance'] = txn['balance']
            else:
                merged.append(txn)
        
        # For narrow format: classify _amount as withdrawal or deposit
        # by comparing consecutive balances
        if fmt == 'narrow':
            self._classify_amounts_by_balance(merged)
        
        # Clean up: remove internal flags and normalize spaces
        for txn in merged:
            txn.pop('_has_date', None)
            txn.pop('_amount', None)
            txn['particulars'] = re.sub(r'\s{2,}', ' ', txn['particulars']).strip()
            txn['chq_ref'] = re.sub(r'\s{2,}', ' ', txn['chq_ref']).strip()
        
        return merged
    
    def _classify_amounts_by_balance(self, transactions: List[Dict]):
        """
        For narrow-format statements, determine withdrawal vs deposit
        by comparing consecutive balances.
        
        Logic:
          - Parse balance to numeric (handle Dr/Cr suffix)
          - If balance increased (Dr went up, or Cr went down), it's a withdrawal
          - If balance decreased (Dr went down, or Cr went up), it's a deposit
        """
        def parse_balance(bal_str: str) -> Optional[float]:
            if not bal_str:
                return None
            m = re.match(r'([\d,]+\.\d{2})(Dr|Cr)$', bal_str)
            if not m:
                return None
            val = float(m.group(1).replace(',', ''))
            # Dr = debit balance (loan outstanding), Cr = credit balance
            return val if m.group(2) == 'Dr' else -val
        
        prev_balance = None
        for txn in transactions:
            curr_balance = parse_balance(txn['balance'])
            amount_str = txn.get('_amount', '')
            
            if amount_str and curr_balance is not None and prev_balance is not None:
                # Compare balances to determine direction
                diff = curr_balance - prev_balance
                
                if diff > 0:
                    # Balance (Dr) increased = withdrawal/debit
                    txn['withdrawal'] = amount_str
                elif diff < 0:
                    # Balance (Dr) decreased = deposit/credit  
                    txn['deposit'] = amount_str
                else:
                    # No change — unusual, put as withdrawal by default
                    txn['withdrawal'] = amount_str
            elif amount_str and not txn['withdrawal'] and not txn['deposit']:
                # No previous balance to compare — try heuristics
                # If particulars contain 'CR' or 'By Cash' or 'UPI.*CR' -> likely deposit
                part = txn.get('particulars', '').upper()
                if any(kw in part for kw in ['/CR/', 'BY CASH', 'BY CLG', 'NEFT-', 'RTGS-']):
                    txn['deposit'] = amount_str
                else:
                    txn['withdrawal'] = amount_str
            
            if curr_balance is not None:
                prev_balance = curr_balance
    
    def _merge_group(self, group: List[Dict], has_date: bool) -> Optional[Dict]:
        """Merge a group of parsed lines into one transaction."""
        if not group:
            return None
        
        txn = {
            'date': '',
            'particulars': '',
            'chq_ref': '',
            'withdrawal': '',
            'deposit': '',
            'balance': '',
            '_has_date': has_date,
            '_amount': '',  # For narrow format: raw amount before W/D classification
        }
        
        parts = []
        refs = []
        
        for line in group:
            if line['date'] and not txn['date']:
                txn['date'] = line['date']
            
            if line['particulars']:
                parts.append(line['particulars'])
            
            if line['chq_ref']:
                refs.append(line['chq_ref'])
            
            if line['withdrawal'] and not txn['withdrawal']:
                txn['withdrawal'] = line['withdrawal']
            
            if line['deposit'] and not txn['deposit']:
                txn['deposit'] = line['deposit']
            
            if line.get('_amount') and not txn['_amount']:
                txn['_amount'] = line['_amount']
            
            if line['balance']:
                txn['balance'] = line['balance']
        
        txn['particulars'] = ' '.join(parts)
        txn['chq_ref'] = ' '.join(refs)
        
        # Skip if it's just empty
        if not txn['particulars'] and not txn['balance'] and not txn['withdrawal'] and not txn['deposit'] and not txn['_amount']:
            return None
        
        return txn
