"""
J&K Bank "DETAILED ACCOUNT STATEMENT" format (distinct from the fixed-width
Dr/Cr layout in test coverage elsewhere).

Synthetic fixture mirrors the real pdfplumber extraction shapes:
  - two slash-dates, cheque-no ("-" when none), remarks, then
    Withdrawal / Deposit / Balance columns, then the txn ref
  - remark text that wraps ABOVE (head) and BELOW (tail) the numeric line
  - single-line rows with an inline remark
  - reverse-chronological order (newest first)
  - detection with NO JAKA0 IFSC on the page (pipe account header anchors)
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from parsers.jk_bank_parser import JKBankParser
from parsers.registry import detect_bank

DETAILED_TEXT = """DETAILED ACCOUNT STATEMENT
Account: 0777010100003871|ROOSKET PRIVATE LIMITED|CAA|0777
Transaction Date From:(dd/MM/yyyy): 01/04/2020 To 30/06/2020
Category: All
Transactions List - CD100- ROOSKET PRIVATE LIMITED (INR) - 0777010100003871
Transaction Ref
Value Date Transaction Date Cheque No Transaction Remarks Withdrawal(INR) Deposit(INR) Account Balance(INR)
No
PRCR/000000967124/26-06-2020
26/06/2020 26/06/2020 - 484.35 0.0 1,47,465.55 S96199091
20:25:48/SWT
MCREF/23062020/000028/EMJA
26/06/2020 26/06/2020 - 0.0 3.00 1,47,949.90 S94017037
Y MO
24/06/2020 24/06/2020 - ATM CHARGES QUARTERLY 53.10 0.0 1,47,946.90 S87826862
NEFT-REDBYTES SOFTWARE
05/06/2020 05/06/2020 451801 1,77,000.00 0.0 1,48,000.00 S17884855
PVT LTD
04/06/2020 04/06/2020 - TRF 0.0 2,00,000.00 3,25,000.00 DC59465
11/05/2020 11/05/2020 - 112193 0.0 1,25,000.00 1,25,000.00 DC44205
Legends Used in Account Statement
1. INFT - Internal Fund Transfer(Within J&K Bank)
2. BPAY - Bill Payment
"""


def _parse():
    return JKBankParser().parse(DETAILED_TEXT)


def test_detects_jk_without_ifsc_label():
    # No JAKA0 anywhere on the page — the pipe account header must anchor it.
    assert 'JAKA0' not in DETAILED_TEXT
    assert detect_bank(DETAILED_TEXT) == 'jk_bank'


def test_detailed_format_routed():
    assert JKBankParser()._is_detailed_format(DETAILED_TEXT)


def test_row_count_excludes_headers_and_legends():
    txns = _parse()['transactions']
    assert len(txns) == 6
    assert all('Withdrawal' not in t['particulars'] for t in txns)
    assert all('Legends' not in t['particulars'] for t in txns)


def test_withdrawal_deposit_and_zero_suppression():
    txns = _parse()['transactions']
    first = txns[0]
    assert first['withdrawal'] == '484.35'
    assert first['deposit'] == ''          # "0.0" is suppressed to blank
    assert first['balance'] == '147465.55'
    trf = txns[4]
    assert trf['deposit'] == '200000.00'
    assert trf['withdrawal'] == ''


def test_running_balance_is_internally_consistent():
    # Statement is newest-first; walk it oldest-first.
    txns = list(reversed(_parse()['transactions']))
    prev = None
    for t in txns:
        bal = float(t['balance'])
        w = float(t['withdrawal']) if t['withdrawal'] else 0.0
        d = float(t['deposit']) if t['deposit'] else 0.0
        if prev is not None:
            assert abs(prev + d - w - bal) < 0.01, t
        prev = bal


def test_wrapped_remark_head_and_tail_join():
    txns = _parse()['transactions']
    # head "MCREF/.../EMJA" + tail "Y MO"
    assert 'MCREF/23062020/000028/EMJA' in txns[1]['particulars']
    assert txns[1]['particulars'].endswith('Y MO')
    # head "PRCR/..." + tail "20:25:48/SWT"
    assert txns[0]['particulars'].endswith('20:25:48/SWT')
    # single-line inline remark
    assert txns[2]['particulars'] == 'ATM CHARGES QUARTERLY'


def test_account_info_and_period():
    r = _parse()
    info = r['account_info']
    assert info['account_number'] == '0777010100003871'
    assert info['account_holder'] == 'ROOSKET PRIVATE LIMITED'
    assert info['type'] == 'CAA'
    assert info['branch'] == '0777'
    assert r['period'] == '01/04/2020 to 30/06/2020'
