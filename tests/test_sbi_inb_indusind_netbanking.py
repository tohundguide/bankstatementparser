"""
SBI internet-banking layout + IndusInd net-banking "Account Statement".

Synthetic fixtures — mirror the real pdfplumber extraction shapes that made
both parsers return 0 transactions:

SBI (INB/YONO download):
  - "Opening Balance as on 1 Apr 2025 :(cid:9)5,856.33" (tab rendered as cid:9)
  - "Txn Date Value Date Description Ref No./Cheque Branch Debit Credit Balance"
  - one line per row: DD/MM/YYYY dates, description + ref text, branch code,
    ONE amount, balance — the Finacle path only accepted DD-MM-YYYY lines
  - description/ref columns interleaved on the following lines
  - the page header repeated mid-statement and the computer-generated footer

IndusInd (net banking):
  - no IFSC and no bank name in the text (the name is in the logo image),
    while a narration carries an SBI IFSC — that used to route it to SBI
  - "S46173898 02-Apr-2025 02-Apr-2025 00:00:0 Debit <narration> 520 33435.36":
    whole amounts with no decimals, explicit Debit/Credit type column
  - two-line narrations centred on the row: one line above, one below
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from parsers.sbi_parser import SBIParser
from parsers.indusind_bank_parser import IndusIndBankParser
from parsers.registry import detect_bank

SBI_TEXT = """Account Name :(cid:9)ACME EDUCATIONAL FOUNDATION
Address SOME ROAD , SOMETOWN
Date :(cid:9)22 Sep 2026
Account Number :(cid:9)00000040000000001
Account Description :(cid:9)CA-REGULAR-PUB-OTH-ALL-INR
Branch :(cid:9)SURAJPUR
CIF No. :(cid:9)91300000000
IFS Code :(cid:9)SBIN0000576
MICR Code :(cid:9)497002523
Opening Balance as on 1 Apr 2025 :(cid:9)5,856.33
Account Statement from 1 Apr 2025 to 31 Mar 2026
Txn Date Value Date Description Ref No./Cheque Branch Debit Credit Balance
No. Code
23/05/2025 23/05/2025 TO TRANSFER- NEFT INB: 99922 903.00 4,953.33
INB NEFT UTR NO: CNADPPTAS5
SBIN425143906166- TRANSFER TO
REENA 4698132044308 /
REENA
04/06/2025 04/06/2025 BY TRANSFER- UTE1507626 99922 10,000.00 14,953.33
INB MBS- TRANSFER FROM
31242352515
Txn Date Value Date Description Ref No./Cheque Branch Debit Credit Balance
No. Code
09/09/2025 09/09/2025 CHEQUE WDL- TRANSFER FROM 2836 14,600.00 353.33
CHEQUE 20317904725
12/03/2026 12/03/2026 A/C Keeping / 99999 353.33 0.00
Chgs--
25/03/2026 25/03/2026 CREDIT / 99999 46.00 46.00
INTEREST--
(cid:9)(cid:9) **This is a computer generated statement and does not require a signature.
"""


def test_sbi_inb_detected():
    assert detect_bank(SBI_TEXT) == 'sbi'


def test_sbi_inb_rows_and_direction():
    r = SBIParser().parse(SBI_TEXT)
    txs = r['transactions']
    assert [(t['date'], t['withdrawal'], t['deposit'], t['balance']) for t in txs] == [
        ('23-05-2025', '903.00', '', '4953.33'),
        ('04-06-2025', '', '10000.00', '14953.33'),
        ('09-09-2025', '14600.00', '', '353.33'),
        ('12-03-2026', '353.33', '', '0.00'),
        ('25-03-2026', '', '46.00', '46.00'),
    ]


def test_sbi_inb_narrations_skip_page_noise():
    txs = SBIParser().parse(SBI_TEXT)['transactions']
    assert txs[0]['particulars'].startswith('TO TRANSFER- NEFT INB: INB NEFT UTR NO: CNADPPTAS5')
    assert txs[1]['particulars'] == 'BY TRANSFER- UTE1507626 INB MBS- TRANSFER FROM 31242352515'
    assert txs[3]['particulars'] == 'A/C Keeping / Chgs--'
    assert 'computer generated' not in txs[-1]['particulars']


def test_sbi_inb_account_info():
    r = SBIParser().parse(SBI_TEXT)
    info = r['account_info']
    assert info['account_number'] == '00000040000000001'
    assert info['ifsc'] == 'SBIN0000576'
    assert info['opening_balance'] == '5856.33'
    assert r['period'] == '1 Apr 2025 to 31 Mar 2026'


INDUSIND_TEXT = """Account Statement
ACME MONTESSORI SCHOOL
Customer Name UNIT OF ACME EDUCATIONAL
Account Number : 250000000001
(Account Name) AND CULTURAL COUNCIL
FOUNDATION
From Date : 01-Apr-2025 To Date : 31-Jan-2026
Transaction Date &
Bank Reference Value Date Type Payment Narration Debit Credit Available Balance
Time
S46173898 02-Apr-2025 02-Apr-2025 00:00:0 Debit UPI/100098381620/8839213254-3@ibl 520 33435.36
ATM CASH TXN/INDUSIND BANK
S51517396 02-Apr-2025 02-Apr-2025 00:00:0 Debit 500 32935.36
LIMITED SURAJPUR CG
IMPS/P2A/509623169839/IDFB/PERFIOS
S17303809 06-Apr-2025 06-Apr-2025 00:00:0 Credit 1 32936.36
SOFTWARE SOL
INDUS POS INSTALLATION AND AMC
M568089 24-Apr-2025 24-Apr-2025 00:00:0 Debit 1178.82 31757.54
FEE PSF- 5311457
Page 1 of 2 2026-02-01
Account Statement
Transaction Date &
Bank Reference Value Date Type Payment Narration Debit Credit Available Balance
Time
N/SBIN125251974151/SBIN0000576/NDU
S64245333 08-Sep-2025 08-Sep-2025 00:00:0 Credit 49500 81257.54
EDUCATIONAL AND
M362765 27-Nov-2025 27-Nov-2025 00:00:0 Debit 01 / TRF TO 251011199225 / 11 81246.54
Page 2 of 2 2026-02-01
"""


def test_indusind_not_misdetected_as_sbi():
    # The only IFSC in the text is SBI's, inside a narration.
    assert detect_bank(INDUSIND_TEXT) == 'indusind_bank'


def test_indusind_netbanking_rows():
    txs = IndusIndBankParser().parse(INDUSIND_TEXT)['transactions']
    assert [(t['date'], t['withdrawal'], t['deposit'], t['balance']) for t in txs] == [
        ('02-04-2025', '520.00', '', '33435.36'),
        ('02-04-2025', '500.00', '', '32935.36'),
        ('06-04-2025', '', '1.00', '32936.36'),
        ('24-04-2025', '1178.82', '', '31757.54'),
        ('08-09-2025', '', '49500.00', '81257.54'),
        ('27-11-2025', '11.00', '', '81246.54'),
    ]


def test_indusind_split_narrations_rejoined():
    txs = IndusIndBankParser().parse(INDUSIND_TEXT)['transactions']
    assert txs[0]['particulars'] == 'UPI/100098381620/8839213254-3@ibl'
    assert txs[1]['particulars'] == 'ATM CASH TXN/INDUSIND BANK LIMITED SURAJPUR CG'
    assert txs[2]['particulars'] == 'IMPS/P2A/509623169839/IDFB/PERFIOS SOFTWARE SOL'
    assert txs[3]['particulars'] == 'INDUS POS INSTALLATION AND AMC FEE PSF- 5311457'
    assert txs[4]['particulars'] == 'N/SBIN125251974151/SBIN0000576/NDU EDUCATIONAL AND'
    assert txs[5]['particulars'] == '01 / TRF TO 251011199225 /'
    assert txs[0]['chq_ref'] == 'S46173898'


def test_indusind_netbanking_account_info():
    r = IndusIndBankParser().parse(INDUSIND_TEXT)
    assert r['account_info']['account_number'] == '250000000001'
    assert r['account_info']['account_holder'] == \
        'ACME MONTESSORI SCHOOL UNIT OF ACME EDUCATIONAL AND CULTURAL COUNCIL FOUNDATION'
    assert r['period'] == '01-Apr-2025 to 31-Jan-2026'
