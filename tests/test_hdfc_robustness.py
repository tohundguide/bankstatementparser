"""
HDFC Bank Parser — Extraction-Robustness Regression Test
=========================================================
Locks in the fix for HDFC statements (e.g. the Seiza Ventures STARTUP CURRENT
ACCOUNT statement) that previously failed to parse because PDF text extraction
produces unstable spacing in HDFC's tightly-packed transaction table.

The same statement is fed through several spacing variants that real extractors
(pdfplumber / PyPDF2 / OCR) are known to emit:

  A. clean single spaces
  B. adjacent Deposit/Closing-Balance columns merged ("1,754.65169,445.52")
  D. date glued to the first narration word ("01/05/26NEFT...")
  E. value-date glued to the first amount
  G. both date-glued and amounts-merged (worst case)

Every variant must yield the same transactions with correct debit/credit
classification and an intact running balance.

Run:
    python tests/test_hdfc_robustness.py
"""

import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from parsers.hdfc_parser import HDFCBankParser
from parsers.registry import detect_bank
from parsers.base_parser import BaseBankParser

_pbv = BaseBankParser.parse_balance_value
FIXTURE = os.path.join(os.path.dirname(__file__), 'hdfc_seiza_fixture.txt')

EXPECTED_TXNS = 10
EXPECTED_OPENING = 167690.87


def _variants(base):
    """Produce realistic extraction-spacing variants of the same statement."""
    merge_last2 = re.sub(r'(\d[\d,]*\.\d{2}) (\d[\d,]*\.\d{2})$', r'\1\2',
                         base, flags=re.M)
    glue_date = re.sub(r'^(\d{2}/\d{2}/\d{2}) ', r'\1', base, flags=re.M)
    glue_vdate = re.sub(r'(\d{2}/\d{2}/\d{2}) (\d[\d,]*\.\d{2})', r'\1\2', base)
    worst = re.sub(r'(\d[\d,]*\.\d{2}) (\d[\d,]*\.\d{2})$', r'\1\2',
                   glue_date, flags=re.M)
    return {
        'A_clean': base,
        'B_merged_amounts': merge_last2,
        'D_glued_date': glue_date,
        'E_glued_vdate': glue_vdate,
        'G_worst_case': worst,
    }


def _verify_balance(txns, opening):
    prev = opening
    ok = tot = 0
    for t in txns:
        bal = _pbv(t['balance'])
        w = float(t['withdrawal'].replace(',', '')) if t['withdrawal'] else 0
        d = float(t['deposit'].replace(',', '')) if t['deposit'] else 0
        if prev is not None and bal is not None:
            tot += 1
            if abs(round(prev + d - w, 2) - bal) < 0.02:
                ok += 1
        if bal is not None:
            prev = bal
    return ok, tot


def main():
    with open(FIXTURE, encoding='utf-8') as f:
        base = f.read()

    failures = []

    # Detection must resolve to HDFC despite many other banks' IFSC handles
    # appearing inside UPI narrations.
    if detect_bank(base) != 'hdfc_bank':
        failures.append(f"detection: got {detect_bank(base)!r}, expected 'hdfc_bank'")

    parser = HDFCBankParser()

    # Account metadata must be fully extracted (space-tolerant).
    info = parser.parse(base)['account_info']
    for key, expected in [
        ('account_number', '50200087642434'),
        ('ifsc', 'HDFC0004139'),
    ]:
        if info.get(key) != expected:
            failures.append(f"account_info[{key}]: got {info.get(key)!r}, expected {expected!r}")
    if info.get('opening_balance') != f"{EXPECTED_OPENING:.2f}":
        failures.append(f"opening_balance: got {info.get('opening_balance')!r}")

    # Every spacing variant must parse identically and verify the balance chain.
    for name, text in _variants(base).items():
        result = parser.parse(text)
        txns = result['transactions']
        opening = _pbv(result['account_info'].get('opening_balance', ''))
        ok, tot = _verify_balance(txns, opening)
        empty = sum(1 for t in txns if not t['balance']
                    or (not t['withdrawal'] and not t['deposit']))
        status = 'OK ' if (len(txns) == EXPECTED_TXNS and ok == tot and empty == 0) else 'FAIL'
        print(f"  [{status}] {name:18} txns={len(txns)} balance={ok}/{tot} empty_fields={empty}")
        if status == 'FAIL':
            failures.append(f"variant {name}: txns={len(txns)} balance={ok}/{tot} empty={empty}")

    # Real scanned-PDF OCR output (Seiza Ventures, 130 txns). This is the actual
    # text Tesseract produces from the image-only PDF — pipes from gridlines,
    # stray commas, OCR-split refs. Must parse end-to-end with a clean balance.
    ocr_dump = os.path.join(os.path.dirname(__file__), 'seiza_ocr_dump.txt')
    if os.path.exists(ocr_dump):
        with open(ocr_dump, encoding='utf-8') as f:
            ocr_text = f.read()
        result = parser.parse(ocr_text)
        txns = result['transactions']
        opening = _pbv(result['account_info'].get('opening_balance', ''))
        ok, tot = _verify_balance(txns, opening)
        status = 'OK ' if (len(txns) == 130 and ok == tot and tot > 0) else 'FAIL'
        print(f"  [{status}] real_OCR_130txns   txns={len(txns)} balance={ok}/{tot}")
        if status == 'FAIL':
            failures.append(f"real OCR: txns={len(txns)} balance={ok}/{tot}")
        if detect_bank(ocr_text) != 'hdfc_bank':
            failures.append(f"real OCR detection: {detect_bank(ocr_text)!r}")
    else:
        print("  [SKIP] real_OCR_130txns   (seiza_ocr_dump.txt not present)")

    # Second real scanned statement ("Cafe SEIZA", May 2026, 129 txns). This one
    # exposed three OCR-robustness gaps, each locked in below:
    #   1. DETECTION: a counterparty IFSC in a narration ("NEFT DR-SBIN0007407-")
    #      hijacked detection to SBI -> 0 txns. The labelled-IFSC anchor must win.
    #   2. ACCOUNT NO: the leading 5 OCR'd as "$" ("Account No: $0200087642434").
    #   3. NARRATION BLEED: the end-of-statement summary block and OCR-typo'd page
    #      footers must not leak into the last transaction's narration.
    cafe_dump = os.path.join(os.path.dirname(__file__), 'cafe_seiza_ocr_dump.txt')
    if os.path.exists(cafe_dump):
        with open(cafe_dump, encoding='utf-8') as f:
            cafe_text = f.read()

        if detect_bank(cafe_text) != 'hdfc_bank':
            failures.append(f"cafe detection: got {detect_bank(cafe_text)!r}, expected 'hdfc_bank'")

        result = parser.parse(cafe_text)
        txns = result['transactions']
        cinfo = result['account_info']
        opening = _pbv(cinfo.get('opening_balance', ''))
        ok, tot = _verify_balance(txns, opening)

        if cinfo.get('account_number') != '50200087642434':
            failures.append(f"cafe account_number: got {cinfo.get('account_number')!r}")

        # The last narration must be clean of summary/footer bleed.
        last_narr = txns[-1]['particulars'] if txns else ''
        if re.search(r'Opening Balance|Closing Bal|Contents of|GSTIN', last_narr, re.I):
            failures.append(f"cafe last-narration bleed: {last_narr[:60]!r}")

        # 129 real rows; >=97% of balance deltas reconcile (a few isolated single-
        # digit OCR misreads in the source image are expected and tolerated).
        ratio = ok / tot if tot else 0
        status = 'OK ' if (len(txns) == 129 and ratio >= 0.97 and tot > 0) else 'FAIL'
        print(f"  [{status}] cafe_OCR_129txns   txns={len(txns)} balance={ok}/{tot}")
        if status == 'FAIL':
            failures.append(f"cafe OCR: txns={len(txns)} balance={ok}/{tot} ratio={ratio:.2f}")
    else:
        print("  [SKIP] cafe_OCR_129txns   (cafe_seiza_ocr_dump.txt not present)")

    print()
    if failures:
        print("FAILED:")
        for f in failures:
            print("  -", f)
        return 1
    print("All HDFC robustness checks passed.")
    return 0


if __name__ == '__main__':
    sys.exit(main())
