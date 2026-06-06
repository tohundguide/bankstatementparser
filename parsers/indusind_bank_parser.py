"""
IndusInd Bank Statement Parser
================================
Parses IndusInd Bank statement PDFs.

Format characteristics:
  - Header: "IndusInd Bank", "INDUSIND BANK"
  - IFSC: INDB0... prefix
  - Account info: Account No, Customer Name, Branch
  - Column Header: Date | Narration | Chq./Ref.No. | Withdrawal Amt. | Deposit Amt. | Closing Balance
  - Alternative: Transaction Date | Value Date | Description | Debit | Credit | Balance
  - Dates in DD/MM/YYYY or DD-MM-YYYY format
  - Multi-line narrations
  - "Opening Balance" and "Closing Balance" marker rows
  - Amounts with commas and 2 decimal places
"""

import re
from typing import Dict, List, Optional
from parsers.base_parser import BaseBankParser


class IndusIndBankParser(BaseBankParser):
    """Parser for IndusInd Bank statements."""

    BANK_CODE = "indusind_bank"
    BANK_NAME = "IndusInd Bank"
    DETECTION_KEYWORDS = [
        "INDB0",
        "IndusInd",
        "INDUSIND",
        "IndusInd Bank",
        "Withdrawal Amt",
        "Deposit Amt",
        "Closing Balance",
    ]

    def parse(self, raw_text: str) -> Dict:
        """Parse IndusInd Bank statement text into structured data."""
        account_info = self._extract_account_info(raw_text)
        period = self._extract_period(raw_text)

        lines = raw_text.split('\n')
        transactions = []
        current_txn = None

        # Skip patterns
        skip_patterns = [
            r'^\s*IndusInd\s+Bank',
            r'^\s*INDUSIND',
            r'^\s*Statement of Account',
            r'^\s*Account Statement',
            r'^\s*Account\s*(No|Number)',
            r'^\s*Customer\s*(Name|ID|Id|Code)',
            r'^\s*CIF\s*(No|Number|ID)',
            r'^\s*Branch\s*(Name|Code|Address)',
            r'^\s*IFSC',
            r'^\s*MICR',
            r'^\s*Address',
            r'^\s*Currency',
            r'^\s*Nomination',
            r'^\s*Date\s+Narration',
            r'^\s*Date\s+Particulars',
            r'^\s*Date\s+Description',
            r'^\s*Transaction\s+Date\s+Value',
            r'^\s*Sr\.?\s*No',
            r'^\s*Sl\.?\s*No',
            r'^\s*Opening\s+Balance',
            r'^\s*Closing\s+Balance',
            r'^\s*Page\s+\d+',
            r'^\s*Page\s+Total',
            r'^\s*Grand\s+Total',
            r'^\s*This is a (system|computer)',
            r'^\s*Electronically\s+Generated',
            r'^\s*-{5,}',
            r'^\s*={5,}',
            r'^\s*Registered\s+Office',
            r'^\s*Corporate\s+Office',
            r'^\s*Disclaimer',
            r'^\s*Note:',
            r'^\s*\*+\s*(End|This)',
            r'^\s*Total\s*$',
            r'^\s*Withdrawal\s+Amt',
        ]

        # Transaction date patterns
        txn_date_slash = re.compile(r'^\s*(\d{2}/\d{2}/\d{4})\s+(.*)')
        txn_date_dash = re.compile(r'^\s*(\d{2}-\d{2}-\d{4})\s+(.*)')

        # Amount patterns at end of line
        amounts_3 = re.compile(
            r'([\d,]+\.\d{2})\s+([\d,]+\.\d{2})\s+([\d,]+\.\d{2})\s*$'
        )
        amounts_2 = re.compile(
            r'([\d,]+\.\d{2})\s+([\d,]+\.\d{2})\s*$'
        )

        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue

            # Skip header/footer
            skip = False
            for pat in skip_patterns:
                if re.match(pat, stripped, re.IGNORECASE):
                    skip = True
                    break
            if skip:
                continue

            # Try matching transaction line
            m = txn_date_slash.match(stripped) or txn_date_dash.match(stripped)
            if m:
                if current_txn:
                    transactions.append(current_txn)

                date_str = m.group(1)
                rest = m.group(2).strip()

                withdrawal = ''
                deposit = ''
                balance = ''
                chq_ref = ''
                narration = rest

                # Check for value date at start of rest
                val_date_m = re.match(r'(\d{2}[/-]\d{2}[/-]\d{4})\s+(.*)', rest)
                if val_date_m:
                    rest = val_date_m.group(2).strip()
                    narration = rest

                am3 = amounts_3.search(rest)
                am2 = amounts_2.search(rest)

                if am3:
                    narration_part = rest[:am3.start()].strip()
                    amt1 = am3.group(1).replace(',', '')
                    amt2 = am3.group(2).replace(',', '')
                    balance = am3.group(3).replace(',', '')

                    # IndusInd: withdrawal, deposit, closing balance
                    if float(amt1) > 0 and float(amt2) == 0:
                        withdrawal = amt1
                    elif float(amt2) > 0 and float(amt1) == 0:
                        deposit = amt2
                    elif float(amt1) > 0:
                        prev_bal = float(transactions[-1]['balance']) if transactions and transactions[-1]['balance'] else 0
                        curr_bal = float(balance)
                        if curr_bal > prev_bal:
                            deposit = amt2
                        else:
                            withdrawal = amt1

                    # Extract ref number from narration
                    ref_m = re.match(r'(.+?)\s+(\S*\d{6,}\S*)\s*$', narration_part)
                    if ref_m:
                        narration = ref_m.group(1).strip()
                        chq_ref = ref_m.group(2).strip()
                    else:
                        ref_m2 = re.match(r'(\S*\d{6,}\S*)\s+(.*)', narration_part)
                        if ref_m2:
                            chq_ref = ref_m2.group(1)
                            narration = ref_m2.group(2).strip()
                        else:
                            narration = narration_part

                elif am2:
                    narration_part = rest[:am2.start()].strip()
                    amount = am2.group(1).replace(',', '')
                    balance = am2.group(2).replace(',', '')

                    prev_bal = float(transactions[-1]['balance']) if transactions and transactions[-1]['balance'] else 0
                    curr_bal = float(balance)

                    if curr_bal > prev_bal:
                        deposit = amount
                    else:
                        withdrawal = amount

                    ref_m = re.match(r'(.+?)\s+(\S*\d{6,}\S*)\s*$', narration_part)
                    if ref_m:
                        narration = ref_m.group(1).strip()
                        chq_ref = ref_m.group(2).strip()
                    else:
                        narration = narration_part

                date_normalized = self._normalize_date(date_str)

                current_txn = {
                    'date': date_normalized,
                    'particulars': narration,
                    'chq_ref': chq_ref,
                    'withdrawal': withdrawal,
                    'deposit': deposit,
                    'balance': balance,
                }
                continue

            # Continuation line
            if current_txn:
                chq_m = re.match(r'^(\d{6,})$', stripped)
                if chq_m and not current_txn['chq_ref']:
                    current_txn['chq_ref'] = chq_m.group(1)
                elif not re.match(r'^\s*Page\s+\d+', stripped, re.IGNORECASE):
                    current_txn['particulars'] += ' ' + stripped

        if current_txn:
            transactions.append(current_txn)

        # Clean up
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
        """Extract account information from header."""
        info = {
            'account_number': '',
            'account_holder': '',
            'branch': '',
            'ifsc': '',
            'type': '',
        }

        m = re.search(r'Account\s*(?:No|Number)[:\s]+(\d+)', raw_text, re.IGNORECASE)
        if m:
            info['account_number'] = m.group(1)

        m = re.search(r'(?:Customer\s*Name|Name)[:\s]+(.+?)(?:\n|Account|Branch|CIF|IFSC)', raw_text, re.IGNORECASE)
        if m:
            info['account_holder'] = m.group(1).strip()

        m = re.search(r'IFSC[:\s]+(INDB\w+)', raw_text, re.IGNORECASE)
        if m:
            info['ifsc'] = m.group(1)

        m = re.search(r'Branch\s*(?:Name)?[:\s]+(.+?)(?:\n|IFSC|Address|MICR)', raw_text, re.IGNORECASE)
        if m:
            info['branch'] = m.group(1).strip()

        m = re.search(r'(?:Account\s*Type|Product)[:\s]+(.+?)(?:\n|Currency)', raw_text, re.IGNORECASE)
        if m:
            info['type'] = m.group(1).strip()

        return info

    def _extract_period(self, raw_text: str) -> str:
        """Extract statement period."""
        m = re.search(
            r'(?:From|Period|Statement)[:\s]*([\d/\-]+\d{4})\s+(?:To|to)\s+([\d/\-]+\d{4})',
            raw_text, re.IGNORECASE
        )
        if m:
            return f"{m.group(1)} to {m.group(2)}"
        return 'N/A'

    def _normalize_date(self, date_str: str) -> str:
        """Convert DD/MM/YYYY to DD-MM-YYYY."""
        return date_str.replace('/', '-') if '/' in date_str else date_str
