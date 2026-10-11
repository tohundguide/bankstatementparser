"""
Contract v1 of /api/v1/parse (bsp-1, bsp-3): what the API answers, and what
it never does.

The sample under contract/v1/ is what build_api_response answers for the
synthetic statement below. Karr keeps a copy of the same sample and its own
test reads it (tests/contract/bankstatementparser/). If the builder's answer
changes, this goes red here, and the sample is regenerated on purpose:

    python tests/test_contract_v1.py --write

Karr's test then goes red until its copy is updated. Whichever side drifts,
someone is told.
"""
import io
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

# The API needs a key before app is imported (api_auth reads the env at import).
os.environ['API_KEYS'] = 'contract-test-key:ContractTest'

import api_auth  # noqa: E402
import app as parser_app  # noqa: E402
from app import PARSER_VERSION, _form_flag, _pages_stated, _period_bounds, build_api_response, post_process  # noqa: E402

api_auth.reload_keys()

CONTRACT_DIR = os.path.join(os.path.dirname(__file__), '..', 'contract', 'v1')
SAMPLE = os.path.join(CONTRACT_DIR, 'sample-v1-scan.json')
SCHEMA = os.path.join(CONTRACT_DIR, 'response.schema.json')
KEY = {'X-API-Key': 'contract-test-key'}


def synthetic_result():
    """An SBI scan: masked account, a printed period, two printed balances in five lines, an OCR warning."""
    return {
        'bank_name': 'State Bank of India',
        'bank_code': 'sbi',
        'account_info': {
            'account_number': 'XXXXXXX0193',
            'ifsc': 'sbin0001234',
            'opening_balance': '2,500.00 Dr',
            'closing_balance': '6,550.00',
        },
        'period': '01/05/2025 to 31/05/2025',
        'transactions': [
            {'date': '02-05-2025', 'value_date': '02-05-2025', 'particulars': 'BY TRANSFER-NEFT', 'chq_ref': 'UTR998877',
             'withdrawal': '', 'deposit': '10,000.00', 'balance': '7,500.00'},
            {'date': '10-05-2025', 'particulars': 'ATM WDL', 'chq_ref': '', 'withdrawal': '1,000.00', 'deposit': '', 'balance': ''},
            {'date': '15-05-2025', 'particulars': 'BALANCE CARRIED', 'chq_ref': '', 'withdrawal': '', 'deposit': '', 'balance': ''},
            {'date': '20-05-2025', 'particulars': 'INTEREST', 'chq_ref': '', 'withdrawal': '', 'deposit': '', 'balance': '6,550.00'},
            {'date': '31-05-2025', 'particulars': 'CLOSING', 'chq_ref': '', 'withdrawal': '', 'deposit': '', 'balance': ''},
        ],
        'warnings': ['Page 2 was read by OCR'],
    }


def build_sample():
    result = post_process(synthetic_result())
    return build_api_response(
        result, result['transactions'],
        used_llm=False, read_method='ocr', pages_seen=2, pages_stated=2,
        verification={'matched': 1, 'total': 1, 'percent': 100.0}, preview=[],
    )


def load(path):
    with open(path, encoding='utf-8') as f:
        return json.load(f)


# ── The sample, the schema and the builder agree ──

def test_sample_is_what_the_builder_answers():
    assert build_sample() == load(SAMPLE), 'contract/v1/sample-v1-scan.json is stale: python tests/test_contract_v1.py --write (and tell Karr)'


def test_every_sample_field_is_in_the_schema():
    schema = load(SCHEMA)
    sample = load(SAMPLE)
    assert set(sample) <= set(schema['properties']), set(sample) - set(schema['properties'])
    line_props = set(schema['$defs']['line']['properties'])
    for line in sample['transactions']:
        assert set(line) <= line_props, set(line) - line_props
    assert sample['read_by'] in schema['properties']['read_by']['enum']


def test_v1_fields():
    s = build_sample()
    assert s['parser_version'] == PARSER_VERSION
    assert s['account_number'] == 'XXXXXXX0193'
    assert s['ifsc'] == 'SBIN0001234'
    assert (s['period_from'], s['period_to']) == ('2025-05-01', '2025-05-31')
    assert s['period'] == '01/05/2025 to 31/05/2025', 'the pre-v1 sentence stays for the callers that read it'
    assert s['pages'] == {'seen': 2, 'stated': 2}
    assert s['read_by'] == 'ocr' and s['parsed_by'] == 'structured'
    assert [t['seq'] for t in s['transactions']] == [1, 2, 3, 4, 5]
    assert s['transactions'][0]['value_date'] == '02-05-2025'
    assert 'value_date' not in s['transactions'][1]


def test_computed_balances_and_inferred_amounts_are_marked():
    s = build_sample()
    t = s['transactions']
    assert 'balance_source' not in t[0] and t[0]['balance'] == '7,500.00', 'printed'
    assert t[1]['balance_source'] == 'computed' and t[1]['balance'] == '6500.00', '7,500 less the 1,000 withdrawal'
    assert t[2]['balance_source'] == 'computed' and t[2]['balance'] == '6500.00'
    assert 'balance_source' not in t[3] and t[3]['balance'] == '6,550.00', 'printed'
    assert t[3]['amount_source'] == 'inferred' and t[3]['deposit'] == '50.00', 'the balance moved by 50 with no amount printed'
    assert t[4]['balance_source'] == 'computed' and t[4]['balance'] == '6550.00', 'chains from the printed 6,550, not from a drifted running figure'
    assert s['filled'] == {'balances': 3, 'amounts': 1}
    assert len(s['warnings']) == 3 and 'computed' in s['warnings'][1] and 'inferred' in s['warnings'][2]


def test_the_pieces():
    assert _period_bounds('01/04/2025 to 30/04/2025') == ('2025-04-01', '2025-04-30')
    assert _period_bounds('1-Apr-2025 - 30-Apr-2025') == ('2025-04-01', '2025-04-30')
    assert _period_bounds('01 April 2025 – 30 April 2025') == ('2025-04-01', '2025-04-30')
    assert _period_bounds('N/A') == (None, None)
    assert _period_bounds('30/04/2025 to 01/04/2025') == (None, None), 'backwards is not a period'
    assert _pages_stated('Statement\nPage 1 of 12\n...\nPage 12 of 12') == 12
    assert _pages_stated('no page numbers here') is None
    assert _form_flag(None, default=True) is True
    assert _form_flag('', default=True) is True
    for no in ('false', 'False', '0', 'no', 'off'):
        assert _form_flag(no, default=True) is False
    assert _form_flag('true', default=False) is True


# ── The API path: allow_ai honoured, nothing kept ──

class _Parser:
    def __init__(self, result):
        self._result = result

    def parse(self, raw_text):
        return self._result


def _txt_upload(extra=None):
    text = 'STATE BANK OF INDIA\nAccount statement, enough text to pass the fifty character floor.\n' * 3
    data = {'file': (io.BytesIO(text.encode()), 'statement.txt')}
    data.update(extra or {})
    return data


def _api_uploads_left():
    return [f for f in os.listdir(parser_app.app.config['UPLOAD_FOLDER']) if f.startswith('api_')]


def test_allow_ai_false_never_reaches_the_ai(monkeypatch):
    monkeypatch.setattr(parser_app, 'detect_bank', lambda raw: None)
    monkeypatch.setattr(parser_app.llm_parser, 'is_available', lambda: True)

    def never(*a, **k):
        raise AssertionError('the AI was called with allow_ai=false')
    monkeypatch.setattr(parser_app.llm_parser, 'parse_with_llm', never)

    client = parser_app.app.test_client()
    res = client.post('/api/v1/parse', data=_txt_upload({'allow_ai': 'false'}), headers=KEY, content_type='multipart/form-data')
    body = res.get_json()
    assert res.status_code == 400
    assert body['code'] == 'PARSE_FAILED'
    assert body['ai_allowed'] is False and body['ai_available'] is True
    assert 'allow_ai=false' in body['error']
    assert res.headers['X-Parser-Version'] == PARSER_VERSION
    assert _api_uploads_left() == [], 'the file is gone once the answer is sent'


def test_allow_ai_default_asks_the_ai_without_learning(monkeypatch):
    monkeypatch.setattr(parser_app, 'detect_bank', lambda raw: None)
    monkeypatch.setattr(parser_app.llm_parser, 'is_available', lambda: True)
    calls = []

    def fake_llm(raw_text, persist=True):
        calls.append(persist)
        return None
    monkeypatch.setattr(parser_app.llm_parser, 'parse_with_llm', fake_llm)

    client = parser_app.app.test_client()
    res = client.post('/api/v1/parse', data=_txt_upload(), headers=KEY, content_type='multipart/form-data')
    assert res.status_code == 400 and res.get_json()['code'] == 'PARSE_FAILED'
    assert calls == [False], 'the API path asks the AI with persist=False: no cache, no learned profile'


def test_a_known_layout_answers_the_v1_contract(monkeypatch):
    monkeypatch.setattr(parser_app, 'detect_bank', lambda raw: 'sbi')
    monkeypatch.setattr(parser_app, 'get_parser', lambda code: _Parser(synthetic_result()))
    monkeypatch.setattr(parser_app.llm_parser, 'parse_with_llm', lambda *a, **k: (_ for _ in ()).throw(AssertionError('no AI for a known layout')))

    client = parser_app.app.test_client()
    res = client.post('/api/v1/parse', data=_txt_upload({'allow_ai': 'false'}), headers=KEY, content_type='multipart/form-data')
    body = res.get_json()
    assert res.status_code == 200, body
    assert res.headers['X-Parser-Version'] == PARSER_VERSION
    assert body['success'] is True and body['parser_version'] == PARSER_VERSION
    assert body['read_by'] == 'text', 'a .txt upload says so'
    assert body['pages'] == {'seen': None, 'stated': None}
    assert body['filled'] == {'balances': 3, 'amounts': 1}
    assert [t['seq'] for t in body['transactions']] == [1, 2, 3, 4, 5]
    assert body['verification']['total'] == 1, 'computed balances are not counted as matches'
    assert _api_uploads_left() == []


def test_status_says_the_contract():
    client = parser_app.app.test_client()
    body = client.get('/api/v1/status').get_json()
    assert body['version'] == PARSER_VERSION
    assert body['features']['allow_ai_param'] is True and body['features']['contract'] == 'v1'


if __name__ == '__main__':
    if '--write' in sys.argv:
        os.makedirs(CONTRACT_DIR, exist_ok=True)
        with open(SAMPLE, 'w', encoding='utf-8') as f:
            json.dump(build_sample(), f, indent=2, ensure_ascii=False)
            f.write('\n')
        print(f'wrote {SAMPLE}')
    else:
        print('run with pytest, or --write to regenerate the sample')
