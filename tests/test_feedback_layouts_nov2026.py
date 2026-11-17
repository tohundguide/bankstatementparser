"""
Four layouts sent through "report a problem" (Oct-Nov 2026), each read in full since 17 Nov 2026.

Synthetic fixtures: invented names and figures in the shapes the real statements extract to; the real
statements are never kept in this repository.

  - SBI internet banking with "9 May" dates, the year on the row's second line, "Balance as on"
  - ICICI "Statement of Transactions in Saving Account": payee above the row, no opening printed
  - ICICI "Account statement" (current account): the balance on the year line or on the row's own
    line, its minus glued, dropped, or real (overdraft)
  - Karur Vysya Bank: Debit and Credit both printed, B/F opening, rows over two to four lines
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from parsers.icici_parser import ICICIBankParser
from parsers.kvb_parser import KVBParser
from parsers.registry import detect_bank
from parsers.sbi_parser import SBIParser


def chain(result):
    """Every row's own amount carries the balance from the row before (and the opening into the first)."""
    prev = result['account_info'].get('opening_balance')
    prev = float(prev) if prev not in (None, '') else None
    for t in result['transactions']:
        bal = float(t['balance'])
        if prev is not None:
            assert abs(prev - float(t['withdrawal'] or 0) + float(t['deposit'] or 0) - bal) < 0.005, t
        prev = bal


SBI_TEXT = """Account Name :DEMO TRADERS
Address SHOP 1, MARKET ROAD
Account Number :00000012345678901
IFS Code :SBIN0001234
Balance as on 1 May 2026 :10,000.00
Account Statement from 1 May 2026 to 31 Jul 2026
Txn Value Description Ref Branch Debit Credit Balance
Date Date No./Cheque Code
No.
2 May 2 May TO NEFT INB: 99922 2,500.00 7,500.00
2026 2026 TRANSFER- CNAF000001
INB NEFT 1
UTR NO: TRANSFER
5 May 5 May BY TRANSFER 4321 1,00,000.0 1,07,500.00
2026 2026 TRANSFER- FROM
NEFT*KKBK0 12345678901
Txn Value Description Ref Branch Debit Credit Balance
Date Date No./Cheque Code
No.
9 Jun 9 Jun DEBIT- / 4321 7,500.00 1,00,000.00
2026 2026 CMP
MANDATE
"""


def test_sbi_day_month_rows_with_the_year_below():
    r = SBIParser().parse(SBI_TEXT)
    tx = r['transactions']
    assert [t['date'] for t in tx] == ['02-05-2026', '05-05-2026', '09-06-2026']
    assert (tx[0]['withdrawal'], tx[1]['deposit'], tx[2]['withdrawal']) == ('2500.00', '100000.00', '7500.00')
    assert r['account_info']['opening_balance'] == '10000.00'
    assert 'CNAF000001' in tx[0]['particulars']
    chain(r)


ICICI_SAVINGS_TEXT = """1
Statement of Transactions in Saving Account no. 000101234567 in INR for the period April 1, 2026 - October 23, 2026
DEMO PERSON Your Base Branch: ICICI BANK LIMITED,
Transaction Withdrawal Deposit Balance
S No. Cheque Number Transaction Remarks
Date Amount (INR) Amount (INR) (INR)
A PAYEE
1 01.04.2026 20000.00 300000.00
BIL/INFT/FD12345678/ A
PAYEE
B CUSTOMER
2 02.04.2026 5000.00 305000.00
UPI/B CUSTOMER/bcust@okaxis/Payment/AXIS
BANK/612345678901/ICI0000000000000000
Debit trxn
3 03.04.2026 1500.00 303500.00
WMS/MF/P-0000-M12345678/00000000000000
www.icici.bank.in Dial your Bank 1800-1080
Please call from your registered mobile number for faster access
Never share your OTP, CVV or passwords with anyone, even if the person claims to be a Bank employee.
2
C SELLER
4 15.04.2026 123456 3500.00 300000.00
UPI/C SELLER/cseller@ybl/Goods/YES
Sincerly,
Team ICICI Bank
Legends for transactions in your Account Statement
BIL - Internet Bill payment
"""


def test_icici_savings_statement_payee_above_the_row():
    r = ICICIBankParser().parse(ICICI_SAVINGS_TEXT)
    tx = r['transactions']
    assert len(tx) == 4
    assert tx[0]['particulars'].startswith('A PAYEE BIL/INFT')
    assert tx[1]['particulars'].startswith('B CUSTOMER UPI/')
    assert tx[1]['deposit'] == '5000.00' and tx[2]['withdrawal'] == '1500.00'
    assert tx[3]['chq_ref'] == '123456' and tx[3]['date'] == '15-04-2026'
    # No opening is printed: the first row's side is worked out from its remarks, and said so.
    assert tx[0]['withdrawal'] == '20000.00' and tx[0]['amount_source'] == 'inferred'
    assert r['account_info']['opening_balance'] == '320000.00'
    chain(r)


ICICI_CURRENT_TEXT = """A ccount statement
Account name: DEMO TRADING COMPANY Account type: Current Account
Account number: 000105012345 Account currency: INR
IFSC code: ICIC0000123
Statement of transactions from 30 Sep '26 - 31 Oct '26
S.no Transaction Transaction Cheque No Description Withdrawal Deposit Available
ID date (Dr) (Cr) Balance
1 S1000001 30-Sep- MMT/IMPS/5 100000.00 -
2026 123456789 900000.00
12/payee/HDFC0
2 S1000002 30-Sep- UPI/123456 50000.00 -
2026 654321/UPI 950000.00
Pay/XX12
Generated on : 01 Nov'26 (12:00 PM) Page 1 of 9
3 S1000003 01-Oct- RTGS- 1000000.00 -50000.00
2026 SBINR2026
123456789
4 S1000004 01-Oct- RTGS/ICICR 30000.00 -20000.00
2026 123456789
5 S1000005 02-Oct- MMT/IMPS/5 70000.00 50000.00
2026 123456789
6 S1000006 03-Oct- UPI/123456 60000.00 10000.00
2026 654321/UPI
"""


def test_icici_current_statement_balance_on_either_line_and_its_minus_decided_by_the_chain():
    r = ICICIBankParser().parse(ICICI_CURRENT_TEXT)
    tx = r['transactions']
    assert [t['balance'] for t in tx] == ['900000.00', '950000.00', '-50000.00', '-20000.00', '50000.00', '-10000.00']
    # Row 4: "-20000.00" is a real overdraft (950000 - 1000000 = -50000, + 30000 = -20000).
    # Row 6: "10000.00" printed with its minus dropped (50000 - 60000 = -10000).
    assert [bool(t['deposit']) for t in tx] == [False, True, False, True, True, False]
    assert tx[0]['date'] == '30-09-2026' and tx[0]['amount_source'] == 'inferred'
    assert r['account_info']['opening_balance'] == '1000000.00'
    chain(r)


KVB_TEXT = """ACCOUNT STATEMENT
DEMO TEXTILES Acc.No. :1234567890123456
Acc. Type : CA-KVB-ECONOMY
St. Period : 01-Oct-2026 to 05-Nov-2026
Txn Date Value Date Particulars Ref. No. Debit Credit Balance
01-OCT-2026 01-OCT-2026 B/F... - - - 10,000.00
512345678901
02-OCT-2026 UPI-DR-512345678901-A PAYEE-
02-OCT-2026 120.00 0.00 9,880.00
10:15:22 SBIN-00012345678
03-OCT-2026
03-OCT-2026 CASH DEP-SELF-.-TOWN - 0.00 5,000.00 14,880.00
11:00:00
04-OCT-2026 NEFT DR-KVBLH26123456789-Some
04-OCT-2026 - 4,880.00 0.00 10,000.00
12:00:00 trader-ICIC0001
Note: This is a computer-generated report and does not require signature.
2
ACCOUNT STATEMENT
Txn Date Value Date Particulars Ref. No. Debit Credit Balance
05-OCT-2026 UPI-CR-512345678902-B CUSTOMER
05-OCT-2026 0.00 1,000.00 11,000.00
13:00:00 HDFC-99
"""


def test_kvb_debit_and_credit_read_directly_and_known_as_kvb():
    assert detect_bank(KVB_TEXT + '\nNEFT DR-KKBK0001234-x KKBK0001234') == 'kvb'
    r = KVBParser().parse(KVB_TEXT)
    tx = r['transactions']
    assert len(tx) == 4
    assert r['account_info']['opening_balance'] == '10000.00'
    assert [(t['withdrawal'], t['deposit']) for t in tx] == [('120.00', ''), ('', '5000.00'), ('4880.00', ''), ('', '1000.00')]
    assert tx[0]['particulars'] == 'UPI-DR-512345678901-A PAYEE- SBIN-00012345678'
    assert tx[1]['particulars'] == 'CASH DEP-SELF-.-TOWN' and tx[1]['date'] == '03-10-2026'
    assert tx[2]['particulars'] == 'NEFT DR-KVBLH26123456789-Some trader-ICIC0001'
    chain(r)
