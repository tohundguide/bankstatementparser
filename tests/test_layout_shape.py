"""
The layout fingerprint, v2 (2.5.0; Karr bankbest-i i-1): the golden vectors in
contract/v1/fingerprint-v2.json, the same file Karr's test asserts
(tests/contract/bankstatementparser/ there), so the browser's fingerprint and
the parser's are one rule. And nothing of a customer's ever enters a shape.
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from parsers.layout_shape import date_shape, fingerprint, layout_shape, shape_of_roles, shape_text  # noqa: E402
from parsers.table_reader import text_layout  # noqa: E402

VECTORS = json.load(open(os.path.join(os.path.dirname(__file__), '..', 'contract', 'v1', 'fingerprint-v2.json'), encoding='utf-8'))


def test_sha256_is_sha256():
    import hashlib
    for v in VECTORS['sha256']:
        assert hashlib.sha256(v['of'].encode('utf-8')).hexdigest() == v['hex']


def test_every_golden_case():
    for c in VECTORS['cases']:
        i = c['input']
        shape = layout_shape(i['headings'], i['kinds'], i['first_date'], i['order'], i['bank'])
        assert shape == c['shape'], c['name']
        assert shape_text(shape) == c['text'], c['name']
        assert fingerprint(shape) == c['fingerprint'], c['name']


def test_dates_by_their_shape():
    for d in VECTORS['dates']:
        assert date_shape(d['cell'], 'dmy') == d['dmy'], d['cell']
        assert date_shape(d['cell'], 'mdy') == d['mdy'], d['cell']


def test_a_pdf_shape_is_its_heading_roles():
    s = shape_of_roles(['date', 'value_date', 'narration', 'withdrawal', 'deposit', 'balance'], '01-Apr-2025', bank='hdfc_bank')
    assert s == {'v': 2, 'bank': 'hdfc_bank', 'headings': ['date', 'value date', 'narration', 'withdrawal', 'deposit', 'balance'],
                 'kinds': ['date', 'date', 'text', 'money', 'money', 'money'], 'date': 'dd-mmm-yyyy'}


def test_nothing_of_the_customer_enters_it():
    a = text_layout('Name: RAVI KUMAR 50100123456789\nDate Narration Withdrawal Deposit Balance\n01/04/2025 UPI RAVI 10.00 90.00\n')
    b = text_layout('Name: ANOTHER PERSON 1234\nDate Narration Withdrawal Deposit Balance\n11/05/2024 NEFT ACME 7.50 1,000.00\n')
    assert a == b
    text = shape_text(a[1]).lower()
    for secret in ('ravi', '5010', 'upi', '90.00', 'acme'):
        assert secret not in text
    assert a[0] != text_layout('Date Narration Debit Credit Balance\n01-Apr-2025 X 1.00 2.00\n')[0]


# ── "Send this statement to Karr" (2.5.0) ──

def _client(tmp_path, monkeypatch):
    os.environ['API_KEYS'] = 'contract-test-key:ContractTest'
    import api_auth
    api_auth.reload_keys()
    import app as parser_app
    monkeypatch.setitem(parser_app.app.config, 'FEEDBACK_FOLDER', str(tmp_path))
    return parser_app.app.test_client()


def test_a_sent_statement_is_kept_with_its_shape_only_and_forgotten_once_fixed(tmp_path, monkeypatch):
    import io
    client = _client(tmp_path, monkeypatch)
    key = {'X-API-Key': 'contract-test-key'}
    res = client.post('/api/v1/report', headers=key, content_type='multipart/form-data', data={
        'file': (io.BytesIO(b'%PDF-1.4 statement'), 'RAVI KUMAR HDFC April.pdf'),
        'fingerprint': '0123456789abcdef', 'reason': 'unknown_layout', 'consent': 'true', 'consented_at': '2026-11-21T10:00:00Z'})
    body = res.get_json()
    assert res.status_code == 200 and body['success'] and body['keep_days'] == 30
    kept = os.listdir(tmp_path)
    assert kept == [body['report_id']]
    files = sorted(os.listdir(tmp_path / kept[0]))
    assert files == ['metadata.json', 'statement.pdf'], 'the file name is not kept'
    meta = json.load(open(tmp_path / kept[0] / 'metadata.json'))
    assert 'RAVI' not in json.dumps(meta) and meta['fingerprint'] == '0123456789abcdef' and meta['source'] == 'karr'
    gone = client.post('/api/v1/report/forget', headers=key, json={'fingerprint': '0123456789abcdef'}).get_json()
    assert gone == {'success': True, 'deleted': 1} and os.listdir(tmp_path) == []


def test_no_consent_no_key_no_file(tmp_path, monkeypatch):
    import io
    client = _client(tmp_path, monkeypatch)
    no_consent = client.post('/api/v1/report', headers={'X-API-Key': 'contract-test-key'}, content_type='multipart/form-data',
                             data={'file': (io.BytesIO(b'x'), 's.pdf'), 'consent': 'false'})
    assert no_consent.status_code == 400
    no_key = client.post('/api/v1/report', content_type='multipart/form-data', data={'file': (io.BytesIO(b'x'), 's.pdf'), 'consent': 'true'})
    assert no_key.status_code in (401, 403)
    assert os.listdir(tmp_path) == []
