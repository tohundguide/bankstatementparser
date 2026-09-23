"""
IDFC FIRST wrapped-header layout + Kotak 2025 layout.

Synthetic fixtures — mirror the real pdfplumber extraction shapes that made
both parsers return 0 transactions:

IDFC FIRST (multi-page, bordered table):
  - the column header wraps into "Transaction Cheque" / "Value Date
    Particulars Debit Credit Balance" / "Date No" (the parser only accepted
    the one-line form, so it never entered the transaction section)
  - each narration is split around the vertically-centred date row: its
    first line sits ABOVE the date line, the rest below
  - a narration that continues at the top of the next page
  - a row whose date line carries no narration text at all
  - a cheque number between the narration and the amounts

Kotak (2025 "Account Statement" redesign):
  - "# Date Description Chq/Ref. No. Withdrawal (Dr.) Deposit (Cr.) Balance"
  - "1 09 May 2025 <description> <ref> <amount> <balance>" — untagged
    amounts (the old parser required "CR"/"DR" after every amount)
  - wrapped descriptions, a wrapped ref ("1/NCRCTS_1405202" + "3468"),
    a ref glued to a value-date note, and a repeated page header between
    a row and its wrapped description
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from parsers.idfc_first_bank_parser import IDFCFirstBankParser
from parsers.kotak_parser import KotakParser
from parsers.registry import detect_bank

IDFC_PAGE_HEADER = """STATEMENT OF ACCOUNT
CUSTOMER ID : 60******00
ACCOUNT NO : 10200000001
STATEMENT PERIOD : 2025-09-04 TO 2026-03-31
Opening Balance Total Debit Total Credit Closing Balance
0.00 6,04,000.00 6,57,501.00 53,501.00
Transaction Cheque
Value Date Particulars Debit Credit Balance
Date No
"""
IDFC_FOOTER = """REGISTERED OFFICE: IDFC FIRST BANK LIMITED, KRM Tower. 7th Floor, No. 1, Harrington Road, Chetpet, Chennai-600031, Tamilnadu, INDIA.
Page {n} of 2
"""

IDFC_TEXT = (
    """STATEMENT OF ACCOUNT
CUSTOMER ID : 60******00
ACCOUNT NO : 10200000001
STATEMENT PERIOD : 2025-09-04 TO 2026-03-31
CUSTOMER NAME : ACME TUTORS PRIVATE LIMITED ACCOUNT BRANCH : Kalyan Branch
IFSC : IDFB0040135
ACCOUNT TYPE : Gold
Opening Balance Total Debit Total Credit Closing Balance
0.00 6,04,000.00 6,57,501.00 53,501.00
Transaction Cheque
Value Date Particulars Debit Credit Balance
Date No
Opening Balance 0.00
BB/CHQ
06-Sep-2025 08-Sep-2025 DEP/000048/25-08-2025/ 000048 50,000.00 50,000.00
SOME PAYER NAME Y/
UPI/CR/525170555819/
08-Sep-2025 08-Sep-2025 PAYER /PUNB/ 1.00 50,001.00
payer/UPI
NEFT/
02-Oct-2025 02-Oct-2025 IDFB527549273599/ 4,000.00 46,001.00
"""
    + IDFC_FOOTER.format(n=1)
    + IDFC_PAGE_HEADER
    + """NIKHIL SOMEONE
BAKALE/BARB0G
CASH DEPOSIT BY SELF
12-Dec-2025 12-Dec-2025 6,07,500.00 6,53,501.00
40196
RTGS/
12-Dec-2025 12-Dec-2025 IDFBR52025121200352393/ 000001 6,00,000.00 53,501.00
PAYEE A SHARMA
UPI/CR/832279152555/Mr
12-Dec-2025 12-Dec-2025 0.00 53,501.00
ASHAR/CBIN/asharfi/UPI
"""
    + IDFC_FOOTER.format(n=2)
    + """IMPORTANT MESSAGE
• Unless the constituent notifies the bank immediately of any discrepancy found by him in this statement, it will be taken that he has found the account correct.
------- End of the statement -------
"""
)


def _idfc():
    return IDFCFirstBankParser().parse(IDFC_TEXT)['transactions']


def test_idfc_detected():
    assert detect_bank(IDFC_TEXT) == 'idfc_first_bank'


def test_idfc_wrapped_header_is_recognised():
    txs = _idfc()
    assert len(txs) == 6
    assert [t['balance'] for t in txs] == [
        '50000.00', '50001.00', '46001.00', '653501.00', '53501.00', '53501.00']


def test_idfc_narration_above_and_below_the_date_row():
    txs = _idfc()
    assert txs[0]['particulars'] == 'BB/CHQ DEP/000048/25-08-2025/ SOME PAYER NAME Y/'
    assert txs[0]['chq_ref'] == '000048'
    assert txs[1]['particulars'] == 'UPI/CR/525170555819/ PAYER /PUNB/ payer/UPI'
    assert txs[1]['chq_ref'] == ''          # a UPI ref glued to "/" is not a cheque no.


def test_idfc_narration_continues_on_next_page():
    txs = _idfc()
    assert txs[2]['particulars'] == 'NEFT/ IDFB527549273599/ NIKHIL SOMEONE BAKALE/BARB0G'
    assert txs[2]['withdrawal'] == '4000.00'


def test_idfc_cash_rtgs_and_empty_narration_rows():
    txs = _idfc()
    assert txs[3]['particulars'] == 'CASH DEPOSIT BY SELF 40196'
    assert txs[3]['deposit'] == '607500.00'
    assert txs[4]['particulars'] == 'RTGS/ IDFBR52025121200352393/ PAYEE A SHARMA'
    assert txs[4]['chq_ref'] == '000001'
    assert txs[4]['withdrawal'] == '600000.00'
    assert txs[5]['particulars'] == 'UPI/CR/832279152555/Mr ASHAR/CBIN/asharfi/UPI'


def test_idfc_single_line_header_still_parses():
    # The layout the parser was originally written against.
    text = """STATEMENT OF ACCOUNT
ACCOUNT NO : 10100000001
IFSC : IDFB0021351
Transaction Value Date Particulars Cheque Debit Credit Balance
Date No
Opening Balance 61,504.00
24-Jun-2025 24-Jun-2025 UPI/MOB/517526881431/Sent 1.00 61,505.00
using Paytm UPI
REGISTERED OFFICE: IDFC FIRST BANK LIMITED, KRM Tower. Chennai-600031, Tamilnadu, INDIA.
"""
    txs = IDFCFirstBankParser().parse(text)['transactions']
    assert len(txs) == 1
    assert txs[0]['particulars'] == 'UPI/MOB/517526881431/Sent using Paytm UPI'
    assert txs[0]['deposit'] == '1.00'


KOTAK_TEXT = """Account Statement
09 May 2025 - 09 May 2026
ACME FOUNDATION Account No. 7500000001
Account Type Current
Branch Chennai - Rk Salai
Branch Phone Number 9800000000
MICR 600485017 IFSC Code KKBK0008476
Current Account Transactions
# Date Description Chq/Ref. No. Withdrawal (Dr.) Deposit (Cr.) Balance
- - Opening Balance - - - 0.00
1 09 May 2025 Recd:IMPS/512925179801/MEMO APPS IMPS-512912006657 2,000.00 2,000.00
/KKBK/X4606/Donat
2 09 May 2025 BY CLG INST 15075/02-05-25/SIB/CHENNAI 35,000.00 37,000.00
3 14 May 2025 CLG TO MS SOMEONE STATE BANK OF INDIA 1/NCRCTS_1405202 25,000.00 12,000.00
3468
4 15 May 2025 UPI/Some Payee/513569908424/Sent UPI-513568318255 1.00 11,999.00
Statement Generated on 09 May 2026, 07:28 Page 1 of 2
ACME FOUNDATION
Account No.7500000001
Account Statement09 May 2025 - 09 May 2026
Current Account Transactions
# Date Description Chq/Ref. No. Withdrawal (Dr.) Deposit (Cr.) Balance
using Payt
5 07 Aug 2025 Chrg: Weekly Bal Alerts charges for Jun- TBMS-1733467083 5.90 11,993.10
25(Value Date: 06-08-2025)
6 11 Dec 2025 Ac xfr from gl 11200 to 11209 11,993.10 0.00
7 11 Dec 2025 Ac xfr from gl 11200 to 11209 11,993.10 11,993.10
8 09 May 2026 CHRG: Debit Card Annual Fee x5285 for 2026 305.62 11,687.48
Account Summary
Particulars Opening Balance Closing Balance
Current Account (CA): 0.00 11,687.48
End of Statement
"""


def _kotak():
    return KotakParser().parse(KOTAK_TEXT)


def test_kotak_2025_detected():
    assert detect_bank(KOTAK_TEXT) == 'kotak_bank'


def test_kotak_2025_rows_and_direction():
    txs = _kotak()['transactions']
    assert len(txs) == 8
    assert txs[0]['date'] == '09-05-2025'
    assert (txs[0]['deposit'], txs[0]['withdrawal']) == ('2000.00', '')
    assert (txs[2]['withdrawal'], txs[2]['deposit']) == ('25000.00', '')
    assert txs[5]['withdrawal'] == '11993.10' and txs[5]['balance'] == '0.00'
    assert txs[6]['deposit'] == '11993.10'
    assert txs[-1]['balance'] == '11687.48'


def test_kotak_2025_descriptions_and_refs():
    txs = _kotak()['transactions']
    assert txs[0]['particulars'] == 'Recd:IMPS/512925179801/MEMO APPS /KKBK/X4606/Donat'
    assert txs[0]['chq_ref'] == 'IMPS-512912006657'
    assert txs[1]['chq_ref'] == ''
    assert txs[2]['chq_ref'] == '1/NCRCTS_14052023468'
    # A page header sits between row 4 and the rest of its description.
    assert txs[3]['particulars'] == 'UPI/Some Payee/513569908424/Sent using Payt'
    assert txs[4]['chq_ref'] == 'TBMS-1733467083'
    assert txs[4]['particulars'] == 'Chrg: Weekly Bal Alerts charges for Jun- 25(Value Date: 06-08-2025)'


def test_kotak_2025_account_info():
    r = _kotak()
    assert r['account_info']['account_number'] == '7500000001'
    assert r['account_info']['account_holder'] == 'ACME FOUNDATION'
    assert r['account_info']['ifsc'] == 'KKBK0008476'
    assert r['period'] == '09-05-2025 to 09-05-2026'


def test_kotak_legacy_cr_dr_layout_still_parses():
    text = """Account Statement
SOMEONE
Cust. Reln. No. 12345678
Account No. 1234567890
Period From 01/04/2024 To 31/03/2025
Sl. No. Date Description Chq / Ref number Amount Dr / Cr Balance Dr / Cr
NEFT CR SOMEPAYER NEFTINW-0814871191 5,000.00 CR 5,000.00 CR
1 02/04/2024
UPI/SHOP/123 UPI-409312345678 250.00 DR 4,750.00 CR
2 03/04/2024
"""
    txs = KotakParser().parse(text)['transactions']
    assert len(txs) == 2
    assert txs[0]['deposit'] == '5000.00' and txs[0]['date'] == '02-04-2024'
    assert txs[1]['withdrawal'] == '250.00'
