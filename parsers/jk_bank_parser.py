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
        re.compile(r'M/S\.\.', re.I),
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
            
            if 'M/S..' in line_s:
                info['account_holder'] = line_s.replace('M/S..', '').strip()
            
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
    
    def _parse_line(self, line: str) -> Dict:
        """
        Parse a single line using fixed column positions.
        
        Returns dict with: date, particulars, chq_ref, withdrawal, deposit, balance
        """
        result = {
            'date': '',
            'particulars': '',
            'chq_ref': '',
            'withdrawal': '',
            'deposit': '',
            'balance': '',
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
        
        # 4. Classify amounts by position
        # Withdrawals: right-aligned in cols 51-63 (amounts start at pos 51-61)
        # Deposits: right-aligned in cols 64-76 (amounts start at pos 64+)
        # Larger numbers shift left within their column, hence 64 not 65
        for pos, val in amounts:
            if pos >= 64:
                result['deposit'] = val
            elif pos >= 51:
                result['withdrawal'] = val
        
        # 5. Extract text regions
        # Particulars: from after date (col 14) to cheque column (col 32)
        # Chq/Ref: from col 32 to withdrawal column (col 51)
        
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
        # Column boundary at position 32 (relative to original line)
        chq_col_start = 32
        
        if text_start < chq_col_start and first_amount_pos > chq_col_start:
            # Both columns present in this line
            part_text = line_no_bal[text_start:chq_col_start].strip()
            chq_text = line_no_bal[chq_col_start:first_amount_pos].strip()
            
            # Validate chq_ref: should contain digits to be a reference number
            if chq_text and re.search(r'\d', chq_text):
                result['particulars'] = part_text
                result['chq_ref'] = chq_text
            else:
                # It's all particulars (no cheque number, just text continuation)
                result['particulars'] = text_region.strip()
        else:
            result['particulars'] = text_region.strip()
        
        return result
    
    def _parse_transactions(self, raw_text: str) -> List[Dict]:
        """
        Parse all transactions from the raw text.
        
        Algorithm:
        1. Split into lines
        2. Filter out header/footer/separator lines
        3. Group consecutive lines into transactions:
           - A new transaction starts with a date or B/F
           - Lines without a date are continuations of the previous transaction
        4. For each transaction group, merge the parsed columns
        5. Handle page-break continuations (orphan lines)
        """
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
            parsed = self._parse_line(line)
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
                if txn['balance']:
                    prev['balance'] = txn['balance']
            else:
                merged.append(txn)
        
        # Clean up: remove internal flags and normalize spaces
        for txn in merged:
            txn.pop('_has_date', None)
            txn['particulars'] = re.sub(r'\s{2,}', ' ', txn['particulars']).strip()
            txn['chq_ref'] = re.sub(r'\s{2,}', ' ', txn['chq_ref']).strip()
        
        return merged
    
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
            
            if line['balance']:
                txn['balance'] = line['balance']
        
        txn['particulars'] = ' '.join(parts)
        txn['chq_ref'] = ' '.join(refs)
        
        # Skip if it's just empty
        if not txn['particulars'] and not txn['balance'] and not txn['withdrawal'] and not txn['deposit']:
            return None
        
        return txn
