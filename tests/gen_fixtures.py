"""
Synthetic Bank Statement Fixture Generator
============================================
Generates fake but structurally accurate bank statement text files
that mimic pdfplumber output for each supported bank format.

Key features:
  - Computes ground-truth running balance from transactions
  - Generates realistic narrations (UPI, NEFT, ATM, etc.)
  - Includes proper headers, footers, and metadata
  - Handles bank-specific quirks (split dates, reverse chrono, etc.)

Usage:
    python tests/gen_fixtures.py           # Generate all fixtures
    python tests/gen_fixtures.py sbi       # Generate SBI only
"""

import os
import random
import sys
from datetime import datetime, timedelta

# Fixture output directory
FIXTURES_DIR = os.path.join(os.path.dirname(__file__), 'fixtures')
os.makedirs(FIXTURES_DIR, exist_ok=True)

# ── Narration templates ──
UPI_CREDITS = [
    "UPI/CR/408812345678/RAJESH KUMAR/SBIN/Payment",
    "UPI/CR/512734567890/AMIT SHARMA/HDFC/Salary",
    "UPI/CR/609823456789/PRIYA SINGH/ICIC/Refund",
    "NEFT CR SBIN0001234 SALARY JAN 2024",
    "BY TRANSFER NEFT RAJESH KUMAR",
    "BY CLG CHQ DEPOSIT",
    "IMPS CR 408812345678",
    "INT.PD 01JAN24 TO 31MAR24",
]

UPI_DEBITS = [
    "UPI/DR/308745678901/FLIPKART/AXIS/Shopping",
    "UPI/P2M/612345678901/RELIANCE JIO/Payment/YES BANK",
    "UPI/P2M/789456123012/SWIGGY/ICICI/Order",
    "TO TRANSFER NEFT AXIS BANK LTD",
    "TO CLG CHQ PAYMENT",
    "ATM CASH WITHDRAWAL SBI ATM",
    "CreditCard Payment XX 4567",
    "POS PURCHASE AMAZON PAY",
    "EMI LOAN REPAYMENT REF#LN123456",
]


def _random_date(start, end):
    """Random date between start and end."""
    delta = end - start
    return start + timedelta(days=random.randint(0, delta.days))


def _generate_transactions(n_txns, opening_balance, start_date, end_date):
    """Generate n synthetic transactions with correct running balance."""
    txns = []
    balance = opening_balance
    dates = sorted([_random_date(start_date, end_date) for _ in range(n_txns)])

    for d in dates:
        is_credit = random.random() < 0.35  # 35% chance of deposit
        if is_credit:
            amount = round(random.choice([500, 1000, 2000, 5000, 10000, 15000, 25000]) + random.random(), 2)
            narration = random.choice(UPI_CREDITS)
            balance = round(balance + amount, 2)
            txns.append({
                'date': d,
                'narration': narration,
                'ref': str(random.randint(100000, 999999999999)),
                'debit': 0,
                'credit': amount,
                'balance': balance,
            })
        else:
            max_w = min(balance * 0.8, 50000)
            if max_w < 10:
                max_w = 100
            amount = round(random.choice([50, 100, 200, 500, 1000, 2000, 5000]) + random.random(), 2)
            amount = min(amount, max_w)
            narration = random.choice(UPI_DEBITS)
            balance = round(balance - amount, 2)
            txns.append({
                'date': d,
                'narration': narration,
                'ref': str(random.randint(100000, 999999999999)),
                'debit': amount,
                'credit': 0,
                'balance': balance,
            })
    return txns


def _fmt_amount(val):
    """Format amount as Indian-style string (e.g., 1,00,000.00)."""
    if val == 0:
        return ""
    s = f"{val:,.2f}"
    return s


# ═══════════════════════════════════════════
#  Bank-specific generators
# ═══════════════════════════════════════════

def gen_sbi(n_txns=12):
    """Generate synthetic SBI (Finacle) statement."""
    opening = 25000.50
    start = datetime(2024, 1, 1)
    end = datetime(2024, 3, 31)
    txns = _generate_transactions(n_txns, opening, start, end)

    lines = [
        "STATE BANK OF INDIA",
        "Account Statement from 01/01/2024 to 31/03/2024",
        "",
        "Account Number : 39876543210",
        "Name           : RAJESH KUMAR",
        "Branch         : SRINAGAR MAIN BRANCH",
        "IFS Code       : SBIN0000123",
        "MICR Code      : 190002001",
        "CIF No         : 87654321098",
        "Account Type   : SAVINGS ACCOUNT",
        "",
        "Opening Balance : %.2f" % opening,
        "",
        "Txn Date     Value Date   Description                                    Ref No/Chq No    Debit        Credit       Balance",
        "-" * 130,
    ]

    for t in txns:
        date_str = t['date'].strftime('%-d %b %Y') if hasattr(t['date'], 'strftime') else str(t['date'])
        # Windows strftime doesn't support %-d, use manual
        date_str = f"{t['date'].day} {t['date'].strftime('%b')} {t['date'].year}"
        val_date = date_str  # Same for synthetic

        debit_str = _fmt_amount(t['debit']).rjust(12) if t['debit'] else " " * 12
        credit_str = _fmt_amount(t['credit']).rjust(12) if t['credit'] else " " * 12
        bal_str = _fmt_amount(t['balance']).rjust(12)

        line = f"{date_str:<13}{val_date:<13}{t['narration']:<47}{t['ref']:<17}{debit_str}{credit_str}{bal_str}"
        lines.append(line)

    lines.extend([
        "-" * 130,
        "Closing Balance : %.2f" % txns[-1]['balance'],
        "",
        "This is a computer generated statement and does not require signature.",
    ])

    return '\n'.join(lines), txns, opening


def gen_axis(n_txns=10):
    """Generate synthetic Axis Bank statement."""
    opening = 5000.00
    start = datetime(2024, 1, 1)
    end = datetime(2024, 2, 28)
    txns = _generate_transactions(n_txns, opening, start, end)

    lines = [
        "AXIS BANK",
        "Detailed Statement for a/c 917010012345678",
        "Statement from 01/01/2024 to 28/02/2024",
        "",
        "Customer Name : NASIR AHMAD",
        "IFSC Code     : UTIB0001234",
        "Axis eDGE Savings Account",
        "",
        "OPENING BALANCE 5,000.00",
        "",
        "Txn Date    Particulars                                              Amount",
        "-" * 90,
    ]

    for t in txns:
        date_str = t['date'].strftime('%d-%m-%Y')
        amt = t['debit'] if t['debit'] else t['credit']
        amt_str = _fmt_amount(amt)
        line = f"{date_str}  {t['narration']:<60}{amt_str}"
        lines.append(line)

    lines.extend([
        "-" * 90,
        "CLOSING BALANCE %.2f" % txns[-1]['balance'],
    ])

    return '\n'.join(lines), txns, opening


def gen_hdfc(n_txns=10):
    """Generate synthetic HDFC Bank statement."""
    opening = 15000.00
    start = datetime(2024, 1, 1)
    end = datetime(2024, 3, 31)
    txns = _generate_transactions(n_txns, opening, start, end)

    lines = [
        "HDFCBANKLIMITED",
        "Statementof account",
        "PageNo.:1",
        "",
        "Account No : 50100123456789",
        "IFSC       : HDFC0001234",
        "Statement from 01/01/24 to 31/03/24",
        "",
        "Date      Narration                                    Chq./Ref.No.  WithdrawalAmt  DepositAmt  ClosingBalance",
        "-" * 120,
    ]

    for t in txns:
        date_str = t['date'].strftime('%d/%m/%y')
        w = _fmt_amount(t['debit']).rjust(14) if t['debit'] else " " * 14
        d = _fmt_amount(t['credit']).rjust(11) if t['credit'] else " " * 11
        bal = _fmt_amount(t['balance']).rjust(14)
        line = f"{date_str}  {t['narration']:<45}{t['ref'][:12]:<14}{w}{d}{bal}"
        lines.append(line)

    lines.append("-" * 120)
    return '\n'.join(lines), txns, opening


def gen_yes_bank_reverse(n_txns=10):
    """Generate synthetic Yes Bank statement in REVERSE chronological order."""
    opening = 50000.00
    start = datetime(2024, 1, 1)
    end = datetime(2024, 3, 31)
    txns = _generate_transactions(n_txns, opening, start, end)

    lines = [
        "YES BANK LIMITED",
        "Statement of account: 001234500001234",
        "Primary Holder: WABISABI STORES PVT LTD",
        "IFSC Code: YESB0000123",
        "Period: 01 Jan 2024 - 31 Mar 2024",
        "",
        "Value Date  Cheque No  Description                                    Amount       Balance",
        "-" * 100,
    ]

    # Reverse chronological order
    for t in reversed(txns):
        date_str = f"{t['date'].day:02d} {t['date'].strftime('%b')} {t['date'].year}"
        val_date = date_str
        amt = t['debit'] if t['debit'] else t['credit']
        amt_str = _fmt_amount(amt).rjust(12)
        bal_str = _fmt_amount(t['balance']).rjust(12)
        ref = t['ref'][:10]
        line = f"{date_str}  {val_date}  {ref:<11}{t['narration']:<45}{amt_str}{bal_str}"
        lines.append(line)

    lines.extend([
        "-" * 100,
        "End of Statement",
    ])

    return '\n'.join(lines), txns, opening


def gen_kotak(n_txns=10):
    """Generate synthetic Kotak Mahindra Bank statement with Dr/Cr suffixes."""
    opening = 30000.00
    start = datetime(2024, 1, 1)
    end = datetime(2024, 3, 31)
    txns = _generate_transactions(n_txns, opening, start, end)

    lines = [
        "Kotak Mahindra Bank Limited",
        "Cust. Reln. No. : 1234567890",
        "Account No : 1234567890",
        "IFSC : KKBK0001234",
        "1860 266 2666",
        "",
        "Sl. No.  Date        Narration                                     Chq No      Dr / Cr    Amount       Balance",
        "-" * 120,
    ]

    for i, t in enumerate(txns):
        date_str = t['date'].strftime('%d-%m-%Y')
        amt = t['debit'] if t['debit'] else t['credit']
        dr_cr = "Dr" if t['debit'] else "Cr"
        bal_suffix = "Cr" if t['balance'] >= 0 else "Dr"
        bal_str = f"{_fmt_amount(abs(t['balance']))}{bal_suffix}"
        line = f"{i+1:<9}{date_str:<12}{t['narration']:<46}{t['ref'][:10]:<12}{dr_cr:<11}{_fmt_amount(amt):<13}{bal_str}"
        lines.append(line)

    lines.append("-" * 120)
    return '\n'.join(lines), txns, opening


# ═══════════════════════════════════════════
#  Main: generate all fixtures
# ═══════════════════════════════════════════

GENERATORS = {
    'sbi': ('sbi_sample.txt', gen_sbi),
    'axis': ('axis_sample.txt', gen_axis),
    'hdfc': ('hdfc_sample.txt', gen_hdfc),
    'yes_bank': ('yes_bank_reverse_sample.txt', gen_yes_bank_reverse),
    'kotak': ('kotak_sample.txt', gen_kotak),
}


def generate_all(banks=None):
    """Generate fixture files and ground-truth metadata."""
    random.seed(42)  # Reproducible

    banks = banks or list(GENERATORS.keys())
    metadata = {}

    for bank in banks:
        if bank not in GENERATORS:
            print(f"  SKIP: no generator for '{bank}'")
            continue

        filename, gen_fn = GENERATORS[bank]
        text, txns, opening = gen_fn()

        # Write fixture text
        filepath = os.path.join(FIXTURES_DIR, filename)
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(text)

        # Write ground truth
        gt = {
            'bank': bank,
            'fixture': filename,
            'opening_balance': opening,
            'closing_balance': txns[-1]['balance'],
            'transaction_count': len(txns),
            'transactions': [
                {
                    'date': t['date'].strftime('%d-%m-%Y'),
                    'debit': t['debit'],
                    'credit': t['credit'],
                    'balance': t['balance'],
                }
                for t in txns
            ],
        }
        metadata[bank] = gt
        print(f"  ✓ Generated {filename}: {len(txns)} txns, opening={opening}, closing={txns[-1]['balance']}")

    # Write combined ground truth
    import json
    gt_path = os.path.join(FIXTURES_DIR, 'ground_truth.json')
    with open(gt_path, 'w', encoding='utf-8') as f:
        json.dump(metadata, f, indent=2, default=str)
    print(f"\n  Ground truth saved to {gt_path}")


if __name__ == '__main__':
    banks = sys.argv[1:] or None
    generate_all(banks)
