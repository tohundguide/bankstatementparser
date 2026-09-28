"""
YES Bank branch-printed "STATEMENT OF ACCOUNT".

Synthetic fixture — mirrors the real pdfplumber extraction that returned 0
rows:
  - no IFSC anywhere; "YES BANK" only in the footer / account type, while
    narrations carry Bank of Baroda's IFSC ("BARB0STJOHN") — it was detected
    as Bank of Baroda
  - TXN DATE | VALUE DATE | DESCRIPTION | REFERENCE | DEBITS | CREDITS | BALANCE,
    DD-MON-YYYY dates, both debit and credit columns on every row
  - the B/F (brought forward) row, which is the opening balance
  - wrapped descriptions, and a REFERENCE value repeated by the wrap
  - the Opening/Total/Closing summary ending the table
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from parsers.yes_bank_parser import YesBankParser
from parsers.registry import detect_bank

YES_TEXT = """STATEMENT OF ACCOUNT
Branch: CMH ROAD,BANGALORE
A/C type: YES HEADSTART-UP
M/S. ACME LEARNING PRIVATE LIMITED OD Limit: 0
ACME LEARNING PRIVATE LIMITED Unclear Amt: 0
BANGALORE
A/C Number: 069600000000001
Customer Id: 10000000
Period : 01-APR-2025 To 31-MAR-2026
TXN DATE VALUE DATE DESCRIPTION REFERENCE DEBITS CREDITS BALANCE
01-APR-2025 01-APR-2025 B/F ... 0.00 503.16 503.16
22-APR-2025 22-APR-2025 ME POS PYMT DT 220425 -MID 0696A0175322 0.00 18,500.00 19,003.16
0696A0175322
22-APR-2025 22-APR-2025 NET-NEFT-YESBN12025042204628661-B. 18,500.00 0.00 503.16
REKHA-BARB0STJOHN-HARISHTH-BANK
OF B
01-SEP-2025 01-SEP-2025 SDB_GST 62.82 0.00 440.34
20-DEC-2025 20-DEC-2025 IMPS/NA/XXXX4398/RRN: 400.00 0.00 40.34
535489773754/XHSHVTKVLV/BANK OF
BARODA/REKHABOB/PAY
Opening Balance : 503.16 C
Total Debit Amt : 18,962.82
Total Credit Amt : 18,500.00 Dr Count : 3
Closing Balance : 40.34 Cr Count : 1
******END OF STATEMENT******
Please check the entries in the statement and in case of any discrepancies, report the same within 30 days by visiting the nearest YES BANK branch or
calling on our YES TOUCH toll free number 1800 1200.
"""


def test_yes_branch_not_misdetected_as_bob():
    assert detect_bank(YES_TEXT) == 'yes_bank'


def test_yes_branch_rows():
    txs = YesBankParser().parse(YES_TEXT)['transactions']
    assert [(t['date'], t['withdrawal'], t['deposit'], t['balance']) for t in txs] == [
        ('22-04-2025', '', '18500.00', '19003.16'),
        ('22-04-2025', '18500.00', '', '503.16'),
        ('01-09-2025', '62.82', '', '440.34'),
        ('20-12-2025', '400.00', '', '40.34'),
    ]


def test_yes_branch_narrations_and_ref():
    txs = YesBankParser().parse(YES_TEXT)['transactions']
    assert txs[0]['particulars'] == 'ME POS PYMT DT 220425 -MID 0696A0175322'
    assert txs[0]['chq_ref'] == '0696A0175322'
    assert txs[1]['particulars'] == 'NET-NEFT-YESBN12025042204628661-B. REKHA-BARB0STJOHN-HARISHTH-BANK OF B'
    assert txs[3]['particulars'] == 'IMPS/NA/XXXX4398/RRN: 535489773754/XHSHVTKVLV/BANK OF BARODA/REKHABOB/PAY'
    assert not any('Balance' in t['particulars'] or 'YES TOUCH' in t['particulars'] for t in txs)


def test_yes_branch_account_info():
    r = YesBankParser().parse(YES_TEXT)
    assert r['account_info']['account_number'] == '069600000000001'
    assert r['account_info']['account_holder'] == 'ACME LEARNING PRIVATE LIMITED'
    assert r['account_info']['type'] == 'YES HEADSTART-UP'
    assert r['period'] == '01-APR-2025 to 31-MAR-2026'
