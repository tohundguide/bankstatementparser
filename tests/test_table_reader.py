"""
R2, the table reads itself (bankbest-b, b-3; Karr's docs/BANK_BEST_SPEC.md §4.2).

Golden tests on synthetic PDFs made here (tests/synth_pdf.py): no real account,
no real bank's layout. Every shape that must read, and every shape that must
NOT read because only a guess would read it.
"""
import io
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault('API_KEYS', 'contract-test-key:ContractTest')

from synth_pdf import money, pdf_bytes, right, statement_pdf  # noqa: E402
from parsers.table_reader import layout_of_roles, read_day, read_money, read_table, text_layout  # noqa: E402
from parsers.registry import detect_bank  # noqa: E402

ROWS = [
    ('01/04/2025', 'UPI/CR/408812/ACME TRADERS', 'REF001', 0, 15000),
    ('03/04/2025', 'ATM WDL MG ROAD', '', 2000, 0),
    ('05/04/2025', 'NEFT DR VENDOR ONE', 'UTR2233', 5000.5, 0),
    ('05/04/2025', 'CHQ DEP 000123', '000123', 0, 1200),
    ('09/04/2025', 'INTEREST CREDIT', '', 0, 12.34),
    ('30/04/2025', 'SMS CHARGES', '', 17.7, 0),
]
WANT = [  # (date, withdrawal, deposit, balance) with an opening of 1,000.00
    ('01-04-2025', '', '15,000.00', '16,000.00'),
    ('03-04-2025', '2,000.00', '', '14,000.00'),
    ('05-04-2025', '5,000.50', '', '8,999.50'),
    ('05-04-2025', '', '1,200.00', '10,199.50'),
    ('09-04-2025', '', '12.34', '10,211.84'),
    ('30-04-2025', '17.70', '', '10,194.14'),
]


def read(pdf, tmp_path, raw_text=''):
    path = tmp_path / 's.pdf'
    path.write_bytes(pdf)
    return read_table(str(path), raw_text=raw_text)


def figures(r):
    return [(t['date'], t['withdrawal'], t['deposit'], t['balance']) for t in r['transactions']]


# ── What reads ──

@pytest.mark.parametrize('layout', ['two_columns', 'signed', 'dr_cr'])
@pytest.mark.parametrize('newest_first', [False, True])
def test_every_money_layout_in_either_row_order(tmp_path, layout, newest_first):
    pdf, _ = statement_pdf(ROWS, opening=1000, layout=layout, newest_first=newest_first)
    r = read(pdf, tmp_path)
    assert r is not None, f'{layout} newest_first={newest_first} did not read'
    assert figures(r) == WANT, 'oldest first, every row, the money on the right side'
    cm = r['column_map']
    assert cm['reader'] == 'table' and cm['amounts'] == layout
    assert cm['row_order'] == ('newest_first' if newest_first else 'oldest_first')
    assert cm['date_order'] == 'day_first'
    assert r['account_info']['opening_balance'] == '1000.00'
    assert r['account_info']['closing_balance'] == '10,194.14'
    assert r['transactions'][0]['particulars'] == 'UPI/CR/408812/ACME TRADERS'
    assert [t['chq_ref'] for t in r['transactions']] == ['REF001', '', 'UTR2233', '000123', '', '']


def test_the_first_row_is_placed_by_its_column_not_by_a_guess(tmp_path):
    """Opening 20,000, first row a withdrawal: only the column tells it apart from a deposit of the same size."""
    rows = [('01/04/2025', 'RENT', '', 5000, 0), ('02/04/2025', 'SALE', '', 0, 700), ('03/04/2025', 'FEE', '', 30, 0)]
    pdf, _ = statement_pdf(rows, opening=20000, layout='two_columns')
    r = read(pdf, tmp_path)
    assert figures(r)[0] == ('01-04-2025', '5,000.00', '', '15,000.00')
    assert r['account_info']['opening_balance'] == '20000.00'


def test_headings_never_choose_the_columns(tmp_path):
    """The deposit column printed first (left) and the headings saying nothing useful: the chain still picks."""
    rows = [('01/04/2025', 'A', '', 0, 100), ('02/04/2025', 'B', '', 40, 0), ('03/04/2025', 'C', '', 0, 5)]
    # deposits in the left column, withdrawals in the right
    swapped = [(d, n, ref, dep, wd) for d, n, ref, wd, dep in rows]
    bal, fixed = 0, []
    for d, n, ref, left, rightcol in swapped:
        bal = round(bal + left - rightcol, 2)
        fixed.append((d, n, ref, left, rightcol, bal))
    items = [(30, 560, 'STATEMENT'), (30, 540, 'Date'), (110, 540, 'Details'), (380, 540, 'Ref'),
             right(560, 'Credit', 540), right(660, 'Debit', 540), right(780, 'Balance', 540)]
    y = 524
    for d, n, ref, left, rightcol, b in fixed:
        items += [(30, y, d), (110, y, n)]
        if left:
            items.append(right(560, money(left), y))
        if rightcol:
            items.append(right(660, money(rightcol), y))
        items.append(right(780, money(b), y))
        y -= 12
    r = read(pdf_bytes([items]), tmp_path)
    assert figures(r) == [('01-04-2025', '', '100.00', '100.00'), ('02-04-2025', '40.00', '', '60.00'),
                          ('03-04-2025', '', '5.00', '65.00')]
    assert r['column_map']['money_out'] == 'right column'


def test_overdrawn_balances(tmp_path):
    rows = [('01/04/2025', 'A', '', 3000, 0), ('02/04/2025', 'B', '', 0, 500), ('03/04/2025', 'C', '', 0, 4000)]
    items = [(30, 560, 'STATEMENT'), (30, 540, 'Date'), (110, 540, 'Narration'), right(560, 'Debit', 540),
             right(660, 'Credit', 540), right(780, 'Balance', 540)]
    bal, y = 1000.0, 524
    for d, n, _, wd, dep in rows:
        bal = round(bal - wd + dep, 2)
        items += [(30, y, d), (110, y, n)]
        items.append(right(560, money(wd), y) if wd else right(660, money(dep), y))
        items.append(right(780, money(abs(bal)) + (' Dr' if bal < 0 else ' Cr'), y))
        y -= 12
    r = read(pdf_bytes([items]), tmp_path)
    assert figures(r) == [('01-04-2025', '3,000.00', '', '2,000.00 Dr'), ('02-04-2025', '', '500.00', '1,500.00 Dr'),
                          ('03-04-2025', '', '4,000.00', '2,500.00')]
    assert r['account_info']['opening_balance'] == '1000.00'


@pytest.mark.parametrize('fmt,printed', [
    (lambda d: d.replace('/', '.'), '01.04.2025'),
    (lambda d: d.replace('/', '-'), '01-04-2025'),
    (lambda d: f"{d[:2]}-{['Jan','Feb','Mar','Apr'][int(d[3:5]) - 1]}-{d[6:]}", '01-Apr-2025'),
    (lambda d: f"{d[:2]}-{['Jan','Feb','Mar','Apr'][int(d[3:5]) - 1]}-{d[8:]}", '01-Apr-25'),
    (lambda d: f"{d[:2]} {['Jan','Feb','Mar','Apr'][int(d[3:5]) - 1]} {d[6:]}", '01 Apr 2025'),
    (lambda d: f"{d[6:]}-{d[3:5]}-{d[:2]}", '2025-04-01'),
    (lambda d: f"{d} 10:35:22", '01/04/2025 10:35:22'),
])
def test_dates_in_every_printed_shape(tmp_path, fmt, printed):
    rows = [(f'{i:02d}/04/2025', f'ROW {i}', '', 0 if i % 2 else 10 * i, 10 * i if i % 2 else 0) for i in range(1, 6)]
    pdf, lines = statement_pdf(rows, opening=500, date_fmt=fmt)
    assert lines[0][0] == printed
    r = read(pdf, tmp_path)
    assert r is not None, printed
    assert [t['date'] for t in r['transactions']] == [f'{i:02d}-04-2025' for i in range(1, 6)]


def test_month_first_only_when_day_first_is_not_a_day(tmp_path):
    rows = [('04/01/2025', 'A', '', 0, 10), ('04/13/2025', 'B', '', 5, 0), ('04/20/2025', 'C', '', 0, 1)]
    pdf, _ = statement_pdf(rows, opening=100)
    r = read(pdf, tmp_path)
    assert [t['date'] for t in r['transactions']] == ['01-04-2025', '13-04-2025', '20-04-2025']
    assert r['column_map']['date_order'] == 'month_first'


def test_narration_running_onto_the_next_line_and_a_second_page(tmp_path):
    rows = [(f'{(i % 28) + 1:02d}/05/2025', f'PAYMENT {i}', '', 0 if i % 3 else 100 + i, 50 + i if i % 3 else 0)
            for i in range(40)]
    rows.sort(key=lambda r: r[0])
    pdf, lines = statement_pdf(rows, opening=10000, rows_per_page=15, narration_wrap='/CONT LINE')
    r = read(pdf, tmp_path)
    assert r is not None and len(r['transactions']) == 40, 'three pages, the heading again on each, no row lost'
    assert all(t['particulars'].endswith('/CONT LINE') for t in r['transactions'])
    assert not any('Page' in t['particulars'] for t in r['transactions']), 'a page footer is not narration'
    assert r['transactions'][-1]['balance'] == money(lines[-1][5])


def test_account_details_printed_above_the_table(tmp_path):
    pdf, _ = statement_pdf(ROWS, opening=1000, header_lines=[
        'Account No: XXXXXXXX4521', 'IFSC Code: SBIN0001234', 'Statement period: 01/04/2025 to 30/04/2025',
        'Opening Balance 1,000.00'])
    raw = 'Account No: XXXXXXXX4521\nIFSC Code: SBIN0001234\nStatement period: 01/04/2025 to 30/04/2025\n'
    r = read(pdf, tmp_path, raw_text=raw)
    assert r['account_info']['account_number'] == 'XXXXXXXX4521'
    assert r['account_info']['ifsc'] == 'SBIN0001234'
    assert r['bank_name'] == 'State Bank of India', 'the IFSC the statement prints for itself names the bank'
    assert r['period'] == '01/04/2025 to 30/04/2025'


# ── What must NOT read: only a guess would read it ──

def test_a_broken_chain_is_no_read(tmp_path):
    pdf, lines = statement_pdf(ROWS, opening=1000)
    bad = pdf.replace(b'(8,999.50)', b'(8,999.60)')
    assert bad != pdf
    assert read(bad, tmp_path) is None, 'one balance off by ten paise and no layout proves itself'


def test_an_opening_that_disagrees_is_no_read(tmp_path):
    pdf, _ = statement_pdf(ROWS, opening=1000, header_lines=['Opening Balance 999.00'])
    assert read(pdf, tmp_path) is None


def test_two_reads_that_both_add_up_is_no_read(tmp_path):
    """All on one day, balances 100 -> 200 -> 100: oldest first and newest first both add up, to different reads."""
    items = [(30, 560, 'STATEMENT'), (30, 540, 'Date'), (110, 540, 'Narration'), right(560, 'Withdrawal', 540),
             right(660, 'Deposit', 540), right(780, 'Balance', 540)]
    for y, (n, wd, dep, bal) in zip((524, 512, 500), [('A', 100, 0, 100), ('B', 0, 100, 200), ('C', 100, 0, 100)]):
        items += [(30, y, '01/04/2025'), (110, y, n)]
        items.append(right(560, money(wd), y) if wd else right(660, money(dep), y))
        items.append(right(780, money(bal), y))
    assert read(pdf_bytes([items]), tmp_path) is None


def test_an_amount_that_cannot_be_read_refuses_the_file(tmp_path):
    pdf, _ = statement_pdf(ROWS, opening=1000)
    bad = pdf.replace(b'(2,000.00)', b'(Rs.2,000.00)')
    assert read(bad, tmp_path) is None, 'never read the rest and drop the row'


def test_a_date_that_is_not_a_day_refuses_the_file(tmp_path):
    rows = list(ROWS)
    rows[2] = ('31/02/2025',) + rows[2][1:]
    pdf, _ = statement_pdf(rows, opening=1000)
    assert read(pdf, tmp_path) is None


def test_money_on_a_dateless_line_inside_the_table_refuses_the_file(tmp_path):
    pdf, _ = statement_pdf(ROWS, opening=1000)
    items = [(30, 560, 'STATEMENT'), (30, 540, 'Date'), (110, 540, 'Narration'), right(560, 'Withdrawal', 540),
             right(660, 'Deposit', 540), right(780, 'Balance', 540),
             (30, 524, '01/04/2025'), (110, 524, 'A'), right(660, '100.00', 524), right(780, '100.00', 524),
             (110, 500, 'SOMETHING'), right(660, '50.00', 500), right(780, '150.00', 500),
             (30, 488, '02/04/2025'), (110, 488, 'B'), right(560, '10.00', 488), right(780, '140.00', 488),
             (30, 476, '03/04/2025'), (110, 476, 'C'), right(560, '10.00', 476), right(780, '130.00', 476)]
    assert read(pdf_bytes([items]), tmp_path) is None


def test_fewer_than_three_rows_cannot_prove_itself(tmp_path):
    pdf, _ = statement_pdf(ROWS[:2], opening=1000)
    assert read(pdf, tmp_path) is None


def test_no_heading_row_is_no_table(tmp_path):
    pdf, _ = statement_pdf(ROWS, opening=1000, headings=['When', 'What', 'Ref', 'Out', 'In', 'Left'])
    assert read(pdf, tmp_path) is None


def test_the_synthetic_layout_is_no_bank_of_ours():
    """The layout these tests and Karr's daily watch use must reach R2, not a bank's parser."""
    text = 'ACCOUNT STATEMENT\nDate Narration Ref No Withdrawal Deposit Balance\n01/04/2025 UPI/CR 15,000.00 16,000.00\n'
    assert detect_bank(text) is None


# ── The pieces ──

def test_read_day():
    assert str(read_day('01/04/2025')) == '2025-04-01'
    assert str(read_day('1/4/25')) == '2025-04-01'
    assert str(read_day('01-Sept-2025')) == '2025-09-01'
    assert read_day('31/02/2025') is None
    assert read_day('13/13/2025') is None
    assert read_day('Opening') is None
    assert str(read_day('04/13/2025', month_first=True)) == '2025-04-13'


def test_read_money():
    assert read_money('1,23,456.70')[:3] == (123456.7, 1, None)
    assert read_money('-500.00')[:2] == (500.0, -1)
    assert read_money('(500.00)')[:2] == (500.0, -1)
    assert read_money('500.00Dr')[2] == 'Dr'
    assert read_money('500') is None, 'a figure without paise is not a statement amount'
    assert read_money('(500.00') is None
    assert read_money('Rs.500.00') is None


def test_fingerprint_is_the_shape_only():
    a = text_layout('Name: RAVI KUMAR\nDate Narration Withdrawal Deposit Balance\n')
    b = text_layout('Name: ANOTHER PERSON\nDate Narration Withdrawal Deposit Balance\n')
    assert a == b == layout_of_roles(['date', 'narration', 'withdrawal', 'deposit', 'balance'], None)
    assert len(a[0]) == 16 and a[0] != text_layout('Date Narration Debit Credit Balance', 'sbi')[0]


# ── Through the API ──

def test_the_api_reads_an_unknown_pdf_by_its_table_and_never_asks_the_ai(monkeypatch):
    import app as parser_app
    import api_auth
    api_auth.reload_keys()
    monkeypatch.setattr(parser_app.llm_parser, 'is_available', lambda: True)
    monkeypatch.setattr(parser_app.llm_parser, 'parse_with_llm',
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError('no AI before R2')))
    pdf, _ = statement_pdf(ROWS, opening=1000, layout='dr_cr', newest_first=True)
    client = parser_app.app.test_client()
    res = client.post('/api/v1/parse', data={'file': (io.BytesIO(pdf), 'statement.pdf'), 'allow_ai': 'false'},
                      headers={'X-API-Key': 'contract-test-key'}, content_type='multipart/form-data')
    body = res.get_json()
    assert res.status_code == 200, body
    assert body['parser_version'] == parser_app.PARSER_VERSION
    assert body['read_by'] == 'pdf_text' and body['parsed_by'] == 'structured'
    assert body['txn_count'] == 6 and body['total_transactions'] == 6
    assert body['column_map']['reader'] == 'table' and body['column_map']['amounts'] == 'dr_cr'
    fp, shape = layout_of_roles(['date', 'date', 'narration', 'reference', 'amount', 'dr_cr', 'balance'], body['transactions'][-1]['date'])
    assert body['layout_shape']['headings'] == shape['headings'] and body['layout_shape']['kinds'] == shape['kinds']
    assert len(body['layout_fingerprint']) == 16
    assert body['verification'] == {'matched': 5, 'total': 5, 'percent': 100.0}
    assert [(t['date'], t['withdrawal'], t['deposit'], t['balance']) for t in body['transactions']] == WANT
    left = [f for f in os.listdir(parser_app.app.config['UPLOAD_FOLDER']) if f.startswith('api_')]
    assert left == [], 'the file is gone once the answer is sent'


def test_an_unread_pdf_says_its_shape(monkeypatch):
    import app as parser_app
    import api_auth
    api_auth.reload_keys()
    pdf, _ = statement_pdf(ROWS, opening=1000)
    bad = pdf.replace(b'(8,999.50)', b'(8,999.60)')
    client = parser_app.app.test_client()
    res = client.post('/api/v1/parse', data={'file': (io.BytesIO(bad), 'statement.pdf'), 'allow_ai': 'false'},
                      headers={'X-API-Key': 'contract-test-key'}, content_type='multipart/form-data')
    body = res.get_json()
    assert res.status_code == 400 and body['code'] == 'PARSE_FAILED'
    assert len(body['layout_fingerprint']) == 16
    assert body['layout_shape']['v'] == 2 and 'balance' in body['layout_shape']['headings']


# ── Karr's daily watch reads this file every day through the live parser ──

def test_karrs_self_test_statement_reads(tmp_path):
    """contract/v1/karr-self-test.pdf is what Karr's bank-read-watch builds (Karr pins its copy byte for byte).
    If this goes red, Karr's daily alarm would ring the day this parser is deployed: fix it here first."""
    path = os.path.join(os.path.dirname(__file__), '..', 'contract', 'v1', 'karr-self-test.pdf')
    from extractors.text_extractor import extract_text_info
    raw, info = extract_text_info(path, '.pdf')
    assert info['method'] == 'pdf_text'
    assert detect_bank(raw) is None, 'no bank parser may claim it: it is how Karr proves R2 every day'
    r = read_table(path, raw_text=raw)
    assert r is not None and len(r['transactions']) == 8
    assert r['transactions'][-1]['balance'] == '15,023.55'
    assert r['column_map']['amounts'] == 'two_columns'
