"""
Merging several statements of one account into a single chronological one
(statement_merger.merge_statements). Synthetic rows; the shapes follow the
J&K Bank quarterly downloads that motivated the feature: newest-first, with
same-day rows whose balances only chain in the bank's printed order.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from statement_merger import merge_statements, verification_stats


def _t(date, w, d, bal, ref):
    return {'date': date, 'particulars': f'TXN {ref}', 'chq_ref': ref,
            'withdrawal': w, 'deposit': d, 'balance': bal}


def _stmt(name, period, txns, account='1000010100000999'):
    return {'filename': name, 'bank_name': 'Jammu & Kashmir Bank', 'period': period,
            'account_info': {'account_number': account, 'account_holder': 'SAMPLE PVT LTD'},
            'transactions': txns}


# Newest-first, as the bank prints them
Q1 = _stmt('q1.pdf', '01/04/2025 to 30/06/2025', [
    _t('30/06/2025', '', '76.00', '12422.00', 'S4'),
    _t('17/06/2025', '', '10000.00', '12346.00', 'S3'),
    _t('06/06/2025', '11500.00', '', '2346.00', 'DC2'),
    _t('10/04/2025', '', '13846.00', '13846.00', 'S1'),
])
Q2 = _stmt('q2.pdf', '01/07/2025 to 30/09/2025', [
    _t('29/09/2025', '50000.00', '', '600.02', 'DC9'),     # same-day trio: only the
    _t('29/09/2025', '', '600.00', '50600.02', 'S8'),      # printed order chains
    _t('29/09/2025', '', '50000.00', '50000.02', 'DC7'),
    _t('18/09/2025', '12421.98', '', '0.02', 'S6'),
    _t('02/07/2025', '', '0.00', '12422.00', 'S5'),
])
Q3 = _stmt('q3.pdf', '01/10/2025 to 31/12/2025', [
    _t('31/12/2025', '', '76.00', '676.02', 'S11'),
    _t('07/10/2025', '', '0.00', '600.02', 'S10'),
])


def test_orders_files_and_rows_oldest_first_whatever_the_upload_order():
    r = merge_statements([Q3, Q1, Q2])
    assert [t['chq_ref'] for t in r['transactions']] == \
        ['S1', 'DC2', 'S3', 'S4', 'S5', 'S6', 'DC7', 'S8', 'DC9', 'S10', 'S11']
    assert r['period'] == '01/04/2025 to 31/12/2025'
    assert [f['filename'] for f in r['merge_report']['files']] == ['q1.pdf', 'q2.pdf', 'q3.pdf']


def test_same_day_rows_keep_the_bank_order_so_every_balance_chains():
    r = merge_statements([Q2, Q1, Q3])
    stats = verification_stats(r['transactions'])
    assert stats['matched'] == stats['total'] == 10
    assert r['merge_report']['balance_continuous'] is True
    assert r['warnings'] == []


def test_overlapping_downloads_keep_each_row_once():
    overlap = _stmt('q2-again.pdf', '01/09/2025 to 30/09/2025', Q2['transactions'][:4])
    r = merge_statements([Q1, Q2, overlap, Q3])
    assert len(r['transactions']) == 11
    assert r['merge_report']['duplicates_removed'] == 4
    assert verification_stats(r['transactions'])['percent'] == 100.0


def test_a_missing_quarter_is_reported_as_a_balance_break():
    r = merge_statements([Q1, Q3])          # Q2 left out
    report = r['merge_report']
    assert report['balance_continuous'] is False
    b = report['boundaries'][0]
    assert (b['closing_balance'], b['expected_balance'], b['shown_balance']) == (12422.0, 12422.0, 600.02)
    assert b['missing_days'] == 92
    assert any('Balance break' in w and 'q1.pdf' in w and 'q3.pdf' in w for w in r['warnings'])


def test_period_gap_without_balance_change_is_only_a_note():
    gap = _stmt('q3-late.pdf', '15/10/2025 to 31/12/2025', [
        _t('31/12/2025', '', '76.00', '676.02', 'S11'),
        _t('20/10/2025', '', '0.00', '600.02', 'S10'),
    ])
    r = merge_statements([Q1, Q2, gap])
    assert r['merge_report']['balance_continuous'] is True
    assert any('not covered by any file' in w for w in r['warnings'])


def test_different_accounts_are_flagged():
    other = dict(Q3, account_info={'account_number': '5555'})
    r = merge_statements([Q1, Q2, other])
    assert any('different accounts' in w for w in r['warnings'])


def test_identical_rows_inside_one_file_are_not_deduplicated():
    twin = _stmt('twin.pdf', '01/01/2026 to 31/01/2026', [
        _t('05/01/2026', '10.00', '', '90.00', 'X'),
        _t('05/01/2026', '10.00', '', '90.00', 'X'),
    ])
    assert len(merge_statements([twin])['transactions']) == 2
