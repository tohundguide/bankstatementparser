"""
parsers/ocr_reconcile — repairing OCR-damaged (amount, balance) chains.

Each case is a tiny synthetic chain (Dr balances are negative). The
reconciler must repair single-row OCR damage, leave clean text-layer data
untouched, and accept — not "repair" — breaks the statement itself carries.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from parsers.ocr_reconcile import reconcile, edit_cost, _default_costs


def row(amount, balance, amount_raw=None, balance_raw=None, page=0):
    def fmt(v):
        return None if v is None else f"{abs(v):,.2f}"
    return {
        'amount': amount, 'balance': balance,
        'amount_raw': amount_raw if amount_raw is not None else fmt(amount),
        'balance_raw': balance_raw if balance_raw is not None else fmt(balance),
        'page': page,
    }


def values(out):
    return [(o['amount'], o['balance'], o['direction']) for o in out]


def test_clean_chain_is_untouched():
    rows = [row(1.0, 1.0), row(1.0, 2.0), row(33918.0, -33916.0), row(24000.0, -57916.0), row(5000.0, -52916.0)]
    out = reconcile(rows)
    assert all(not o['corrections'] for o in out)
    assert [o['how'] for o in out] == ['keep'] * 5
    assert values(out)[2:] == [(33918.0, -33916.0, 'withdrawal'), (24000.0, -57916.0, 'withdrawal'), (5000.0, -52916.0, 'deposit')]


def test_amount_with_spurious_leading_4_is_fixed_from_balances():
    # 1,400.00 read as 41,400.00; balances around it are right.
    rows = [row(5000.0, -62145.0), row(41400.0, -63545.0), row(32400.0, -95945.0)]
    out = reconcile(rows)
    assert out[1]['amount'] == 1400.0
    assert out[1]['direction'] == 'withdrawal'
    assert out[1]['corrections'] == [('amount', '41,400.00', '1400.00')]
    assert not out[2]['corrections']


def test_misread_balance_is_fixed_from_neighbours():
    # -1,13,405.00 read as -4,13,405,00 (comma for point too); next row chains from the truth.
    rows = [row(4000.0, -115405.0), row(2000.0, -413405.0, balance_raw='-4,13,405,00'), row(1800.0, -115205.0)]
    out = reconcile(rows)
    assert out[1]['balance'] == -113405.0
    assert out[1]['direction'] == 'deposit'
    assert out[1]['corrections'] == [('balance', '-4,13,405,00', '-113405.00')]
    assert out[2]['how'] == 'keep'


def test_missing_balance_is_filled():
    rows = [row(700.0, -115905.0), row(6300.0, None, balance_raw=None), row(6000.0, -116205.0)]
    out = reconcile(rows)
    assert out[1]['balance'] == -122205.0
    assert out[1]['direction'] == 'withdrawal'
    assert ('balance', None, '-122205.00') in out[1]['corrections']


def test_both_values_misread_consistently_are_undone():
    # 15,000 W -> -1,55,524.74 read as 45,000 / 1,85,524.74: the two errors keep
    # the chain consistent, but the document-typical 4->1 confusion (seen on
    # other rows) plus the next row's balance expose them.
    rows = [
        # three plain "1 read as 4" amounts the balances expose — the evidence
        # the adaptation pass needs before it will undo that confusion elsewhere
        row(2000.0, -130000.0),
        row(4000.0, -131000.0), row(4500.0, -132500.0), row(4200.0, -133700.0),
        row(4900.0, -131800.0),                                 # 1,900 D
        row(45000.0, -176800.0), row(4395.0, -148195.0),        # 15,000 W; 1,395 W
        row(500.0, -148695.0), row(8130.0, -140565.0),
    ]
    out = reconcile(rows)
    assert (out[5]['amount'], out[5]['balance'], out[5]['direction']) == (15000.0, -146800.0, 'withdrawal')
    assert (out[6]['amount'], out[6]['balance'], out[6]['direction']) == (1395.0, -148195.0, 'withdrawal')
    assert [o['amount'] for o in out[1:5]] == [1000.0, 1500.0, 1200.0, 1900.0]
    assert out[7]['how'] == 'keep' and out[8]['how'] == 'keep'


def test_out_of_order_rows_are_accepted_not_repaired():
    # J&K prints rows in a different order than it posted them: the balance on
    # the LATEEFA row already includes the 3,000 deposit printed two rows later,
    # and the AAMIR rows chain from older balances. All values are right as read.
    rows = [
        row(22600.0, -147161.0),
        row(9900.0, -154061.0),     # -147161 + 3000 - 9900 (phantom)
        row(1090.0, -155151.0),
        row(3000.0, -144161.0),     # -147161 + 3000 (chains from 3 rows back)
        row(1700.0, -153451.0),     # -155151 + 1700 (chains from 2 rows back)
        row(3000.0, -150451.0),
    ]
    out = reconcile(rows)
    assert all(not o['corrections'] for o in out), values(out)
    assert [o['direction'] for o in out[1:]] == ['withdrawal', 'withdrawal', 'deposit', 'deposit', 'deposit']
    assert out[1]['how'] == 'accept' and out[3]['how'] == 'keep_prior' and out[4]['how'] == 'keep_prior'


def test_row_printed_early_is_chained_from_the_rows_below():
    # The 1,805 withdrawal was posted AFTER the SMS charge and the 3,000 deposit
    # printed below it; OCR also read it as 4,805.
    rows = [
        row(320.0, -158076.24),
        row(4805.0, -156910.74),    # truth 1,805 W: -158076.24 - 29.50 + 3000 - 1805
        row(29.5, -158105.74),      # -158076.24 - 29.50
        row(3000.0, -155105.74),    # + 3000
        row(41380.0, -158290.74),   # truth 1,380 W from -156910.74
        row(135.0, -158425.74),
    ]
    out = reconcile(rows)
    assert (out[1]['amount'], out[1]['direction']) == (1805.0, 'withdrawal')
    assert out[1]['how'] == 'fix_amount'
    assert (out[4]['amount'], out[4]['direction']) == (1380.0, 'withdrawal')
    assert out[2]['balance'] == -158105.74 and out[3]['balance'] == -155105.74


def test_real_gap_without_a_cheap_explanation_is_left_alone():
    # A page is missing between the rows: no single-digit story explains the
    # jump, so both values must stay exactly as read (and be flagged by the
    # verification column downstream).
    rows = [row(1000.0, -10000.0), row(2500.0, -87654.32), row(100.0, -87754.32)]
    out = reconcile(rows)
    assert (out[1]['amount'], out[1]['balance']) == (2500.0, -87654.32)
    assert out[1]['how'] == 'accept'
    assert not out[1]['corrections']


def test_page_totals_break_a_tie():
    # -33,911 -> 24,000 W -> -57,911 -> 1,000 W -> -58,911, read with the middle
    # balance as -67,911 and the last amount as 4,000. Two single-digit stories
    # fit (34,000 W + 9,000 D, or the truth); the page's printed totals decide.
    rows = [row(33918.0, -33911.0), row(24000.0, -67911.0), row(4000.0, -58911.0), row(18000.0, -76911.0)]
    out = reconcile(rows, page_totals={0: (33918.0 + 24000.0 + 1000.0 + 18000.0, 0.0)})
    assert values(out)[1:] == [(24000.0, -57911.0, 'withdrawal'), (1000.0, -58911.0, 'withdrawal'), (18000.0, -76911.0, 'withdrawal')]


def test_edit_cost_prefers_confusable_digits():
    costs = _default_costs()
    assert edit_cost('41,400.00', 1400.0, costs) == 1.0          # spurious leading 4
    assert edit_cost('4,000.00', 1000.0, costs) == 1.0           # 4 <-> 1
    assert edit_cost('4,000.00', 4000.0, costs) == 0.0
    assert edit_cost('4,000.00', 2000.0, costs) > edit_cost('4,000.00', 1000.0, costs)
    assert edit_cost(None, 5.0, costs) == 0.5                    # missing value
