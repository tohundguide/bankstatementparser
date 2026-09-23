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


    # ── 2025 layout ("Account Statement" / "<Type> Account Transactions") ──
    #   # Date Description Chq/Ref. No. Withdrawal (Dr.) Deposit (Cr.) Balance
    #   - - Opening Balance - - - 0.00
    #   1 09 May 2025 Recd:IMPS/512925179801/MEMO APPS IMPS-512912006657 2,000.00 2,000.00
    #   /KKBK/X4606/Donat                                  <- wrapped description
    # One amount per row (Withdrawal OR Deposit, never tagged), then the
    # balance. The older layout above tags every amount "CR"/"DR", which is
    # all the original parser looks for — so on this layout it found 0 rows.
    NEW_ROW_RE = re.compile(
        r'^(\d{1,5})\s+(\d{1,2}\s+[A-Z][a-z]{2}\s+\d{4})\s+(.*?)\s*'
        r'(-?[\d,]+\.\d{2})\s+(-?[\d,]+\.\d{2})\s*$'
    )
    NEW_ROW_NO_AMOUNTS_RE = re.compile(r'^(\d{1,5})\s+(\d{1,2}\s+[A-Z][a-z]{2}\s+\d{4})\s+(.*)$')
    AMOUNTS_RE = re.compile(r'(-?[\d,]+\.\d{2})\s+(-?[\d,]+\.\d{2})\s*$')
    # The Chq/Ref column: "UPI-513124703374", "IMPS-...", "NEFTINW-...", "TBMS-...",
    # "SUN-5931910", "1/NCRCTS_140520255", or a bare long number. A value-date
    # note can sit right before it with no space: "...(Value Date: 06-08-2025)TBMS-1733467083".
    NEW_REF_RE = re.compile(
        r'(?:^|\s|(?<=\)))([A-Z][A-Z0-9]{1,14}-[A-Z0-9]{4,}|\d{1,3}/[A-Z]+_\d+|\d{9,})$'
    )
    MONTHS = {m: f'{i:02d}' for i, m in enumerate(
        ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'], 1)}

    def _is_new_format(self, raw_text: str) -> bool:
        return (re.search(r'Withdrawal\s*\(Dr\.?\)\s+Deposit\s*\(Cr\.?\)\s+Balance', raw_text) is not None
                or len(self.NEW_ROW_RE.findall(raw_text[:20000])) >= 2
                or sum(1 for l in raw_text.split('\n')[:400] if self.NEW_ROW_RE.match(l.strip())) >= 2)

    def _parse_new_format(self, raw_text: str) -> Dict:
        lines = raw_text.split('\n')

        holder = ''
        m = re.search(r'^(.+?)\s+Account\s+No\.\s*\d+', raw_text, re.MULTILINE)
        if m and not re.match(r'^\s*Account\b', m.group(1)):
            holder = m.group(1).strip()
        acct = re.search(r'Account\s+No\.\s*(\d+)', raw_text)
        ifsc = re.search(r'IFSC\s+Code\s+(KKBK\w+)', raw_text)
        branch = re.search(r'^Branch\s+(?!Phone)(.+)$', raw_text, re.MULTILINE)
        acc_type = re.search(r'Account\s+Type\s+(\w+)', raw_text)
        per = re.search(r'(\d{1,2}\s+[A-Z][a-z]{2}\s+\d{4})\s+-\s+(\d{1,2}\s+[A-Z][a-z]{2}\s+\d{4})', raw_text)
        account_info = {
            'account_number': acct.group(1) if acct else '',
            'account_holder': holder,
            'branch': branch.group(1).strip() if branch else '',
            'ifsc': ifsc.group(1) if ifsc else '',
            'type': acc_type.group(1) if acc_type else '',
        }
        period = f"{self._normalize_date(per.group(1))} to {self._normalize_date(per.group(2))}" if per else 'N/A'

        ob = re.search(r'Opening\s+Balance[\s-]*(-?[\d,]+\.\d{2})\s*$', raw_text, re.MULTILINE)
        prev_bal: Optional[float] = float(ob.group(1).replace(',', '')) if ob else None

        # Lines repeated at the top/bottom of every page.
        page_noise = [
            re.compile(r'^Statement\s+Generated\s+on\b', re.I),
            re.compile(r'^Account\s+(No\.|Statement)', re.I),
            re.compile(r'^\w+(\s+\w+)?\s+Account\s+Transactions\s*$', re.I),
            re.compile(r'^#\s+Date\s+Description', re.I),
            re.compile(r'^[-\s]*Opening\s+Balance\b', re.I),
            re.compile(r'^Page\s+\d+\s+of\s+\d+', re.I),
        ]
        end_re = re.compile(r'^(Account\s+Summary|End\s+of\s+Statement)\b', re.I)

        transactions: List[Dict] = []
        txn: Optional[Dict] = None

        def set_amounts(t: Dict, amount_s: str, balance_s: str) -> None:
            nonlocal prev_bal
            amount = amount_s.replace(',', '').lstrip('-')
            balance = float(balance_s.replace(',', ''))
            if prev_bal is not None and balance > prev_bal + 1e-9:
                t['deposit'] = amount
            else:
                t['withdrawal'] = amount
            t['balance'] = f'{balance:.2f}'
            prev_bal = balance

        def split_ref(t: Dict, text: str) -> str:
            rm = self.NEW_REF_RE.search(text)
            if rm and rm.start(1) > 0:
                t['chq_ref'] = rm.group(1)
                return text[:rm.start(1)].strip()
            return text

        started = False
        for line in lines:
            s = line.strip()
            if not s:
                continue
            if end_re.match(s):
                break
            if re.match(r'^#\s+Date\s+Description', s, re.I):
                started = True
                continue
            if not started:
                continue
            if any(p.match(s) for p in page_noise) or (holder and s == holder):
                continue

            rm = self.NEW_ROW_RE.match(s)
            nm = rm or self.NEW_ROW_NO_AMOUNTS_RE.match(s)
            if nm:
                if txn:
                    transactions.append(txn)
                txn = {'date': self._normalize_date(nm.group(2)), 'particulars': '',
                       'chq_ref': '', 'withdrawal': '', 'deposit': '', 'balance': ''}
                if rm:
                    set_amounts(txn, rm.group(4), rm.group(5))
                txn['particulars'] = split_ref(txn, nm.group(3).strip())
                continue

            if txn is None:
                continue
            # Amounts that wrapped below their row.
            if not txn['balance']:
                am = self.AMOUNTS_RE.search(s)
                if am:
                    set_amounts(txn, am.group(1), am.group(2))
                    s = split_ref(txn, s[:am.start()].strip()) if not txn['chq_ref'] else s[:am.start()].strip()
            # The ref column wraps too: "1/NCRCTS_140520255" + "3468".
            if re.fullmatch(r'\d{1,8}', s) and '_' in txn['chq_ref']:
                txn['chq_ref'] += s
                continue
            if s:
                txn['particulars'] += ' ' + s

        if txn:
            transactions.append(txn)
        for t in transactions:
            t['particulars'] = re.sub(r'\s+', ' ', t['particulars']).strip()

        return {
            'bank_name': self.BANK_NAME,
            'bank_code': self.BANK_CODE,
            'account_info': account_info,
            'period': period,
            'transactions': transactions,
        }

    def parse(self, raw_text: str) -> Dict:
        """Parse Kotak Bank statement text into structured data."""
        if self._is_new_format(raw_text):
            return self._parse_new_format(raw_text)

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
        """Convert DD/MM/YYYY or 'DD Mon YYYY' (2025 layout) to DD-MM-YYYY."""
        if not date_str:
            return ''
        m = re.match(r'^(\d{1,2})\s+([A-Z][a-z]{2})\s+(\d{4})$', date_str.strip())
        if m and m.group(2) in self.MONTHS:
            return f"{m.group(1).zfill(2)}-{self.MONTHS[m.group(2)]}-{m.group(3)}"
        return date_str.replace('/', '-')
