"""
Canara Bank branch "STATEMENT OF ACCOUNT" (TRANS DATE | VALUE DATE | BRANCH |
REF/CHQ.NO | DESCRIPTION | WITHDRAWS | DEPOSIT | BALANCE).

Synthetic fixture — mirrors the real pdfplumber extraction shapes that made
the parser return a handful of amount-less junk rows:
  - DD-MON-YY dates with a 2-digit year (only 4-digit years were accepted,
    so no real row matched at all)
  - a UPI narration whose timestamp wraps onto its own line starting with a
    date ("09/09/2025 12:09:34") — that was being taken as a row start
  - a timestamp split as "12:39:" + "57" (a bare number that is narration)
  - a page number + repeated column header between a row and the rest of
    its description
  - the B/F (brought forward) row, which is the opening balance, not a deposit
  - the BRANCH column before REF/CHQ.NO (the branch code was read as the ref)
  - "Statement Summary" ending the transaction table
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from parsers.canara_bank_parser import CanaraBankParser
from parsers.registry import detect_bank

CANARA_TEXT = """1
STATEMENT OF ACCOUNT
CANARA BANK
DATE : 09-07-26 13:57:53 PM
Account Branch : 4142-JHABUA 457661
IFSC : CNRB0004142
Account No : 120000000001
Product Name : CURRENT ACCOUNT- GENERAL
Customer ID : 300000000
Customer Name : ACME TRIBAL ART AND CRAFT PRODU
Address : 169 SOME MARG
Account Title : ACME TRIBAL ART AND CRAFT PRODU
Period : 01-04-2025 To 31-03-2026
Name Currency : INDIAN RUPEES
TRANS VALUE BRANCH REF/CHQ.NO DESCRIPTION WITHDRAWS DEPOSIT BALANCE
DATE DATE
01-APR-25 01-APR-25 0 B/F ... 0.00 5,475.46 5,475.46
14-MAY-25 14-MAY-25 4142 000962546257 NEFT DR- 3,000.00 0.00 2,475.46
CNRBH00047482225-
BARB0JHABUA-
14-MAY-25 14-MAY-25 4142 NEFT SC 3.00 0.00 2,472.46
09-SEP-25 09-SEP-25 33 267199817807 UPI/CR/267199817807/ 0.00 800.00 3,272.46
AJHAR
ULL/SBIN/**84290@YB
09/09/2025 12:09:34
26-FEB-26 26-FEB-26 33 642374706072 UPI/CR/642374706072/ 0.00 3,400.00 6,672.46
BABULAL
E7F3/26/02/2026 12:39:
57
29-NOV-25 29-NOV-25 4142 000000000000 CASH DEPOSIT 0.00 49,500.00 56,172.46
3
TRANS VALUE BRANCH REF/CHQ.NO DESCRIPTION WITHDRAWS DEPOSIT BALANCE
DATE DATE
TOSEEF JHABUA
21-AUG-25 21-AUG-25 136 000962546264 CHQ PAID-MICR 19,840.00 0.00 36,332.46
INWARD CLEARING-
Statement Summary :
Opening Total Debit Total Credit Debit Count Credit Closing Unclear Hold Sweep-in Balance
5,475.46 22,843.00 53,700.00 3 3 36,332.46 0.00 0.00 0.00
COMPUTER OUTPUT DOES NOT REQUIRE SIGNATURE.
******END OF STATEMENT******
"""


def _parse():
    return CanaraBankParser().parse(CANARA_TEXT)


def test_canara_detected():
    assert detect_bank(CANARA_TEXT) == 'canara_bank'


def test_canara_rows_amounts_and_bf_skipped():
    txs = _parse()['transactions']
    assert [(t['date'], t['withdrawal'], t['deposit'], t['balance']) for t in txs] == [
        ('14-05-2025', '3000.00', '', '2475.46'),
        ('14-05-2025', '3.00', '', '2472.46'),
        ('09-09-2025', '', '800.00', '3272.46'),
        ('26-02-2026', '', '3400.00', '6672.46'),
        ('29-11-2025', '', '49500.00', '56172.46'),
        ('21-08-2025', '19840.00', '', '36332.46'),
    ]


def test_canara_wrapped_timestamps_stay_in_the_narration():
    txs = _parse()['transactions']
    assert txs[2]['particulars'] == \
        'UPI/CR/267199817807/ AJHAR ULL/SBIN/**84290@YB 09/09/2025 12:09:34'
    assert txs[3]['particulars'].endswith('12:39: 57')


def test_canara_page_header_and_summary_not_in_narrations():
    txs = _parse()['transactions']
    assert txs[4]['particulars'] == 'CASH DEPOSIT TOSEEF JHABUA'
    assert txs[5]['particulars'] == 'CHQ PAID-MICR INWARD CLEARING-'
    assert not any('TRANS VALUE' in t['particulars'] or 'Summary' in t['particulars'] for t in txs)


def test_canara_branch_column_is_not_the_ref():
    txs = _parse()['transactions']
    assert txs[0]['chq_ref'] == '000962546257'
    assert txs[1]['chq_ref'] == ''          # NEFT SC: branch 4142, no ref
    assert txs[1]['particulars'] == 'NEFT SC'


def test_canara_account_info():
    info = _parse()['account_info']
    assert info['account_number'] == '120000000001'
    assert info['account_holder'] == 'ACME TRIBAL ART AND CRAFT PRODU'
    assert info['type'] == 'CURRENT ACCOUNT- GENERAL'
    assert info['ifsc'] == 'CNRB0004142'
