"""
J&K Bank "STATEMENT OF ACCOUNT FOR THE PERIOD FROM … TO …" (branch-printed
Current / Cash-Credit statement), as it comes out of Tesseract when the PDF
has no text layer.

The synthetic fixture (fake names, account and phone numbers) reproduces the
real OCR shapes seen in production:
  - per-page header with the IFSC's zero read as a letter O, "A/C" as "AIC"
  - one amount per row, Dr/Cr balance, "-" read as "~", "." read as ","
  - a leading "1" read as "4" in amounts (1,400 -> 41,400 / 4,000)
  - a balance with a garbled Dr suffix ("De") and one with a garbled value
  - a narration wrapped onto a second line carrying the amounts, and a
    duplicated-digit debris line under a wrapped row
  - rows the bank printed out of posting order (balances chain from an
    earlier row), "Page Total :" per page and "Grand Total :" at the end
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from parsers.jk_bank_parser import JKBankParser
from parsers.registry import detect_bank

HEADER = """IFSC: JAKAOMMSUM
Main Market Sumbal 193501
iy |

MS SAMPLE DAIRY FARM PROP TEST USER
S/O SOME ONE DATE: 30-04-2026 PAGE:{page}
C/A SOMEWHERE CUSTOMER ID : 001234567

TYPE :
JAMMU AND KASHMIR, INDIA AIC NO : 0123456789012345
PIN:190001 CURRENCY CODE:INR
TESTUSER000@EXAMPLE.COM

STATEMENT OF ACCOUNT FOR THE PERIOD FROM 01-04-2026 TO 30-04-2026

DATE PARTICULARS. CHQ.NO. WITHDRAWALS DEPOSITS BALANCE
"""

PAGE1_ROWS = """01-04-2026 1.00 1.00Cr
02-04-2026 mTFR/9999999999/ALPHA STORE 33,918.00 -33,917.00Dr
03-04-2026 mTFR/9999999999/BETA MART 41,400.00 -35,317.00Dr
04-04-2026 mTFR/9999999999/GAMMA CO 5,000.00 ~30,317.00Dr
05-04-2026 mTFR/9999999999/DELTA TRADERS PVT
LTD 3,000.00 -27,317.00Dr
000. 27,317.
05-04-2026 mTFR/9999999999/EPSILON 2,000.00 -4,29,317,00Dr
06-04-2026 mTFR/9999999999/ZETA 4,000.00 -30,317.00Dr
07-04-2026 mTFR/9999999999/ETA 2,500.00 -31,817.00Dr
07-04-2026 mTFR/9999999999/THETA 1,000.00 -29,317.00Dr
08-04-2026 mTFR/9999999999/IOTA 500.00 -32,317.00Dr
Page Total : 41,318.00 9,001.00 -32,317.00Dr
Page 1 of 2
"""

PAGE2_ROWS = """09-04-2026 mTFR/9999999999/KAPPA 7,080.00 -39,397.00De
10-04-2026 0123456789012345:Int.Coll:01-04-2026 to 30- 663.00 4,40,060:0008
04-202
11-04-2026 UPI/JAKA/123456789012/CR/LAMBDA
SHOP /P2P 3,600.00 -36,460.00Dr
Page Total : 7,743.00 3,600.00 -36,460.00Dr
Grand Total : 49,061.00 12,601.00 -36,460.00Dr
This is a system generated statement and does not require any signature
Date/Time: 30-04-2026 09:02:06 PM
"""

OCR_TEXT = HEADER.format(page=1) + PAGE1_ROWS + HEADER.format(page=2) + PAGE2_ROWS

# The same statement with a text layer: exact values, proper minus signs and
# zero in the IFSC. Must parse identically with nothing to reconcile.
CLEAN_TEXT = (
    OCR_TEXT.replace('JAKAOMMSUM', 'JAKA0MMSUM').replace('AIC NO', 'A/C NO')
    .replace('41,400.00', '1,400.00').replace('~30,317.00Dr', '-30,317.00Dr')
    .replace('-4,29,317,00Dr', '-29,317.00Dr').replace('4,000.00 -30,317.00Dr', '1,000.00 -30,317.00Dr')
    .replace('-39,397.00De', '-39,397.00Dr').replace('4,40,060:0008', '-40,060.00Dr')
    .replace('000. 27,317.\n', '')
)

EXPECTED = [
    # date, particulars, withdrawal, deposit, balance
    ('01-04-2026', '', '', '1.00', '1.00'),
    ('02-04-2026', 'mTFR/9999999999/ALPHA STORE', '33918.00', '', '-33917.00'),
    ('03-04-2026', 'mTFR/9999999999/BETA MART', '1400.00', '', '-35317.00'),
    ('04-04-2026', 'mTFR/9999999999/GAMMA CO', '', '5000.00', '-30317.00'),
    ('05-04-2026', 'mTFR/9999999999/DELTA TRADERS PVT LTD', '', '3000.00', '-27317.00'),
    ('05-04-2026', 'mTFR/9999999999/EPSILON', '2000.00', '', '-29317.00'),
    ('06-04-2026', 'mTFR/9999999999/ZETA', '1000.00', '', '-30317.00'),
    ('07-04-2026', 'mTFR/9999999999/ETA', '2500.00', '', '-31817.00'),      # printed before THETA, posted after
    ('07-04-2026', 'mTFR/9999999999/THETA', '', '1000.00', '-29317.00'),    # chains from ZETA
    ('08-04-2026', 'mTFR/9999999999/IOTA', '500.00', '', '-32317.00'),      # chains from ETA
    ('09-04-2026', 'mTFR/9999999999/KAPPA', '7080.00', '', '-39397.00'),
    ('10-04-2026', '0123456789012345:Int.Coll:01-04-2026 to 30 04-202', '663.00', '', '-40060.00'),
    ('11-04-2026', 'UPI/JAKA/123456789012/CR/LAMBDA SHOP /P2P', '', '3600.00', '-36460.00'),
]


def _rows(result):
    return [(t['date'], t['particulars'], t['withdrawal'], t['deposit'], t['balance']) for t in result['transactions']]


def test_detects_jk_from_ocr_header():
    assert detect_bank(OCR_TEXT) == 'jk_bank'
    assert detect_bank(CLEAN_TEXT) == 'jk_bank'


def test_routes_to_period_parser():
    assert JKBankParser()._is_period_format(OCR_TEXT)
    assert not JKBankParser()._is_detailed_format(OCR_TEXT)


def test_ocr_statement_values():
    result = JKBankParser().parse(OCR_TEXT)
    assert _rows(result) == EXPECTED


def test_ocr_reconciliation_is_reported():
    result = JKBankParser().parse(OCR_TEXT)
    fields = {(c['row'], c['field']) for c in result['ocr_corrections']}
    assert (3, 'amount') in fields         # 41,400 -> 1,400
    assert (6, 'balance') in fields        # -4,29,317,00 -> -29317
    assert (7, 'amount') in fields         # 4,000 -> 1,000
    assert (12, 'balance') in fields       # garbled 4,40,060:0008
    assert any('reconciled against the running balance' in w for w in result['warnings'])
    assert not any('Grand Total' in w for w in result['warnings'])
    assert result['notes']


def test_totals_match_printed_grand_total():
    result = JKBankParser().parse(OCR_TEXT)
    w = sum(float(t['withdrawal'] or 0) for t in result['transactions'])
    d = sum(float(t['deposit'] or 0) for t in result['transactions'])
    assert (round(w, 2), round(d, 2)) == (49061.0, 12601.0)


def test_account_info_and_period():
    info = JKBankParser().parse(OCR_TEXT)['account_info']
    assert info['ifsc'] == 'JAKA0MMSUM'            # letter O normalised to zero
    assert info['account_number'] == '0123456789012345'
    assert info['account_holder'] == 'MS SAMPLE DAIRY FARM PROP TEST USER'
    assert info['branch'] == 'Main Market Sumbal 193501'
    assert info['customer_id'] == '001234567'
    assert JKBankParser().parse(OCR_TEXT)['period'] == '01-04-2026 to 30-04-2026'


def test_clean_text_layer_needs_no_reconciliation():
    result = JKBankParser().parse(CLEAN_TEXT)
    assert _rows(result) == EXPECTED
    assert 'ocr_corrections' not in result
    assert 'warnings' not in result
