"""
Bank Statement Parser — Test Harness
======================================
Comprehensive test suite for detection accuracy, parsing correctness,
and balance verification across all supported bank parsers.

Usage:
    python tests/run_harness.py                  # Run all tests
    python tests/run_harness.py --pdf-dir .      # Test with real PDFs in current dir
    python tests/run_harness.py --bank sbi       # Test specific bank only
"""

import json
import os
import re
import sys
from datetime import datetime
from typing import Dict, List, Optional, Tuple

# Add parent dir to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from parsers.registry import detect_bank, get_parser, get_supported_banks
from parsers.base_parser import BaseBankParser


# ── Helper Functions ──

_pbv = BaseBankParser.parse_balance_value


def verify_balances(transactions: List[Dict], opening_balance: Optional[float] = None) -> Tuple[int, int, List[str]]:
    """
    Verify balance continuity across transactions.
    
    Returns:
        (matches, total, errors) — where errors are descriptive strings for failures
    """
    matches = 0
    total = 0
    errors = []
    
    prev_bal = opening_balance
    for i, t in enumerate(transactions):
        bal = _pbv(t.get('balance', ''))
        w = 0
        d = 0
        try:
            w = float(str(t.get('withdrawal', '')).replace(',', '')) if t.get('withdrawal') else 0
        except (ValueError, TypeError):
            pass
        try:
            d = float(str(t.get('deposit', '')).replace(',', '')) if t.get('deposit') else 0
        except (ValueError, TypeError):
            pass
        
        if i > 0 and prev_bal is not None and bal is not None:
            total += 1
            expected = round(prev_bal + d - w, 2)
            if abs(expected - bal) < 0.02:
                matches += 1
            else:
                errors.append(
                    f"Row {i+1}: prev={prev_bal:.2f} + D={d:.2f} - W={w:.2f} = {expected:.2f}, "
                    f"but balance={bal:.2f} (delta={abs(expected-bal):.2f})"
                )
        
        if bal is not None:
            prev_bal = bal
    
    return matches, total, errors


def test_detection(text: str, expected_bank: str) -> Dict:
    """Test bank detection on a text sample."""
    detected = detect_bank(text)
    
    # Get scores for all parsers
    scores = {}
    for bank_info in get_supported_banks():
        parser = get_parser(bank_info['code'])
        if parser:
            score, anchors = parser.detection_score(text)
            if score > 0:
                scores[bank_info['code']] = {'score': score, 'anchors': anchors}
    
    return {
        'detected': detected,
        'expected': expected_bank,
        'correct': detected == expected_bank,
        'scores': scores,
    }


def test_parse(bank_code: str, text: str) -> Dict:
    """Test parsing a bank statement."""
    parser = get_parser(bank_code)
    if not parser:
        return {'error': f'No parser for {bank_code}'}
    
    result = parser.parse(text)
    txns = result.get('transactions', [])
    
    # Get opening balance
    opening = _pbv(result.get('account_info', {}).get('opening_balance', ''))
    if opening is None and txns:
        opening = _pbv(txns[0].get('balance', ''))
    
    # Verify balances
    matches, total, errors = verify_balances(txns, opening)
    
    # Count empty fields
    empty_bal = sum(1 for t in txns if not t.get('balance'))
    empty_wd = sum(1 for t in txns if not t.get('withdrawal') and not t.get('deposit'))
    
    # Validate
    warnings = parser.validate(result)
    
    return {
        'bank_code': bank_code,
        'bank_name': result.get('bank_name', ''),
        'transaction_count': len(txns),
        'balance_verification': {
            'matches': matches,
            'total': total,
            'percentage': round(matches / total * 100, 1) if total else 0,
            'errors': errors[:5],  # First 5 errors only
        },
        'empty_balances': empty_bal,
        'empty_wd': empty_wd,
        'warnings': warnings,
        'account_info': result.get('account_info', {}),
        'period': result.get('period', 'N/A'),
    }


# ── Test Runners ──

def run_pdf_tests(pdf_dir: str, bank_filter: Optional[str] = None) -> List[Dict]:
    """Run tests on all PDF files in a directory."""
    results = []
    
    # Find PDF files
    pdfs = []
    for f in os.listdir(pdf_dir):
        if f.lower().endswith('.pdf'):
            pdfs.append(os.path.join(pdf_dir, f))
    
    if not pdfs:
        print(f"  No PDF files found in {pdf_dir}")
        return results
    
    try:
        import pdfplumber
    except ImportError:
        print("  ERROR: pdfplumber not installed")
        return results
    
    for pdf_path in sorted(pdfs):
        filename = os.path.basename(pdf_path)
        print(f"\n  Testing: {filename}")
        
        try:
            pdf = pdfplumber.open(pdf_path)
            text = '\n'.join([p.extract_text() or '' for p in pdf.pages])
            
            if not text.strip():
                print(f"    SKIP: empty text")
                continue
            
            # Detect bank
            detected = detect_bank(text)
            
            if bank_filter and detected != bank_filter:
                print(f"    SKIP: detected as {detected}, filter={bank_filter}")
                continue
            
            if not detected:
                print(f"    SKIP: could not detect bank")
                continue
            
            print(f"    Detected: {detected}")
            
            # Parse
            parse_result = test_parse(detected, text)
            parse_result['filename'] = filename
            parse_result['detected_as'] = detected
            
            # Print summary
            bv = parse_result['balance_verification']
            print(f"    Transactions: {parse_result['transaction_count']}")
            print(f"    Balance: {bv['matches']}/{bv['total']} ({bv['percentage']}%)")
            
            if bv['errors']:
                for err in bv['errors'][:3]:
                    print(f"      ⚠ {err}")
            
            if parse_result['warnings']:
                print(f"    Warnings: {', '.join(parse_result['warnings'])}")
            
            results.append(parse_result)
            
        except Exception as e:
            print(f"    ERROR: {e}")
            results.append({'filename': filename, 'error': str(e)})
    
    return results


def run_fixture_tests() -> List[Dict]:
    """Run tests on synthetic fixture files."""
    fixtures_dir = os.path.join(os.path.dirname(__file__), 'fixtures')
    gt_path = os.path.join(fixtures_dir, 'ground_truth.json')
    
    if not os.path.exists(gt_path):
        print("  No fixtures found. Run 'python tests/gen_fixtures.py' first.")
        return []
    
    with open(gt_path, 'r') as f:
        ground_truth = json.load(f)
    
    results = []
    
    for bank, gt in ground_truth.items():
        fixture_path = os.path.join(fixtures_dir, gt['fixture'])
        if not os.path.exists(fixture_path):
            print(f"  SKIP: {gt['fixture']} not found")
            continue
        
        with open(fixture_path, 'r') as f:
            text = f.read()
        
        # Test detection
        det = test_detection(text, bank)
        
        # Test parsing
        if det['detected']:
            parse_result = test_parse(det['detected'], text)
        else:
            parse_result = {'transaction_count': 0, 'balance_verification': {'matches': 0, 'total': 0, 'percentage': 0}}
        
        result = {
            'bank': bank,
            'fixture': gt['fixture'],
            'detection': det,
            'parse': parse_result,
            'ground_truth': {
                'transaction_count': gt['transaction_count'],
                'opening_balance': gt['opening_balance'],
                'closing_balance': gt['closing_balance'],
            },
        }
        results.append(result)
        
        det_status = '✓' if det['correct'] else '✗'
        bv = parse_result.get('balance_verification', {})
        print(f"  {det_status} {bank}: detected={det['detected']}, "
              f"txns={parse_result.get('transaction_count', '?')}/{gt['transaction_count']}, "
              f"bal={bv.get('percentage', 0)}%")
    
    return results


def print_summary(pdf_results: List[Dict], fixture_results: List[Dict]):
    """Print a summary table of all test results."""
    print("\n" + "=" * 80)
    print("  SUMMARY")
    print("=" * 80)
    
    all_results = []
    
    for r in pdf_results:
        if 'error' not in r:
            bv = r['balance_verification']
            all_results.append({
                'source': r.get('filename', 'unknown'),
                'bank': r.get('detected_as', '?'),
                'txns': r['transaction_count'],
                'bal_pct': bv['percentage'],
                'bal_str': f"{bv['matches']}/{bv['total']}",
            })
    
    for r in fixture_results:
        bv = r['parse'].get('balance_verification', {})
        all_results.append({
            'source': r['fixture'],
            'bank': r['bank'],
            'txns': r['parse'].get('transaction_count', 0),
            'bal_pct': bv.get('percentage', 0),
            'bal_str': f"{bv.get('matches', 0)}/{bv.get('total', 0)}",
            'detect_ok': r['detection']['correct'],
        })
    
    if all_results:
        print(f"\n  {'Source':<40} {'Bank':<15} {'Txns':>5}  {'Balance':>10}  {'%':>6}")
        print("  " + "-" * 78)
        
        for r in all_results:
            detect = ' ✓' if r.get('detect_ok', True) else ' ✗'
            print(f"  {r['source']:<40} {r['bank']:<15} {r['txns']:>5}  {r['bal_str']:>10}  {r['bal_pct']:>5.0f}%{detect}")
        
        # Overall stats
        total_txns = sum(r['txns'] for r in all_results)
        avg_pct = sum(r['bal_pct'] for r in all_results) / len(all_results) if all_results else 0
        print("  " + "-" * 78)
        print(f"  {'TOTAL':<40} {'':<15} {total_txns:>5}  {'':>10}  {avg_pct:>5.1f}%")


# ── Main ──

def main():
    import argparse
    
    parser = argparse.ArgumentParser(description='Bank Statement Parser Test Harness')
    parser.add_argument('--pdf-dir', default=None, help='Directory with PDF files to test')
    parser.add_argument('--bank', default=None, help='Filter by bank code')
    parser.add_argument('--fixtures', action='store_true', help='Run fixture tests')
    parser.add_argument('--all', action='store_true', help='Run all tests')
    
    args = parser.parse_args()
    
    # Default: run all tests
    if not args.pdf_dir and not args.fixtures:
        args.all = True
    
    print("=" * 80)
    print("  Bank Statement Parser — Test Harness")
    print(f"  {len(get_supported_banks())} parsers loaded")
    print("=" * 80)
    
    pdf_results = []
    fixture_results = []
    
    # Run PDF tests
    if args.pdf_dir or args.all:
        pdf_dir = args.pdf_dir or os.path.join(os.path.dirname(__file__), '..')
        print(f"\n─── PDF Tests ({pdf_dir}) ───")
        pdf_results = run_pdf_tests(pdf_dir, args.bank)
    
    # Run fixture tests
    if args.fixtures or args.all:
        print(f"\n─── Fixture Tests ───")
        fixture_results = run_fixture_tests()
    
    # Summary
    print_summary(pdf_results, fixture_results)
    
    # Return exit code
    all_ok = all(
        r.get('balance_verification', {}).get('percentage', 0) >= 90
        for r in pdf_results if 'error' not in r
    )
    return 0 if all_ok else 1


if __name__ == '__main__':
    sys.exit(main())
