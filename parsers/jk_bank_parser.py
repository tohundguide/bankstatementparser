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
from typing import Dict, List, Optional, Tuple
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
        (r"\bJAKA0\w{5,6}\b", 10, True),
        # OCR'd statements: Tesseract reads the "0" in JAKA0… as a letter O.
        (r"\bJAKA[0O][A-Z0-9]{5,6}\b", 10, True),
        ("jkbank.com", 10, False),
        # Column header of the branch-printed "STATEMENT OF ACCOUNT FOR THE
        # PERIOD FROM … TO …" layout (punctuation optional — OCR adds dots).
        (r"DATE\s+PARTICULARS[.:]?\s+CHQ[.:]?\s*NO[.:]?\s+WITHDRAWALS[.:]?\s+DEPOSITS[.:]?\s+BALANCE", 3, True),
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
        if self._is_period_format(raw_text):
            return self._parse_period(raw_text)

        account_info = self._extract_account_info(raw_text)
        period = self._extract_period(raw_text)
        transactions = self._parse_transactions(raw_text)

        if not transactions:
            # Fixed-width parser found nothing — the text may be an OCR'd
            # branch statement whose header did not survive recognition.
            alt = self._parse_period(raw_text)
            if alt['transactions']:
                return alt

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
    
    # ──────────────────────────────────────────────────────────────────
    # "STATEMENT OF ACCOUNT FOR THE PERIOD FROM … TO …" format
    # ──────────────────────────────────────────────────────────────────
    #
    # The branch-printed Current / Cash-Credit account statement. Each page
    # repeats a header (IFSC, branch, customer block) and the column header
    #   DATE | PARTICULARS | CHQ.NO. | WITHDRAWALS | DEPOSITS | BALANCE
    # then rows, a "Page Total :" line, and finally "Grand Total :".
    # Every row is one line with ONE amount (withdrawal OR deposit) and a
    # Dr/Cr balance:
    #
    #   09-10-2025 mTFR/9419034560/INFINITY CLOTHING 33,918.00 -33,911.00Dr
    #
    # Which column the amount sat in is not recoverable from the text, so
    # direction is derived from the running balance. Long narrations wrap
    # onto a second line; when the amounts are printed between the two
    # lines, OCR may attach them to the continuation line instead.
    #
    # These statements frequently arrive re-printed through "Microsoft Print
    # to PDF", which turns the text into vector outlines — no text layer —
    # so the text comes from Tesseract with its usual damage: a leading "1"
    # read as "4" (1,400 -> 41,400), "," and "." swapped, "-" read as "~",
    # the "0" in the IFSC read as "O", duplicated digit fragments on the
    # wrapped line. Nothing here trusts column position or a single number:
    # amounts and balances are settled against each other by
    # parsers/ocr_reconcile.py, and every change is reported.

    _PRD_HDR_RE = re.compile(r'STATEMENT OF ACCOUNT FOR THE PERIOD FROM', re.IGNORECASE)
    _PRD_DATE_RE = re.compile(r'^(\d{2}-\d{2}-\d{4})\b[\s:.,]*(.*)$')
    # Dr/Cr as OCR renders them: Dr, DR, Or, 0r, De, Dc, D¢, DF, Cr, CR, Ce …
    _PRD_BAL_RE = re.compile(
        r'([-~—–_"“”\'`]?\s?\d[\d,.]*[.,:]\d{2})\s*'
        r'(Dr|DR|Or|0r|D[a-z¢€£]|Cr|CR|C[a-z])\b\.?\s*$'
    )
    # Any money-looking token; used when the Dr/Cr suffix did not survive OCR.
    _PRD_MONEYISH_RE = re.compile(r'[-~—–_"“”\'`]?\s?\d[\d,.]*[.,:]\d{2}')
    _PRD_TRAIL_RE = re.compile(r'([-~—–_"“”\'`]?\s?\d[\d,.]*[.,:]\d{2})\s*(\S{0,4})\s*$')
    _PRD_AMT_RE = re.compile(r'(?<![\d/:])\d{1,3}(?:[,.]\d{2,3})*[.,]\d{2}(?![\d/])')
    _PRD_BLOCK_START_RE = re.compile(r'^(IFSC\b|Page\s*Total\b)', re.IGNORECASE)
    _PRD_COLHDR_RE = re.compile(r'DATE\s+PARTICULARS', re.IGNORECASE)
    _PRD_GRAND_RE = re.compile(r'^Grand\s*Total\b', re.IGNORECASE)
    _PRD_SKIP_RE = re.compile(
        r'(CUSTOMER\s*ID|A[/I1|\\]?C\s*NO|^TYPE\s*:|^PIN\s*:|CURRENCY\s*CODE|@|'
        r'STATEMENT OF ACCOUNT|^Page\s+\S+\s*(of|0f)\b|system generated|'
        r'^Date/Time|JAMMU AND KASHMIR|^[SDW$]/O\b|^C/A\b|PAGE\s*:\s*\d|'
        r'^[^A-Za-z0-9]*$)',
        re.IGNORECASE
    )
    _PRD_CHQ_RE = re.compile(r'\s(\d{6})$')

    def _is_period_format(self, raw_text: str) -> bool:
        return bool(self._PRD_HDR_RE.search(raw_text))

    @staticmethod
    def _prd_money(tok: str) -> Optional[float]:
        """'1,04,405.00' / '4,00' / '1.59,465.74' -> float; the last two digits are paise."""
        d = ''.join(ch for ch in tok if ch.isdigit())
        if len(d) < 3:
            return None
        return float(d[:-2] + '.' + d[-2:])

    @staticmethod
    def _prd_pick_amount(matches):
        """Among amount-looking tokens on a line, the transaction amount is the
        one that looks most like money: a proper '.dd' decimal beats a comma
        decimal, more digits beat fewer, and later beats earlier (nearest the
        balance column). Guards against fragments of a garbled balance such as
        the '4,49' inside '4,49,327:7408'."""
        def score(item):
            idx, m = item
            tok = m.group(0)
            digits = sum(ch.isdigit() for ch in tok)
            return (2 if tok[-3] == '.' else 0) + (1 if digits >= 3 else 0), idx
        return max(enumerate(matches), key=score)[1]

    def _prd_balance(self, bm) -> Optional[float]:
        val = self._prd_money(bm.group(1))
        if val is None:
            return None
        neg = bm.group(2).upper()[0] in 'DO0'
        self._prd_last_neg = neg
        return -val if neg else val

    def _prd_trailing_balance(self, text: str):
        """When no Dr/Cr suffix survived, the last money-looking token on a line
        that carries at least two of them is the balance. Its sign comes from a
        leading minus, from whatever junk follows it (D… / C…), else from the
        last balance seen — an overdrawn account stays overdrawn."""
        if len(self._PRD_MONEYISH_RE.findall(text)) < 2:
            return None
        m = self._PRD_TRAIL_RE.search(text)
        if not m:
            return None
        tok, junk = m.group(1).strip(), m.group(2)
        val = self._prd_money(tok)
        if val is None:
            return None
        if tok[0] in '-~—–_"“”\'`' or junk[:1].upper() in ('D', 'O', '0'):
            neg = True
        elif junk[:1].upper() == 'C':
            neg = False
        else:
            neg = self._prd_last_neg
        return m, tok, (-val if neg else val)

    def _prd_take_numbers(self, row: Dict, text: str, primary: bool) -> None:
        """Pull the balance / amount out of a row line (or its continuation) and
        append what is left to the narration."""
        bm = self._PRD_BAL_RE.search(text)
        if bm:
            if row['balance'] is None:
                row['balance_raw'] = bm.group(1).strip()
                row['balance'] = self._prd_balance(bm)
            # (a balance seen again on a continuation line is a duplicated fragment)
            text = text[:bm.start()]
        elif row['balance'] is None:
            tb = self._prd_trailing_balance(text)
            if tb:
                m, tok, val = tb
                row['balance_raw'] = tok
                row['balance'] = val
                text = text[:m.start()]

        amts = list(self._PRD_AMT_RE.finditer(text))
        if amts and row['amount'] is None:
            m = self._prd_pick_amount(amts)
            row['amount_raw'] = m.group(0)
            row['amount'] = self._prd_money(m.group(0))
            # Narration always precedes the amount; whatever follows it is a
            # garbled balance or duplicated digit fragments.
            text = text[:m.start()]
        else:
            text = self._PRD_AMT_RE.sub(' ', text)

        if not primary:
            # Wrapped-line debris: OCR re-reads half-height digit fragments of
            # the amounts ("000. 14,205."); real wrapped narration is
            # upper-case words, slashes or digit runs (phone / ref numbers).
            keep = []
            for tok in text.split():
                core = tok.strip('.,:;-_|')
                if not core:
                    continue
                if '/' in core or re.fullmatch(r"[A-Z][A-Z0-9&.'-]*", core) \
                        or re.fullmatch(r"\d+-\d+", core):
                    keep.append(tok)
            text = ' '.join(keep)

        text = re.sub(r'\s{2,}', ' ', text).strip(' .,:;-_|')
        if text:
            row['particulars'] = (row['particulars'] + ' ' + text).strip()

    def _prd_page_totals(self, line: str) -> Optional[Tuple[float, float]]:
        """'Page Total : 123,178.00 18,773.00 -1,04,405.00Dr' -> (withdrawals, deposits);
        None when a column is blank (one amount only — cannot tell which)."""
        text = line
        bm = self._PRD_BAL_RE.search(text)
        if bm:
            text = text[:bm.start()]
        vals = [v for v in (self._prd_money(t) for t in self._PRD_AMT_RE.findall(text)) if v is not None]
        return (vals[0], vals[1]) if len(vals) == 2 else None

    def _prd_direction_guess(self, particulars: str, amount: float, balance: float) -> str:
        """Direction for a row the balance chain cannot settle (the first row)."""
        p = particulars.upper()
        if '/CR/' in p or p.startswith('BY ') or 'CREDIT' in p:
            return 'deposit'
        if any(k in p for k in ('INT.COLL', 'CHARGES', 'SMS ', '/DR/', ' FEE', 'GST')):
            return 'withdrawal'
        if balance > 0 and abs(balance - amount) <= 0.011:
            return 'deposit'          # first credit into a fresh account
        if balance < 0 and abs(-balance - amount) <= 0.011:
            return 'withdrawal'
        return 'withdrawal' if balance < 0 else 'deposit'

    def _parse_period(self, raw_text: str) -> Dict:
        from parsers.ocr_reconcile import reconcile

        account_info = self._extract_period_account_info(raw_text)
        period = self._extract_period_range(raw_text)

        rows: List[Dict] = []
        current: Optional[Dict] = None
        in_block = True        # inside a page header/footer: nothing is narration
        grand: List[float] = []
        page, page_has_rows = 0, False
        page_totals: Dict[int, Tuple[float, float]] = {}
        self._prd_last_neg = False

        for raw_line in raw_text.split('\n'):
            s = raw_line.strip()
            if not s:
                continue
            if self._PRD_GRAND_RE.match(s):
                grand = [v for v in (self._prd_money(t) for t in self._PRD_AMT_RE.findall(s)) if v is not None]
                current, in_block = None, True
                continue
            if self._PRD_BLOCK_START_RE.match(s):
                current, in_block = None, True
                if s.upper().startswith('PAGE'):
                    totals = self._prd_page_totals(s)
                    if totals:
                        page_totals[page] = totals
                elif page_has_rows:          # next page's header block
                    page, page_has_rows = page + 1, False
                continue
            if self._PRD_COLHDR_RE.search(s):
                current, in_block = None, False
                if page_has_rows:            # header survived but IFSC line did not
                    page, page_has_rows = page + 1, False
                continue
            dm = self._PRD_DATE_RE.match(s)
            if dm:
                in_block = False
                current = {
                    'date': dm.group(1), 'particulars': '', 'chq_ref': '',
                    'amount': None, 'amount_raw': None,
                    'balance': None, 'balance_raw': None, 'page': page,
                }
                self._prd_take_numbers(current, dm.group(2), primary=True)
                rows.append(current)
                page_has_rows = True
                continue
            if in_block or current is None or self._PRD_SKIP_RE.search(s):
                continue
            self._prd_take_numbers(current, s, primary=False)

        rec = reconcile([
            {k: r[k] for k in ('amount', 'amount_raw', 'balance', 'balance_raw', 'page')}
            for r in rows
        ], page_totals)

        transactions: List[Dict] = []
        corrections: List[Dict] = []
        total_w = total_d = 0.0
        for idx, (row, rc) in enumerate(zip(rows, rec)):
            particulars = re.sub(r'\s{2,}', ' ', row['particulars']).strip()
            chq = ''
            cm = self._PRD_CHQ_RE.search(particulars)
            if cm:
                chq = cm.group(1)
                particulars = particulars[:cm.start()].strip()

            amount, balance = rc['amount'], rc['balance']
            direction = rc['direction'] or self._prd_direction_guess(particulars, amount, balance)
            amt_s = f"{amount:.2f}" if amount > 0.011 else ''
            withdrawal = amt_s if direction == 'withdrawal' else ''
            deposit = amt_s if direction == 'deposit' else ''
            total_w += float(withdrawal or 0)
            total_d += float(deposit or 0)

            transactions.append({
                'date': row['date'],
                'particulars': particulars,
                'chq_ref': chq,
                'withdrawal': withdrawal,
                'deposit': deposit,
                'balance': f"{balance:.2f}",
            })
            for field, read, used in rc['corrections']:
                corrections.append({
                    'row': idx + 1, 'date': row['date'], 'field': field,
                    'read': read, 'used': used, 'particulars': particulars[:40],
                })

        warnings: List[str] = []
        if corrections:
            warnings.append(
                f"{len(corrections)} value(s) could not be read cleanly from the scan "
                f"and were reconciled against the running balance"
            )
        if len(grand) >= 3:
            gw, gd = grand[0], grand[1]
            if abs(gw - total_w) > 0.011 or abs(gd - total_d) > 0.011:
                warnings.append(
                    f"Grand Total printed on the statement (withdrawals {gw:,.2f}, deposits "
                    f"{gd:,.2f}) differs from the parsed totals (withdrawals "
                    f"{total_w:,.2f}, deposits {total_d:,.2f}) — review the flagged rows"
                )

        result = {
            'bank_name': self.BANK_NAME,
            'bank_code': self.BANK_CODE,
            'account_info': account_info,
            'period': period,
            'transactions': transactions,
        }
        if corrections:
            result['ocr_corrections'] = corrections
        if warnings:
            result['warnings'] = warnings
            result['notes'] = '; '.join(warnings)
        return result

    def _extract_period_account_info(self, raw_text: str) -> Dict:
        info = {
            'account_number': '', 'account_holder': '', 'branch': '',
            'ifsc': '', 'type': '', 'customer_id': '',
        }
        lines = [l.strip() for l in raw_text.split('\n')]
        head = lines[:60]
        for idx, l in enumerate(head):
            if not info['ifsc']:
                m = re.search(r'IFSC\s*:?\s*\|?\s*([A-Z0-9]{10,11})\b', l, re.IGNORECASE)
                if m:
                    code = m.group(1).upper()
                    if code[4] == 'O':
                        code = code[:4] + '0' + code[5:]     # OCR: letter O for zero
                    info['ifsc'] = code
                    for nl in head[idx + 1: idx + 4]:
                        if nl and re.search(r'[A-Za-z]{3}', nl) and \
                                not re.match(r'^(M/?S\b|MR\b|MRS\b|SHRI\b|SMT\b)', nl, re.IGNORECASE):
                            info['branch'] = nl
                            break
            if not info['account_number']:
                m = re.search(r'A[/I1|\\]?C\s*NO\s*:?\s*(\d{10,20})', l, re.IGNORECASE)
                if m:
                    info['account_number'] = m.group(1)
            if not info['customer_id']:
                m = re.search(r'CUSTOMER\s*ID\s*:?\s*(\d{4,})', l, re.IGNORECASE)
                if m:
                    info['customer_id'] = m.group(1)
            if not info['type']:
                m = re.match(r'^TYPE\s*:\s*(.+)$', l, re.IGNORECASE)
                if m and 'DATE' not in m.group(1).upper():
                    info['type'] = m.group(1).strip()
            if not info['account_holder']:
                if re.match(r'^(M/?S\.?|MR\.?|MRS\.?|SHRI|SMT|DR\.?)\s+\S', l, re.IGNORECASE):
                    info['account_holder'] = re.split(r'\s+DATE\s*:', l, flags=re.IGNORECASE)[0].strip()
        if not info['account_holder']:
            for idx, l in enumerate(head):
                if re.match(r'^[SDW$]/O\b', l, re.IGNORECASE) and idx > 0:
                    prev = next((p for p in reversed(head[:idx]) if p), '')
                    if prev and not re.match(r'^(IFSC|Main)', prev, re.IGNORECASE):
                        info['account_holder'] = prev
                    break
        return info

    def _extract_period_range(self, raw_text: str) -> str:
        m = re.search(
            r'PERIOD\s+FROM\s*(\d{2}-\d{2}-\d{4})\s*TO\s*(\d{2}-\d{2}-\d{4})',
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
