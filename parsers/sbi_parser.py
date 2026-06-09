"""
SBI (State Bank of India) Parser
=================================
Parses SBI bank statements from the real Finacle/CBI layout.

Real SBI Column Layout (from layout-mode extraction):
    Value Date   Cheque
    Post Date    Description              Debit        Credit       Balance
                                          No/Reference

Each transaction spans TWO lines:
    Line 1 (Value Date): DD-MM-YYYY  DESCRIPTION_TEXT                [AMT_IN_DEBIT_COL]  [AMT_IN_CREDIT_COL]  [BALANCE_COL]
    Line 2 (Post Date):  DD-MM-YYYY  [DESC_OVERFLOW]      [REF]     [AMT_IN_DEBIT_COL]  [AMT_IN_CREDIT_COL]  BALANCE[CR/DR]
    Line 3+: Continuation (ref numbers, PAN, etc.)

The key insight: amounts are positionally aligned to Debit/Credit/Balance columns.
Using layout=True in pdfplumber preserves this column alignment.
"""

import re
from typing import Dict, List, Optional
from parsers.base_parser import BaseBankParser


class SBIParser(BaseBankParser):
    """Parser for State Bank of India (SBI) statements."""

    BANK_CODE = "sbi"
    BANK_NAME = "State Bank of India"
    DETECTION_KEYWORDS = [
        "SBIN0", "STATE BANK OF INDIA", "State Bank of India",
        "onlinesbi", "STATEMENT OF ACCOUNT", "IFS Code",
    ]
    DETECTION_RULES = [
        (r"\bSBIN0\w{6}\b", 10, True),            # IFSC anchor
        ("sbi.co.in", 10, False),                   # Domain anchor
        ("STATE BANK OF INDIA", 3, False),          # Strong: bank name
        ("STATEMENT OF ACCOUNT", 3, False),         # Strong: header
        ("BROUGHT FORWARD", 3, False),              # Strong: SBI-specific marker
        ("MICR Code", 1, False),                    # Weak
        ("CIF No", 1, False),                       # Weak
    ]
    NEGATIVE_RULES = [
        (r"\bBARB0\w{6}\b", -8, True),
        (r"\bUBIN0\w{6}\b", -8, True),
        (r"\bJAKA0\w{6}\b", -8, True),
    ]

    DATE_RE = re.compile(r'(\d{2}-\d{2}-\d{4})')
    AMOUNT_RE = re.compile(r'([\d,]+\.\d{2})')

    SKIP_PATTERNS = [
        'Value Date', 'Post Date', 'Description', 'No/Reference',
        'Page no.', 'Statement Summary', 'In Case Your',
        'Last transaction', 'END OF STATEMENT', 'Account Open Date',
        'Brought Forward', 'Dr Count', 'Cr Count',
    ]

    def parse(self, raw_text: str) -> Dict:
        """Parse SBI statement text using balance-delta approach."""
        account_info = self._extract_account_info(raw_text)
        period = self._extract_period(raw_text)
        transactions = self._parse_transactions(raw_text, account_info)

        return {
            'bank_name': self.BANK_NAME,
            'bank_code': self.BANK_CODE,
            'account_info': account_info,
            'period': period,
            'transactions': transactions,
        }

    def _parse_transactions(self, raw_text: str, account_info: Dict) -> List[Dict]:
        """
        Parse using balance-delta: extract all balances first, then compute W/D.
        
        Strategy:
        1. Collect all transaction groups (date-line pairs + continuations)
        2. For each, extract the BALANCE (the rightmost amount, often with CR/DR)
        3. Compute W/D from balance delta (prev_balance → curr_balance)
        """
        lines = raw_text.split('\n')
        lines = self._rejoin_wrapped_suffixes(lines)
        
        # Phase 1: Collect raw transaction groups
        raw_txns = self._collect_transactions(lines)
        
        # Phase 2: For each group, determine balance and narration
        transactions = []
        for raw in raw_txns:
            txn = self._build_transaction(raw)
            if txn:
                transactions.append(txn)
        
        # Phase 3: Use balance-delta for W/D (most robust for SBI)
        opening = self.parse_balance_value(account_info.get('opening_balance', ''))
        prev_bal = opening
        
        for txn in transactions:
            curr_bal = self.parse_balance_value(txn['balance'])
            if prev_bal is not None and curr_bal is not None:
                delta = round(curr_bal - prev_bal, 2)
                if delta > 0:
                    txn['deposit'] = f"{delta:.2f}"
                    txn['withdrawal'] = ''
                elif delta < 0:
                    txn['withdrawal'] = f"{abs(delta):.2f}"
                    txn['deposit'] = ''
            if curr_bal is not None:
                prev_bal = curr_bal
        
        # Clean internal fields
        for txn in transactions:
            txn.pop('_all_amounts', None)
        
        return transactions

    def _rejoin_wrapped_suffixes(self, lines: List[str]) -> List[str]:
        """
        Rejoin CR/DR suffixes that wrapped to the next line.
        
        SBI sometimes wraps the balance suffix like:
            01-11-2024 MAB CHGSBCH 17,46,293.78    <-- balance here (desc+amount)
            01-11-2024 1,896.22                      <-- debit here (date+amount only)
            CR                                       <-- belongs to balance (2 lines back)
        
        Strategy: CR/DR goes to the nearest preceding line that ends with
        an amount AND has text content (description), not just a bare amount.
        If the immediately previous line is "date + amount only" (no text),
        skip it and attach CR to the line before.
        """
        out = []
        for line in lines:
            stripped = line.strip()
            if stripped.upper() in ('CR', 'DR', 'CR.', 'DR.') and out:
                suffix = stripped.rstrip('.')
                # Look backwards for the right line to attach to
                attached = False
                for j in range(len(out) - 1, max(len(out) - 4, -1), -1):
                    prev = out[j].rstrip()
                    # Check if this line ends with an amount (no existing CR/DR)
                    if not re.search(r'[\d,]+\.\d{2}\s*$', prev):
                        continue
                    # Skip if it already has CR/DR
                    if re.search(r'[\d,]+\.\d{2}\s*(?:CR|DR)', prev, re.IGNORECASE):
                        continue
                    # Check if this is a "date + amount only" line (no description text)
                    # These are debit/credit amount lines, NOT balance lines
                    after_date = re.sub(r'^\d{2}-\d{2}-\d{4}\s*', '', prev).strip()
                    has_text = bool(re.search(r'[a-zA-Z]', after_date))
                    
                    if has_text:
                        # This line has description text + amount = it has the balance
                        out[j] = prev + suffix
                        attached = True
                        break
                    # else: this is a bare amount line (debit/credit), skip it
                
                if not attached:
                    # Fallback: attach to the most recent line ending with amount
                    for j in range(len(out) - 1, max(len(out) - 4, -1), -1):
                        prev = out[j].rstrip()
                        if re.search(r'[\d,]+\.\d{2}\s*$', prev):
                            out[j] = prev + suffix
                            attached = True
                            break
                if not attached:
                    out[-1] = out[-1].rstrip() + suffix
            else:
                out.append(line)
        return out

    def _should_skip(self, line: str) -> bool:
        """Check if a line should be skipped (header/footer/metadata)."""
        upper = line.upper().strip()
        for pat in self.SKIP_PATTERNS:
            if pat.upper() in upper:
                return True
        return False

    def _collect_transactions(self, lines: List[str]) -> List[Dict]:
        """
        Collect lines into raw transaction groups.
        
        Each group: {date, all_lines: [line1, line2, ...], desc_parts: [...]}
        
        SBI pairs: description line (Value Date) + amount line (Post Date).
        Both start with DD-MM-YYYY.
        """
        raw_txns = []
        i = 0
        
        while i < len(lines):
            line = lines[i].strip()
            i += 1
            
            if not line or self._should_skip(line):
                continue
            
            # Skip balance markers
            if re.match(r'BROUGHT FORWARD|CLOSING BALANCE', line, re.IGNORECASE):
                continue
            
            # Look for date-starting line
            m = self.DATE_RE.match(line)
            if not m:
                # Continuation of last transaction
                if raw_txns and not self._should_skip(line):
                    raw_txns[-1]['continuations'].append(line)
                continue
            
            date_str = m.group(1)
            rest = line[m.end():].strip()
            
            # Classify line: description line vs amount-only line
            # Amount-only: date + numbers only (no alphabetic text except CR/DR)
            # Description: date + text + optional amounts (balance on desc line)
            rest_no_amounts = re.sub(r'[\d,]+\.\d{2}\s*(?:CR|DR)?', '', rest, flags=re.IGNORECASE).strip()
            has_description_text = bool(re.search(r'[a-zA-Z]', rest_no_amounts))
            
            # Check for zero balance or CR/DR (amount line indicators)
            has_zero_bal = bool(re.search(r'\b0\.00\s*$', rest))
            is_numeric_only = not has_description_text and len(rest) > 0
            is_amount_line = is_numeric_only or has_zero_bal
            
            if is_amount_line:
                # This is the "Post Date" / amount line (date + numbers only)
                if raw_txns and not raw_txns[-1].get('amount_line'):
                    raw_txns[-1]['amount_line'] = rest
                    raw_txns[-1]['amount_line_full'] = line
                else:
                    # Standalone amount line — treat as new transaction
                    raw_txns.append({
                        'date': date_str,
                        'desc_line': '',
                        'amount_line': rest,
                        'amount_line_full': line,
                        'continuations': [],
                    })
            else:
                # Has description text. Check if this is actually the "amount line"
                # for the previous transaction (SBI pairs desc+amount, both can have text).
                # Key signal: if this line has a CR/DR-suffixed balance AND the previous
                # transaction hasn't gotten an amount_line yet, this IS the amount line.
                has_balance_suffix = bool(re.search(r'[\d,]+\.\d{2}\s*(?:CR|DR)', rest, re.IGNORECASE))
                
                if has_balance_suffix and raw_txns and not raw_txns[-1].get('amount_line'):
                    # This is the amount line with overflow description text
                    # e.g., "ITDTAX REFUND 2024-25 17,48,190.00 CR"
                    raw_txns[-1]['amount_line'] = rest
                    raw_txns[-1]['amount_line_full'] = line
                else:
                    # Truly a new transaction's description line
                    raw_txns.append({
                        'date': date_str,
                        'desc_line': rest,
                        'amount_line': '',
                        'amount_line_full': '',
                        'continuations': [],
                    })
        
        return raw_txns

    def _build_transaction(self, raw: Dict) -> Optional[Dict]:
        """Build a transaction dict from a raw group."""
        # Extract description
        desc_parts = []
        
        if raw['desc_line']:
            # Remove trailing amounts (with or without CR/DR suffix) from description
            desc = re.sub(r'\s+[\d,]+\.\d{2}\s*(?:CR|DR)?\s*$', '', raw['desc_line'], flags=re.IGNORECASE).strip()
            if desc:
                desc_parts.append(desc)
        
        # Extract text from amount line (before the numbers)
        if raw['amount_line']:
            text_before_amounts = re.sub(
                r'[\d,]+\.\d{2}(?:\s*(?:CR|DR))?\s*', '', raw['amount_line'],
                flags=re.IGNORECASE
            ).strip()
            # Clean up leftover colons, UTR labels etc
            if text_before_amounts and not text_before_amounts.isdigit():
                desc_parts.append(text_before_amounts)
        
        # Add continuations (but skip summary data)
        for cont in raw['continuations']:
            cont_stripped = cont.strip()
            if cont_stripped and not self._should_skip(cont_stripped):
                # Skip lines that are purely numeric or look like summary totals
                if re.match(r'^[\d,.\s]+(?:CR|DR)?\s*(?:\d+\s*)*$', cont_stripped, re.IGNORECASE):
                    continue
                desc_parts.append(cont_stripped)
        
        full_desc = ' '.join(desc_parts)
        full_desc = re.sub(r'\s+', ' ', full_desc).strip()
        
        # Extract balance — rightmost amount with CR/DR suffix
        balance = self._extract_balance(raw)
        if not balance:
            return None
        
        # Extract all amounts for reference
        all_amounts = self.AMOUNT_RE.findall(
            raw.get('desc_line', '') + ' ' + raw.get('amount_line', '')
        )
        
        chq_ref = self.finacle_extract_ref(full_desc)
        
        return {
            'date': raw['date'],
            'particulars': full_desc,
            'chq_ref': chq_ref,
            'withdrawal': '',
            'deposit': '',
            'balance': balance,
            '_all_amounts': all_amounts,
        }

    def _extract_balance(self, raw: Dict) -> str:
        """
        Extract balance from the transaction.
        
        Priority:
        1. Amount with CR/DR suffix on the amount line (or desc line)
        2. Last amount on amount line if no CR/DR found (could be "0.00")
        """
        # Check amount line for CR/DR suffixed balance
        for text_key in ['amount_line', 'desc_line']:
            text = raw.get(text_key, '')
            # Find ALL occurrences of amount+CR/DR
            matches = re.findall(r'([\d,]+\.\d{2})\s*(CR|DR)', text, re.IGNORECASE)
            if matches:
                # Take the LAST one (rightmost = balance column)
                val = matches[-1][0].replace(',', '')
                suffix = matches[-1][1].upper()
                return f"{val}{suffix}"
        
        # No CR/DR found — check for "0.00" (zero balance, no suffix)
        amount_line = raw.get('amount_line', '')
        if amount_line:
            amounts = self.AMOUNT_RE.findall(amount_line)
            if amounts:
                last = amounts[-1].replace(',', '')
                if float(last) == 0:
                    return '0.00'
                # Non-zero without CR/DR — this is a positional balance
                # In SBI, balances without CR are still credit balances
                return last
        
        # Check desc_line for balance (e.g., "MAB CHGSBCH 17,46,293.78")
        desc_line = raw.get('desc_line', '')
        if desc_line:
            amounts = self.AMOUNT_RE.findall(desc_line)
            if amounts and not raw.get('amount_line'):
                # Only use desc_line amounts if no amount_line exists
                return amounts[-1].replace(',', '')
        
        return ''

    def _extract_account_info(self, raw_text: str) -> Dict:
        """Extract account information from SBI statement header."""
        info = {
            'account_number': '',
            'account_holder': '',
            'branch': '',
            'ifsc': '',
            'type': '',
            'opening_balance': '',
            'closing_balance': '',
        }

        m = re.search(r'Account\s*No\s*[:\s]+(\d{10,18})', raw_text, re.IGNORECASE)
        if m:
            info['account_number'] = m.group(1)

        # Account holder: look for company/person name
        lines = raw_text.split('\n')
        for line in lines:
            stripped = line.strip()
            if stripped and re.match(r'^[A-Z][A-Z\s.]+(?:PRIVATE|PVT|LTD|LIMITED|ENTERPRISES)', stripped):
                if 'STATE BANK' not in stripped and 'STATEMENT' not in stripped:
                    info['account_holder'] = stripped
                    break

        m = re.search(r'IFSC\s*Code\s*[:\s]*([A-Z]{4}0\w{6})', raw_text, re.IGNORECASE)
        if m:
            info['ifsc'] = m.group(1)

        m = re.search(r'STATE BANK OF INDIA\s*\n\s*(.+)', raw_text)
        if m:
            info['branch'] = m.group(1).strip()

        m = re.search(r'Product\s*[:\s]+(.+)', raw_text, re.IGNORECASE)
        if m:
            info['type'] = m.group(1).strip()

        m = re.search(r'BROUGHT FORWARD\s+([\d,]+\.\d{2})\s*(CR|DR)?', raw_text, re.IGNORECASE)
        if m:
            val = m.group(1).replace(',', '')
            suffix = m.group(2).upper() if m.group(2) else ''
            info['opening_balance'] = f"{val}{suffix}"

        m = re.search(r'CLOSING BALANCE\s+([\d,]+\.\d{2})\s*(CR|DR)?', raw_text, re.IGNORECASE)
        if m:
            val = m.group(1).replace(',', '')
            suffix = m.group(2).upper() if m.group(2) else ''
            info['closing_balance'] = f"{val}{suffix}"

        return info

    def _extract_period(self, raw_text: str) -> str:
        """Extract statement period."""
        m = re.search(
            r'Statement\s+From\s*[:\s]+(\d{2}-\d{2}-\d{4})\s+To\s+(\d{2}-\d{2}-\d{4})',
            raw_text, re.IGNORECASE
        )
        if m:
            return f"{m.group(1)} to {m.group(2)}"
        return 'N/A'
