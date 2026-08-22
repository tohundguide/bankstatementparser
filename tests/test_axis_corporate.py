"""
Axis Bank corporate ("neo for corporates" / Account Statement Report) format.

Synthetic fixture — mirrors the real pdfplumber extraction shapes:
  - single-line rows (S.NO date date narration amount DR|CR balance branch)
  - wrapped rows where the amount columns arrive on the narration line
    BEFORE the S.NO line, with further narration lines after it
  - a TRANSACTION TOTAL summary row carrying its own serial number
  - "Opening Balance: INR x" / "Closing Balance: INR x" markers
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from parsers.axis_bank_parser import AxisBankParser
from parsers.registry import detect_bank

CORPORATE_TEXT = """Account Statement Report
ACME LOGISTICS PRIVATE LIMITED
Joint Holder :- C/O SOMEONE~SOMEWHERE~190001 Scheme : CA - BUSINESS ADVANTAGE currency : INR
Customer No : 900000001 IFSC Code : UTIB0001969 MICR Code : 193211201 CKYC Number: **********0000
Statement of Axis Bank Account No : 925020009999999 for the period ( From : 01/04/2025 To : 31/03/2026 )
Opening Balance: INR 0.00
S.NO Transaction Value Date Particulars Amount(INR) Debit/Credit Balance(INR) Cheque Branch Name(SOL)
Date (dd/mm/yyyy) Number
(dd/mm/yyyy)
1 02/07/2025 02/07/2025 By DD Num 626 Paid 13,000.00 CR 13,000.00 KUPWARA KUP JK (1969)
2 16/08/2025 16/08/2025 Monthly Service Chrgs 100.00 DR 12,900.00 KUPWARA KUP JK (1969)
NEFT/JAKAH25234043833/SOMEPAYER 50,000.00 CR 62,900.00 KUPWARA KUP JK (248)
3 22/08/2025 22/08/2025 NAME CONTINUES/JAMMU AND
KASHMIR BA/Urgent//
S.NO Transaction Value Date Particulars Amount(INR) Debit/Credit Balance(INR) Cheque Branch Name(SOL)
Date (dd/mm/yyyy) Number
(dd/mm/yyyy)
4 26/09/2025 26/09/2025 INB/NEFT/OUTBOUND PAYMENT 1,00,000.00 DR -37,100.00 KUPWARA KUP JK (1969)
5 03/12/2025 03/12/2025 Razorpay Software Pvt Ltd Fund 1,050.00 CR -36,050.00 KUPWARA KUP JK (1506)
6 TRANSACTION TOTAL DR/CR 1,00,100.00/64,050.00 KUPWARA KUP JK
Closing Balance: INR -36,050.00
Cheque Return Details
Unless the constituent notifies the bank immediately of any discrepancy found by him/her in this statement of Account, it will be taken that he/she has found the
account correct.
"""


def _parse():
    return AxisBankParser().parse(CORPORATE_TEXT)


def test_detects_axis():
    assert detect_bank(CORPORATE_TEXT) == 'axis_bank'


def test_corporate_format_detected():
    assert AxisBankParser()._is_corporate_format(CORPORATE_TEXT)
    assert not AxisBankParser()._is_corporate_format(
        'Detailed Statement for a/c no. 123 between 01-12-2025 to 31-12-2025\n'
        'Opening Balance 5,376.40\n'
        '01-12-2025 UPI/P2M/coffee 120.00\n'
    )


def test_row_count_and_opening_row():
    txns = _parse()['transactions']
    # opening pseudo-row + 5 transactions; the TOTAL row must not appear
    assert len(txns) == 6
    assert txns[0]['particulars'] == 'Opening Balance'
    assert txns[0]['balance'] == '0.00'
    assert all('TRANSACTION' not in t['particulars'] for t in txns)


def test_drcr_classification_and_balances():
    txns = _parse()['transactions'][1:]
    assert [t['deposit'] or '-' for t in txns] == ['13000.00', '-', '50000.00', '-', '1050.00']
    assert [t['withdrawal'] or '-' for t in txns] == ['-', '100.00', '-', '100000.00', '-']
    assert txns[-1]['balance'] == '-36050.00'


def test_wrapped_row_joins_narration():
    txns = _parse()['transactions']
    wrapped = txns[3]
    assert wrapped['date'] == '22/08/2025'
    assert wrapped['deposit'] == '50000.00'
    assert 'NEFT/JAKAH25234043833/SOMEPAYER' in wrapped['particulars']
    assert 'KASHMIR BA/Urgent//' in wrapped['particulars']


def test_account_info_and_period():
    result = _parse()
    info = result['account_info']
    assert info['account_number'] == '925020009999999'
    assert info['account_holder'] == 'ACME LOGISTICS PRIVATE LIMITED'
    assert info['ifsc'] == 'UTIB0001969'
    assert info['type'] == 'CA - BUSINESS ADVANTAGE'
    assert info['opening_balance'] == '0.00'
    assert info['closing_balance'] == '-36050.00'
    assert result['period'] == '01/04/2025 to 31/03/2026'
