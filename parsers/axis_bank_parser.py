"""
Axis Bank Statement Parser
============================
Parses Axis Bank statement PDFs.

Format characteristics (from real sample analysis):
  - Header block: Customer info, reward points, relationship summary, event board
  - Account info: "Account No. XXXXXXXXXXX0811 - Quick View"
  - IFSC: UTIB0... prefix
  - Column Header: "Txn Date Transaction Withdrawals Deposits Balance Other Information"
  - Opening Balance row: "Opening Balance 5,376.40"
  - Transaction lines: DD-MM-YYYY followed by description then ONE amount
  - The single amount is either a Withdrawal or Deposit (no balance on most lines)
  - Balance is only shown on "Opening Balance", some special rows, and "Closing Balance"
  - Multi-line narrations (description wraps to next line without date)
  - Closing Balance row: "Closing Balance 5,338.97"
  - "Legends used in the Statement" marks end of transactions
  - Large disclaimer/footer text after legends
"""

import re
from typing import Dict, List, Optional
from parsers.base_parser import BaseBankParser


class AxisBankParser(BaseBankParser):
    """Parser for Axis Bank statements."""

    BANK_CODE = "axis_bank"
    BANK_NAME = "Axis Bank"
    DETECTION_KEYWORDS = [
        "UTIB0",
        "AXIS BANK",
        "Axis Bank",
        "Axis eDGE",
        "Txn Date",
        "Detailed Statement for a/c",
        "axis bank",
        "axisbank",
    ]

    def parse(self, raw_text: str) -> Dict:
        """Parse Axis Bank statement text into structured data."""
        account_info = self._extract_account_info(raw_text)
        period = self._extract_period(raw_text)

        lines = raw_text.split('\n')
        transactions = []
        current_txn = None
        opening_balance = None
        in_transactions = False

        # Skip patterns — header/footer/metadata lines
        skip_patterns = [
            r'^\s*Customer ID:',
            r'^\s*Registered (Address|Mobile)',
            r'^\s*Points (for|As On)',
            r'^\s*Total Balance',
            r'^\s*Profile Completeness',
            r'^\s*Relationship summary',
            r'^\s*Bank Account\s+CRN',
            r'^\s*(Savings|Current|Fixed Deposit|Recurring|FCNRB)\s+INR',
            r'^\s*Event Board',
            r'^\s*\d+\s*-\s*Please note',
            r'^\s*\d+\s*-\s*Effective',
            r'^\s*\d+\s*-\s*Interest',
            r'^\s*http',
            r'^\s*-http',
            r'^\s*https',
            r'^\s*Account No\.',
            r'^\s*Account Type',
            r'^\s*Lien Amount',
            r'^\s*IFSC Code',
            r'^\s*MICR Code',
            r'^\s*Average Balance',
            r'^\s*Detailed Statement',
            r'^\s*Account Statement',
            r'^\s*Txn Date\s+Transaction',
            r'^\s*Legends used',
            r'^\s*Transaction through',
            r'^\s*ICONN\s',
            r'^\s*AUTO SWEEP',
            r'^\s*REV SWEEP',
            r'^\s*SWEEP TRF',
            r'^\s*VMT\s',
            r'^\s*CWDR\s',
            r'^\s*TIP/SCG',
            r'^\s*BRN\s',
            r'^\s*TD\s+Term',
            r'^\s*SI\s+Standing',
            r'^\s*Send\s',
            r'^\s*Note$',
            r'^\s*Iambeingcustomer',
            r'^\s*theBankconsidered',
            r'^\s*Pleasenote',
            r'^\s*Disclaimer',
            r'^\s*Thea/cbalance',
            r'^\s*clearing\.',
            r'^\s*structuredin',
            r'^\s*sensitive',
            r'^\s*respond\.',
            r'^\s*maintaining',
            r'^\s*www\.',
            r'^\s*3\s*consecutive',
            r'^\s*documents',
            r'^\s*regulation',
            r'^\s*aspertheprevailing',
            r'^\s*pointoftime',
            r'^\s*the Bank comes',
            r'^\s*-\s*Never share',
            r'^\s*-\s*Do not click',
            r'^\s*Please click',
            r'^\s*Customers are',
            r'^\s*·\s*(Internet|Mobile)',
            r'^\s*o\s+(Android|iOS)',
            r'^\s*Phone Banking',
            r'^\s*Click here',
            r'^\s*Deposit Insurance',
            r'^\s*amount of Rs',
            r'^\s*\(\*\s*or\s*exceptions',
            r'^\s*In compliance',
            r'^\s*valid for CASH',
            r'^\s*In case of',
            r'^\s*1860-',
            r'^\s*To view the updated',
            r'^\s*Update your correct',
            r'^\s*Branch Address',
            r'^\s*Registered Office',
            r'^\s*Telephone No',
            r'^\s*Call Customer Care',
            r'^\s*PIN:\s+\d+',
            r'^\s*Rewardpoints',
            r'^\s*Flipkart,',
            r'^\s*Internet Banking',
            r'^\s*Warpora',
            r'^\s*Baramulla',
            r'^\s*India$',
            r'^\s*\d{2}/\d{2}/\d{4}\s+to\s+\d{2}/\d{2}/\d{4}',  # Date range header
            r'^[A-Z ]{2,50}$',  # Customer name line (all caps, no numbers)
            r'^\s*Email ID:',
            r'^\s*KYC Status',
            r'^\s*CKYC No',
            r'^\s*Shop till you',
            r'^\s*Loan Against',
            r'^\s*Overdraft',
            r'^\s*without any',
            r'^\s*open effective',
        ]

        # Amount pattern at end of line
        amount_at_end = re.compile(r'([\d,]+\.\d{2})\s*$')

        # Transaction date pattern: DD-MM-YYYY
        txn_date = re.compile(r'^\s*(\d{2}-\d{2}-\d{4})\s+(.*)')

        # Opening/Closing balance
        opening_re = re.compile(r'Opening\s+Balance\s+([\d,]+\.\d{2})', re.IGNORECASE)
        closing_re = re.compile(r'Closing\s+Balance\s+([\d,]+\.\d{2})', re.IGNORECASE)

        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue

            # Check for opening balance (marks start of transactions)
            om = opening_re.search(stripped)
            if om:
                opening_balance = om.group(1).replace(',', '')
                in_transactions = True
                # Add as first "transaction" (B/F)
                current_txn = {
                    'date': '',
                    'particulars': 'Opening Balance',
                    'chq_ref': '',
                    'withdrawal': '',
                    'deposit': '',
                    'balance': opening_balance,
                }
                transactions.append(current_txn)
                current_txn = None
                continue

            # Check for closing balance (marks end of transactions)
            cm = closing_re.search(stripped)
            if cm:
                if current_txn:
                    transactions.append(current_txn)
                    current_txn = None
                in_transactions = False
                continue

            # Check for legends / end markers
            if 'Legends used' in stripped:
                if current_txn:
                    transactions.append(current_txn)
                    current_txn = None
                in_transactions = False
                continue

            if not in_transactions:
                continue

            # Skip known non-transaction lines
            skip = False
            for pat in skip_patterns:
                if re.match(pat, stripped, re.IGNORECASE):
                    skip = True
                    break
            if skip:
                continue

            # Try matching a transaction line (starts with date)
            m = txn_date.match(stripped)
            if m:
                # Save previous transaction
                if current_txn:
                    transactions.append(current_txn)

                date_str = m.group(1)
                rest = m.group(2).strip()

                # Extract amount from end of line
                am = amount_at_end.search(rest)
                amount = ''
                narration = rest

                if am:
                    amount = am.group(1).replace(',', '')
                    narration = rest[:am.start()].strip()

                current_txn = {
                    'date': date_str,
                    'particulars': narration,
                    'chq_ref': '',
                    'withdrawal': '',
                    'deposit': '',
                    'balance': '',
                    '_amount': amount,  # Will classify later
                }
                continue

            # Continuation line (no date)
            if current_txn:
                # Check if continuation has an amount (sometimes balance or extra amount)
                am = amount_at_end.search(stripped)
                if am and not current_txn.get('_amount'):
                    current_txn['_amount'] = am.group(1).replace(',', '')
                    extra_text = stripped[:am.start()].strip()
                    if extra_text:
                        current_txn['particulars'] += ' ' + extra_text
                else:
                    current_txn['particulars'] += ' ' + stripped

        # Don't forget last transaction
        if current_txn:
            transactions.append(current_txn)

        # Now classify amounts as withdrawal or deposit
        # Axis Bank format: only one amount per transaction, no running balance
        # We need to use the opening balance + transaction amounts to compute running balance
        # and determine W vs D from the narration or pattern
        self._classify_amounts(transactions, opening_balance)

        # Clean up
        for txn in transactions:
            txn.pop('_amount', None)
            txn['particulars'] = re.sub(r'\s+', ' ', txn['particulars']).strip()

        return {
            'bank_name': self.BANK_NAME,
            'bank_code': self.BANK_CODE,
            'account_info': account_info,
            'period': period,
            'transactions': transactions,
        }

    def _classify_amounts(self, transactions: List[Dict], opening_balance: Optional[str]):
        """
        Classify each transaction's amount as withdrawal or deposit.

        Axis Bank statements show only one amount per transaction — it's either
        in the Withdrawals column or the Deposits column. We use narration
        patterns to classify:
          - UPI/P2A (person-to-account inward) = Deposit
          - UPI/P2M (person-to-merchant) = Withdrawal
          - CreditCard Payment = Withdrawal
          - Narrations with 'Ref#' (credit card) = Withdrawal
          - NEFT/RTGS/IMPS inward = Deposit
          - Cash Deposit = Deposit
          - ATM/Cash Withdrawal = Withdrawal
        """
        # Deposit indicators in narration
        deposit_patterns = [
            r'UPI/P2A/',       # P2A inward transfers
            r'NEFT.*CR',
            r'RTGS.*CR',
            r'IMPS.*CR',
            r'Cash Deposit',
            r'BY TRANSFER',
            r'INT\.\s*PD',      # Interest paid
            r'Interest\s+Paid',
            r'REV SWEEP',
            r'SWEEP TRF',
        ]

        # Withdrawal indicators
        withdrawal_patterns = [
            r'UPI/P2M/',       # P2M merchant payments
            r'CreditCard\s+Payment',
            r'Credit\s*Card\s+Payment',
            r'NEFT.*DR',
            r'RTGS.*DR',
            r'ATM',
            r'Cash Withdrawal',
            r'CWDR',
            r'Debit Card',
            r'POS\s',
            r'SI/',             # Standing instruction
            r'INB/',            # Internet banking
            r'AUTO SWEEP',
            r'Charges',
            r'Fee',
            r'GST',
        ]

        for txn in transactions:
            amount = txn.get('_amount', '')
            if not amount:
                continue

            # Skip the opening balance row
            if txn['particulars'] == 'Opening Balance':
                continue

            narration = txn['particulars']

            # Check deposit patterns first
            is_deposit = False
            for pat in deposit_patterns:
                if re.search(pat, narration, re.IGNORECASE):
                    is_deposit = True
                    break

            # Check withdrawal patterns
            is_withdrawal = False
            if not is_deposit:
                for pat in withdrawal_patterns:
                    if re.search(pat, narration, re.IGNORECASE):
                        is_withdrawal = True
                        break

            if is_deposit:
                txn['deposit'] = amount
            elif is_withdrawal:
                txn['withdrawal'] = amount
            else:
                # Default: assume withdrawal (most common in personal statements)
                txn['withdrawal'] = amount

    def _extract_account_info(self, raw_text: str) -> Dict:
        """Extract account information from header."""
        info = {
            'account_number': '',
            'account_holder': '',
            'branch': '',
            'ifsc': '',
            'type': '',
        }

        # Account number: "Account No. XXXXXXXXXXX0811"
        m = re.search(r'Account\s+No\.\s+(\S+)', raw_text)
        if m:
            info['account_number'] = m.group(1)

        # Account holder: typically the second line (after date range)
        lines = raw_text.split('\n')
        if len(lines) > 1:
            # Line 2 is typically the name
            name_line = lines[1].strip()
            if name_line and not re.match(r'Customer ID|Registered|Points|Account', name_line):
                info['account_holder'] = name_line

        # IFSC
        m = re.search(r'IFSC\s+Code\s*:\s*(UTIB\w+)', raw_text)
        if m:
            info['ifsc'] = m.group(1)

        # Branch
        m = re.search(r'Branch\s+Name\s*:\s*(.+?)(?:\n|$)', raw_text)
        if m:
            info['branch'] = m.group(1).strip()

        # Account type
        m = re.search(r'Account\s+Type\s*:\s*(.+?)(?:\s+Branch|\n)', raw_text)
        if m:
            info['type'] = m.group(1).strip()

        return info

    def _extract_period(self, raw_text: str) -> str:
        """Extract statement period."""
        # "Detailed Statement for a/c no. XXX between 01-12-2025 to 31-12-2025"
        m = re.search(
            r'between\s+(\d{2}-\d{2}-\d{4})\s+to\s+(\d{2}-\d{2}-\d{4})',
            raw_text, re.IGNORECASE
        )
        if m:
            return f"{m.group(1)} to {m.group(2)}"

        # "01/12/2025 to 31/12/2025" at top of document
        m = re.search(
            r'(\d{2}/\d{2}/\d{4})\s+to\s+(\d{2}/\d{2}/\d{4})',
            raw_text
        )
        if m:
            return f"{m.group(1)} to {m.group(2)}"

        return 'N/A'
