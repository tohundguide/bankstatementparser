"""
Bank Statement Parser - Main Application
=========================================
A modular, extensible bank statement parser that converts statements
from various banks and formats (PDF, DOCX, TXT) into clean Excel files.

Features:
  - Auto-detect bank format
  - OCR for scanned PDFs (Tesseract – free)
  - Password-protected PDF support
  - Batch file upload
  - Excel + CSV export
  - Monthly summary with charts
  - 100% free, no API costs

Architecture:
  - parsers/         -> One parser per bank format (plugin system)
  - extractors/      -> File format extractors (PDF, DOCX, TXT, OCR)
  - exporters/       -> Excel/CSV exporters
  - templates/       -> HTML templates for web UI
  - uploads/         -> Temporary uploaded files
  - output/          -> Generated files
"""

import os
import re
import uuid
import zipfile
import traceback
from datetime import datetime
from flask import Flask, render_template, request, send_file, jsonify, redirect, url_for

# Our modules
from extractors.text_extractor import extract_text, check_ocr_available
from parsers.registry import detect_bank, get_parser
from parsers.base_parser import BaseBankParser
from parsers import llm_parser
from exporters.excel_exporter import export_to_excel, export_to_csv
from api_auth import require_api_key

from flask_cors import CORS

app = Flask(__name__)

# Enable CORS:
# - Restrictive for web UI routes (tohundguide.com only)
# - Open for /api/* routes (third-party consumers call from anywhere)
CORS(app, resources={
    r'/api/*': {'origins': '*'},
    r'/*': {'origins': [
        'https://tohundguide.com',
        'https://www.tohundguide.com',
        'http://localhost:3000',
    ]},
}, supports_credentials=False)
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB max
app.config['UPLOAD_FOLDER'] = os.path.join(os.path.dirname(__file__), 'uploads')
app.config['OUTPUT_FOLDER'] = os.path.join(os.path.dirname(__file__), 'output')
app.config['FEEDBACK_FOLDER'] = os.path.join(os.path.dirname(__file__), 'feedback')

# Create necessary directories
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(app.config['OUTPUT_FOLDER'], exist_ok=True)
os.makedirs(app.config['FEEDBACK_FOLDER'], exist_ok=True)


# ── Universal post-processing: fixes balance gaps, detects order, verifies ──

_pbv = BaseBankParser.parse_balance_value  # shortcut


def _parse_date(d):
    """Parse DD-MM-YYYY or DD/MM/YYYY to datetime, or None."""
    if not d:
        return None
    for fmt in ('%d-%m-%Y', '%d/%m/%Y', '%d-%m-%y'):
        try:
            return datetime.strptime(d.strip(), fmt)
        except (ValueError, TypeError):
            continue
    return None


def post_process(result):
    """
    Universal post-processing that runs after every parser's output.
    
    This single function retroactively protects ALL parsers against:
    (a) Reverse-chronological order
    (b) Missing balance fields (auto-fill from opening balance)
    (c) Ambiguous W/D when both fields are empty but balance moves
    (d) Balance verification flags
    """
    txns = result.get('transactions', [])
    if not txns:
        return result

    # --- (a) Detect chronological direction from dates ---
    dates = [_parse_date(t.get('date', '')) for t in txns]
    valid_dates = [d for d in dates if d is not None]

    is_reverse = False
    if len(valid_dates) >= 2:
        is_reverse = valid_dates[0] > valid_dates[-1]

    # Work in chronological order (oldest first)
    ordered = list(reversed(txns)) if is_reverse else list(txns)

    # --- (b) Fill missing balances if we have opening balance + amounts ---
    acct_info = result.get('account_info', {})
    opening_str = acct_info.get('opening_balance')
    opening = _pbv(opening_str) if opening_str else None

    # If no opening_balance in account_info, try the first transaction's balance
    if opening is None and ordered:
        first_bal = _pbv(ordered[0].get('balance'))
        if first_bal is not None:
            opening = first_bal

    have_balances = sum(1 for t in ordered if _pbv(t.get('balance')) is not None)

    if have_balances < len(ordered) * 0.5 and opening is not None:
        running = opening
        for i, t in enumerate(ordered):
            if i == 0 and _pbv(t.get('balance')) is not None:
                running = _pbv(t.get('balance'))
                continue
            try:
                w = float(str(t.get('withdrawal', '')).replace(',', '')) if t.get('withdrawal') else 0
                d = float(str(t.get('deposit', '')).replace(',', '')) if t.get('deposit') else 0
            except (ValueError, TypeError):
                w, d = 0, 0
            running = running + d - w
            if _pbv(t.get('balance')) is None:
                t['balance'] = f'{running:.2f}'

    # --- (c) Infer W/D when both blank but balance moves ---
    prev = None
    for t in ordered:
        bal = _pbv(t.get('balance'))
        w = t.get('withdrawal', '')
        d = t.get('deposit', '')
        if prev is not None and bal is not None and not w and not d:
            delta = round(bal - prev, 2)
            if delta < 0:
                t['withdrawal'] = f'{abs(delta):.2f}'
            elif delta > 0:
                t['deposit'] = f'{delta:.2f}'
        if bal is not None:
            prev = bal

    return result

# ── Auto-cleanup: delete temp files older than 1 hour ──
import threading
import time as _time

def _cleanup_old_files():
    """Delete upload/output files older than 1 hour (runs in background)."""
    while True:
        _time.sleep(3600)  # Run every hour
        cutoff = _time.time() - 3600
        for folder in [app.config['UPLOAD_FOLDER'], app.config['OUTPUT_FOLDER']]:
            try:
                for f in os.listdir(folder):
                    fp = os.path.join(folder, f)
                    if os.path.isfile(fp) and os.path.getmtime(fp) < cutoff:
                        os.remove(fp)
            except OSError:
                pass

_cleanup_thread = threading.Thread(target=_cleanup_old_files, daemon=True)
_cleanup_thread.start()


@app.route('/llms.txt')
def llms_txt():
    """Serve AI-readable description (like robots.txt but for LLMs)."""
    return send_file(
        os.path.join(os.path.dirname(__file__), 'static', 'llms.txt'),
        mimetype='text/plain'
    )


@app.route('/')
def index():
    """Render the main upload page."""
    from parsers.registry import get_supported_banks
    banks = get_supported_banks()
    ocr_status = check_ocr_available()
    return render_template('index.html', banks=banks, ocr_status=ocr_status)


@app.route('/parse', methods=['POST'])
def parse_statement():
    """
    Handle file upload, detect bank, parse statement, and return Excel + CSV.
    
    Supports:
      - Single file upload
      - Password-protected PDFs
      - Scanned PDFs (via OCR)
      - Excel and CSV export
    """
    try:
        # 1. Validate and save the uploaded file
        if 'file' not in request.files:
            return jsonify({'error': 'No file uploaded'}), 400

        file = request.files['file']
        if file.filename == '':
            return jsonify({'error': 'No file selected'}), 400

        # Get original extension
        _, ext = os.path.splitext(file.filename)
        ext = ext.lower()
        
        if ext not in ['.pdf', '.docx', '.doc', '.txt', '.csv']:
            return jsonify({'error': f'Unsupported file format: {ext}. Supported: PDF, DOCX, TXT, CSV'}), 400

        # Save with unique name
        unique_id = str(uuid.uuid4())[:8]
        safe_filename = f"{unique_id}_{file.filename}"
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], safe_filename)
        file.save(filepath)

        # 2. Extract raw text (with optional password)
        password = request.form.get('password', '').strip() or None
        
        try:
            raw_text = extract_text(filepath, ext, password=password)
        except ValueError as e:
            error_msg = str(e)
            if 'password' in error_msg.lower():
                return jsonify({
                    'error': error_msg,
                    'needs_password': True
                }), 400
            elif 'OCR' in error_msg or 'scanned' in error_msg.lower():
                return jsonify({
                    'error': error_msg,
                    'needs_ocr': True
                }), 400
            raise
        
        if not raw_text or len(raw_text.strip()) < 50:
            return jsonify({'error': 'Could not extract meaningful text from the file. The file may be empty or in an unsupported format.'}), 400

        # 3. Detect bank or use user selection
        bank_code = request.form.get('bank', 'auto')
        used_llm = False
        
        if bank_code == 'auto':
            bank_code = detect_bank(raw_text)
        
        # 4. Parse the statement (structured parser first)
        result = None
        
        if bank_code:
            parser = get_parser(bank_code)
            if parser:
                result = parser.parse(raw_text)
                if result and result.get('transactions'):
                    result = post_process(result)
                else:
                    result = None
        
        # 4b. LLM Fallback: try AI parsing if structured parser failed
        if not result and llm_parser.is_available():
            print('  [LLM] Structured parser failed, trying AI fallback...')
            result = llm_parser.parse_with_llm(raw_text)
            if result and result.get('transactions'):
                used_llm = True
                print(f"  [LLM] Success! Parsed {len(result['transactions'])} transactions")
            else:
                result = None
                print('  [LLM] AI parsing also failed')
        
        # 4c. Everything failed: offer feedback collection
        if not result:
            feedback_id = str(uuid.uuid4())[:12]
            temp_path = os.path.join(app.config['UPLOAD_FOLDER'], f'pending_{feedback_id}{ext}')
            import shutil
            shutil.copy2(filepath, temp_path)
            
            llm_status = llm_parser.get_status()
            
            return jsonify({
                'error': 'Could not parse this bank statement. Help us improve by sharing this file!',
                'feedback_eligible': True,
                'feedback_id': feedback_id,
                'llm_available': llm_status['available'],
                'llm_remaining': llm_status['remaining'],
                'raw_preview': raw_text[:300],
            }), 400

        # 5. Export to Excel + CSV
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        safe_bank_name = re.sub(r'[^\w]+', '_', result['bank_name']).strip('_')
        
        # Excel
        excel_filename = f"Statement_{safe_bank_name}_{timestamp}.xlsx"
        excel_path = os.path.join(app.config['OUTPUT_FOLDER'], excel_filename)
        export_to_excel(result, excel_path)
        
        # CSV
        csv_filename = f"Statement_{safe_bank_name}_{timestamp}.csv"
        csv_path = os.path.join(app.config['OUTPUT_FOLDER'], csv_filename)
        export_to_csv(result, csv_path)

        # 6. Compute balance verification for preview
        _parse_balance_value = _pbv  # use centralized version
        transactions = result['transactions']
        prev_bal = None
        v_match = 0
        v_total = 0
        preview_rows = []
        for idx, t in enumerate(transactions[:10]):
            bal = _parse_balance_value(t['balance'])
            if idx == 0:
                chk = '○ Opening'
            elif prev_bal is not None and bal is not None:
                try:
                    w = float(str(t['withdrawal']).replace(',', '')) if t['withdrawal'] else 0
                    d = float(str(t['deposit']).replace(',', '')) if t['deposit'] else 0
                except (ValueError, TypeError):
                    w, d = 0, 0
                exp = prev_bal + d - w
                diff = bal - exp
                v_total += 1
                if abs(diff) < 0.02:
                    chk = '✓ Match'
                    v_match += 1
                else:
                    chk = f'✗ Diff: {diff:+,.2f}'
            else:
                chk = '? N/A'
            prev_bal = bal
            preview_rows.append({
                'date': t['date'],
                'particulars': t['particulars'],
                'chq_ref': t['chq_ref'],
                'withdrawal': t['withdrawal'],
                'deposit': t['deposit'],
                'balance': t['balance'],
                'check': chk,
            })
        
        v_pct = (v_match / v_total * 100) if v_total > 0 else 0

        # 7. Return result
        return jsonify({
            'success': True,
            'bank_name': result['bank_name'],
            'account_info': result.get('account_info', {}),
            'total_transactions': len(transactions),
            'period': result.get('period', 'N/A'),
            'download_url': url_for('download_file', filename=excel_filename),
            'excel_filename': excel_filename,
            'csv_download_url': url_for('download_file', filename=csv_filename),
            'csv_filename': csv_filename,
            'verification_stats': {
                'matched': v_match,
                'total': v_total,
                'percent': round(v_pct, 1),
            },
            'parsed_by': 'ai' if used_llm else 'structured',
            'preview': preview_rows,
        })


    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': f'Processing error: {str(e)}'}), 500

    finally:
        # Cleanup uploaded file
        try:
            if 'filepath' in locals() and os.path.exists(filepath):
                os.remove(filepath)
        except:
            pass


# ── Google Drive URL helpers ──

def _extract_gdrive_file_id(url: str) -> str:
    """
    Extract the Google Drive file ID from various URL formats:
      - https://drive.google.com/file/d/{ID}/view?usp=sharing
      - https://drive.google.com/open?id={ID}
      - https://drive.google.com/uc?id={ID}&export=download
      - https://docs.google.com/document/d/{ID}/...
      - https://docs.google.com/spreadsheets/d/{ID}/...
    Returns the file ID or None.
    """
    if not url:
        return None

    # Format: /file/d/{ID}/ or /document/d/{ID}/ or /spreadsheets/d/{ID}/
    match = re.search(r'/d/([a-zA-Z0-9_-]+)', url)
    if match:
        return match.group(1)

    # Format: ?id={ID} or &id={ID}
    match = re.search(r'[?&]id=([a-zA-Z0-9_-]+)', url)
    if match:
        return match.group(1)

    return None


def _download_from_gdrive(file_id: str, dest_path: str) -> dict:
    """
    Download a file from Google Drive by file ID.
    Handles the large-file virus-scan confirmation page.

    Returns:
        dict with 'success', 'filename' (from Content-Disposition), and 'error'.
    """
    import requests

    base_url = "https://drive.google.com/uc?export=download"
    session = requests.Session()

    # First request — may get a confirmation page for large files
    response = session.get(base_url, params={'id': file_id}, stream=True, timeout=60)

    # Check for the virus-scan confirmation token
    confirm_token = None
    for key, value in response.cookies.items():
        if key.startswith('download_warning'):
            confirm_token = value
            break

    if confirm_token:
        response = session.get(
            base_url,
            params={'id': file_id, 'confirm': confirm_token},
            stream=True,
            timeout=60,
        )

    # Check if we got an actual file (not an HTML error page)
    content_type = response.headers.get('Content-Type', '')
    if 'text/html' in content_type and response.status_code == 200:
        # Could be a "you need access" page or Google's error page
        snippet = response.text[:500].lower()
        if 'sign in' in snippet or 'request access' in snippet:
            return {'success': False, 'error': 'File is not publicly shared. Please set sharing to "Anyone with the link".'}
        if 'quota' in snippet:
            return {'success': False, 'error': 'Google Drive download quota exceeded. Try again later.'}
        # Generic HTML response — likely not a real file
        return {'success': False, 'error': 'Could not download file. Ensure the link is a direct file (not a folder) and is publicly shared.'}

    if response.status_code != 200:
        return {'success': False, 'error': f'Google Drive returned HTTP {response.status_code}'}

    # Try to get the original filename from Content-Disposition
    original_filename = None
    cd = response.headers.get('Content-Disposition', '')
    if cd:
        # Try filename*= (RFC 5987)
        match = re.search(r"filename\*=(?:UTF-8''|utf-8'')(.+?)(?:;|$)", cd)
        if match:
            from urllib.parse import unquote
            original_filename = unquote(match.group(1).strip())
        else:
            # Try filename=
            match = re.search(r'filename="?([^";\n]+)"?', cd)
            if match:
                original_filename = match.group(1).strip()

    # Write the file
    with open(dest_path, 'wb') as f:
        for chunk in response.iter_content(chunk_size=32768):
            if chunk:
                f.write(chunk)

    return {'success': True, 'filename': original_filename}


def _download_from_direct_url(url: str, dest_path: str) -> dict:
    """
    Download a file from a direct (non-Google-Drive) URL.
    """
    import requests

    try:
        response = requests.get(url, stream=True, timeout=60, allow_redirects=True)
        if response.status_code != 200:
            return {'success': False, 'error': f'HTTP {response.status_code} when downloading file'}

        # Get filename from Content-Disposition or URL
        original_filename = None
        cd = response.headers.get('Content-Disposition', '')
        if cd:
            match = re.search(r'filename="?([^";\n]+)"?', cd)
            if match:
                original_filename = match.group(1).strip()

        if not original_filename:
            from urllib.parse import urlparse
            original_filename = os.path.basename(urlparse(url).path) or None

        with open(dest_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=32768):
                if chunk:
                    f.write(chunk)

        return {'success': True, 'filename': original_filename}

    except requests.exceptions.Timeout:
        return {'success': False, 'error': 'Download timed out (60s). File may be too large or server unreachable.'}
    except requests.exceptions.RequestException as e:
        return {'success': False, 'error': f'Download failed: {str(e)}'}


@app.route('/parse-url', methods=['POST'])
def parse_from_url():
    """
    Parse a bank statement from a Google Drive URL (or any direct file URL).

    Accepts JSON body:
      {
        "url": "https://drive.google.com/file/d/.../view?usp=sharing",
        "password": "optional-pdf-password",
        "bank": "auto"   // or a specific bank code like "jk_bank"
      }

    Returns JSON with parsed transactions, account info, balance verification, etc.
    """
    filepath = None
    try:
        # 1. Parse request
        data = request.get_json(silent=True)
        if not data:
            # Also accept form data
            data = {
                'url': request.form.get('url', ''),
                'password': request.form.get('password', ''),
                'bank': request.form.get('bank', 'auto'),
            }

        url = (data.get('url') or '').strip()
        if not url:
            return jsonify({'error': 'Missing "url" parameter. Provide a Google Drive or direct file URL.'}), 400

        password = (data.get('password') or '').strip() or None
        bank_code = (data.get('bank') or 'auto').strip()

        # 2. Download the file
        unique_id = str(uuid.uuid4())[:8]
        temp_path = os.path.join(app.config['UPLOAD_FOLDER'], f'url_{unique_id}_download')

        gdrive_id = _extract_gdrive_file_id(url)
        if gdrive_id:
            dl_result = _download_from_gdrive(gdrive_id, temp_path)
        else:
            dl_result = _download_from_direct_url(url, temp_path)

        if not dl_result['success']:
            return jsonify({'error': dl_result['error']}), 400

        # Determine file extension from original filename
        original_filename = dl_result.get('filename') or ''
        _, ext = os.path.splitext(original_filename)
        ext = ext.lower()

        # If no extension from filename, try to guess from content
        if ext not in ['.pdf', '.docx', '.doc', '.txt', '.csv']:
            # Sniff the file header
            with open(temp_path, 'rb') as f:
                header = f.read(8)
            if header[:4] == b'%PDF':
                ext = '.pdf'
            elif header[:4] == b'PK\x03\x04':
                ext = '.docx'
            else:
                ext = '.txt'

        # Rename with correct extension
        filepath = temp_path + ext
        os.rename(temp_path, filepath)

        # Validate file size
        file_size = os.path.getsize(filepath)
        if file_size < 10:
            return jsonify({'error': 'Downloaded file is empty or too small.'}), 400
        if file_size > 50 * 1024 * 1024:
            return jsonify({'error': 'File exceeds 50MB limit.'}), 400

        # 3. Extract raw text
        try:
            raw_text = extract_text(filepath, ext, password=password)
        except ValueError as e:
            error_msg = str(e)
            if 'password' in error_msg.lower():
                return jsonify({'error': error_msg, 'needs_password': True}), 400
            elif 'OCR' in error_msg or 'scanned' in error_msg.lower():
                return jsonify({'error': error_msg, 'needs_ocr': True}), 400
            raise

        if not raw_text or len(raw_text.strip()) < 50:
            return jsonify({'error': 'Could not extract meaningful text from the downloaded file.'}), 400

        # 4. Detect bank
        used_llm = False
        if bank_code == 'auto':
            bank_code = detect_bank(raw_text)

        # 5. Parse the statement
        result = None
        if bank_code:
            parser = get_parser(bank_code)
            if parser:
                result = parser.parse(raw_text)
                if result and result.get('transactions'):
                    result = post_process(result)
                else:
                    result = None

        # 5b. LLM fallback
        if not result and llm_parser.is_available():
            result = llm_parser.parse_with_llm(raw_text)
            if result and result.get('transactions'):
                used_llm = True
            else:
                result = None

        # 5c. Parsing failed
        if not result:
            return jsonify({
                'error': 'Could not parse this bank statement.',
                'raw_preview': raw_text[:300],
                'source_url': url,
            }), 400

        # 6. Build full JSON response with balance verification
        _parse_balance_value = _pbv  # use centralized version
        transactions = result['transactions']

        # Balance verification for ALL transactions
        prev_bal = None
        verified_transactions = []
        v_match = 0
        v_total = 0

        for idx, t in enumerate(transactions):
            bal = _parse_balance_value(t['balance'])
            if idx == 0:
                check = 'opening'
            elif prev_bal is not None and bal is not None:
                try:
                    w = float(str(t['withdrawal']).replace(',', '')) if t['withdrawal'] else 0
                    d = float(str(t['deposit']).replace(',', '')) if t['deposit'] else 0
                except (ValueError, TypeError):
                    w, d = 0, 0
                exp = prev_bal + d - w
                diff = bal - exp
                v_total += 1
                if abs(diff) < 0.02:
                    check = 'match'
                    v_match += 1
                else:
                    check = f'mismatch:{diff:+.2f}'
            else:
                check = 'na'
            prev_bal = bal

            verified_transactions.append({
                'index': idx + 1,
                'date': t['date'],
                'particulars': t['particulars'],
                'chq_ref': t['chq_ref'],
                'withdrawal': t['withdrawal'],
                'deposit': t['deposit'],
                'balance': t['balance'],
                'balance_check': check,
            })

        v_pct = (v_match / v_total * 100) if v_total > 0 else 0

        # Monthly summary
        monthly = {}
        for t in transactions:
            try:
                # Parse date — try common formats
                dt = None
                for fmt in ('%d/%m/%Y', '%d-%m-%Y', '%Y-%m-%d', '%d/%m/%y', '%d-%m-%y', '%d %b %Y', '%d-%b-%Y'):
                    try:
                        dt = datetime.strptime(t['date'].strip(), fmt)
                        break
                    except ValueError:
                        continue
                if not dt:
                    continue
                month_key = dt.strftime('%Y-%m')
                if month_key not in monthly:
                    monthly[month_key] = {'month': month_key, 'withdrawals': 0, 'deposits': 0, 'count': 0}
                try:
                    w = float(str(t['withdrawal']).replace(',', '')) if t['withdrawal'] else 0
                    d = float(str(t['deposit']).replace(',', '')) if t['deposit'] else 0
                except (ValueError, TypeError):
                    w, d = 0, 0
                monthly[month_key]['withdrawals'] += w
                monthly[month_key]['deposits'] += d
                monthly[month_key]['count'] += 1
            except Exception:
                continue

        monthly_summary = sorted(monthly.values(), key=lambda x: x['month'])
        for m in monthly_summary:
            m['withdrawals'] = round(m['withdrawals'], 2)
            m['deposits'] = round(m['deposits'], 2)
            m['net'] = round(m['deposits'] - m['withdrawals'], 2)

        # 7. Return full JSON
        return jsonify({
            'success': True,
            'source_url': url,
            'parsed_by': 'ai' if used_llm else 'structured',
            'bank_name': result['bank_name'],
            'account_info': result.get('account_info', {}),
            'period': result.get('period', 'N/A'),
            'total_transactions': len(transactions),
            'verification': {
                'matched': v_match,
                'total_checked': v_total,
                'match_percent': round(v_pct, 1),
            },
            'monthly_summary': monthly_summary,
            'transactions': verified_transactions,
        })

    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': f'Processing error: {str(e)}'}), 500

    finally:
        # Cleanup downloaded file
        try:
            if filepath and os.path.exists(filepath):
                os.remove(filepath)
        except:
            pass
        # Also remove the temp file without extension (in case rename failed)
        try:
            base = filepath.rsplit('.', 1)[0] if filepath else None
            if base and os.path.exists(base):
                os.remove(base)
        except:
            pass


@app.route('/feedback', methods=['POST'])
def submit_feedback():
    """
    Accept a failed-parse file for improvement (with user consent).
    
    Expected JSON body:
      - feedback_id: ID from the failed parse response
      - bank_name: User-reported bank name
      - consent: Must be true
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No data provided'}), 400
        
        feedback_id = data.get('feedback_id', '')
        bank_name = data.get('bank_name', 'Unknown').strip()
        consent = data.get('consent', False)
        
        if not consent:
            return jsonify({'error': 'User consent is required'}), 400
        
        if not feedback_id:
            return jsonify({'error': 'No feedback ID provided'}), 400
        
        # Find the pending file
        pending_files = [f for f in os.listdir(app.config['UPLOAD_FOLDER']) 
                        if f.startswith(f'pending_{feedback_id}')]
        
        if not pending_files:
            return jsonify({'error': 'Feedback session expired. Please try again.'}), 404
        
        pending_file = pending_files[0]
        pending_path = os.path.join(app.config['UPLOAD_FOLDER'], pending_file)
        
        # Save to feedback folder with metadata
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        safe_bank = re.sub(r'[^\w]+', '_', bank_name).strip('_')[:30]
        
        import hashlib
        with open(pending_path, 'rb') as f:
            file_hash = hashlib.md5(f.read()).hexdigest()[:10]
        
        feedback_name = f"{timestamp}_{safe_bank}_{file_hash}"
        feedback_dir = os.path.join(app.config['FEEDBACK_FOLDER'], feedback_name)
        os.makedirs(feedback_dir, exist_ok=True)
        
        # Copy the file
        _, ext = os.path.splitext(pending_file)
        import shutil
        dest_file = os.path.join(feedback_dir, f'statement{ext}')
        shutil.move(pending_path, dest_file)
        
        # Save metadata
        import json
        metadata = {
            'bank_name': bank_name,
            'submitted_at': datetime.now().isoformat(),
            'file_hash': file_hash,
            'file_ext': ext,
            'consent': True,
        }
        with open(os.path.join(feedback_dir, 'metadata.json'), 'w') as f:
            json.dump(metadata, f, indent=2)
        
        # Extract and save raw text for analysis
        try:
            raw_text = extract_text(dest_file, ext)
            if raw_text:
                with open(os.path.join(feedback_dir, 'raw_text.txt'), 'w', encoding='utf-8') as f:
                    f.write(raw_text)
        except Exception:
            pass
        
        print(f"  [FEEDBACK] Saved feedback: {feedback_name} (bank: {bank_name})")
        
        return jsonify({
            'success': True,
            'message': 'Thank you! Your file has been saved. We will add support for this bank format soon.',
        })
    
    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': f'Feedback submission error: {str(e)}'}), 500


@app.route('/feedback/stats')
def feedback_stats():
    """Return stats on collected feedback files."""
    feedback_dir = app.config['FEEDBACK_FOLDER']
    entries = []
    for name in os.listdir(feedback_dir):
        meta_path = os.path.join(feedback_dir, name, 'metadata.json')
        if os.path.exists(meta_path):
            import json
            with open(meta_path) as f:
                meta = json.load(f)
            entries.append({
                'bank': meta.get('bank_name', '?'),
                'date': meta.get('submitted_at', '?'),
            })
    
    return jsonify({
        'total': len(entries),
        'entries': entries,
    })

@app.route('/batch', methods=['POST'])
def batch_parse():
    """
    Handle batch file upload — parse multiple statements and return a ZIP.
    """
    try:
        files = request.files.getlist('files')
        if not files or all(f.filename == '' for f in files):
            return jsonify({'error': 'No files uploaded'}), 400
        
        password = request.form.get('password', '').strip() or None
        bank_code = request.form.get('bank', 'auto')
        
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        results = []
        excel_paths = []
        csv_paths = []
        errors = []
        
        for file in files:
            if file.filename == '':
                continue
            
            _, ext = os.path.splitext(file.filename)
            ext = ext.lower()
            
            if ext not in ['.pdf', '.docx', '.doc', '.txt', '.csv']:
                errors.append({'file': file.filename, 'error': f'Unsupported format: {ext}'})
                continue
            
            # Save
            unique_id = str(uuid.uuid4())[:8]
            safe_fn = f"{unique_id}_{file.filename}"
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], safe_fn)
            file.save(filepath)
            
            try:
                raw_text = extract_text(filepath, ext, password=password)
                if not raw_text or len(raw_text.strip()) < 50:
                    errors.append({'file': file.filename, 'error': 'No text could be extracted'})
                    continue
                
                detected = bank_code if bank_code != 'auto' else detect_bank(raw_text)
                if not detected:
                    errors.append({'file': file.filename, 'error': 'Could not detect bank format'})
                    continue
                
                parser = get_parser(detected)
                if not parser:
                    errors.append({'file': file.filename, 'error': f'No parser for: {detected}'})
                    continue
                
                result = parser.parse(raw_text)
                result = post_process(result)
                
                if not result['transactions']:
                    errors.append({'file': file.filename, 'error': 'No transactions parsed'})
                    continue
                
                # Export
                safe_name = re.sub(r'[^\w]+', '_', os.path.splitext(file.filename)[0]).strip('_')
                safe_bank = re.sub(r'[^\w]+', '_', result['bank_name']).strip('_')
                
                excel_fn = f"{safe_name}_{safe_bank}.xlsx"
                excel_path = os.path.join(app.config['OUTPUT_FOLDER'], excel_fn)
                export_to_excel(result, excel_path)
                excel_paths.append(excel_path)
                
                csv_fn = f"{safe_name}_{safe_bank}.csv"
                csv_path = os.path.join(app.config['OUTPUT_FOLDER'], csv_fn)
                export_to_csv(result, csv_path)
                csv_paths.append(csv_path)
                
                results.append({
                    'file': file.filename,
                    'bank': result['bank_name'],
                    'transactions': len(result['transactions']),
                    'period': result.get('period', 'N/A'),
                })
                
            except Exception as e:
                errors.append({'file': file.filename, 'error': str(e)})
            finally:
                try:
                    os.remove(filepath)
                except:
                    pass
        
        if not results:
            return jsonify({
                'error': 'No files could be parsed successfully.',
                'details': errors
            }), 400
        
        # Create ZIP if multiple files
        if len(excel_paths) > 1:
            zip_filename = f"Batch_Statements_{timestamp}.zip"
            zip_path = os.path.join(app.config['OUTPUT_FOLDER'], zip_filename)
            
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                for ep in excel_paths:
                    zf.write(ep, os.path.basename(ep))
                for cp in csv_paths:
                    zf.write(cp, os.path.basename(cp))
            
            download_url = url_for('download_file', filename=zip_filename)
            download_name = zip_filename
        else:
            download_url = url_for('download_file', filename=os.path.basename(excel_paths[0]))
            download_name = os.path.basename(excel_paths[0])
        
        return jsonify({
            'success': True,
            'batch': True,
            'results': results,
            'errors': errors,
            'total_files': len(results),
            'total_transactions': sum(r['transactions'] for r in results),
            'download_url': download_url,
            'download_name': download_name,
        })
    
    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': f'Batch processing error: {str(e)}'}), 500


@app.route('/download/<filename>')
def download_file(filename):
    """Serve the generated file for download."""
    filepath = os.path.join(app.config['OUTPUT_FOLDER'], filename)
    if not os.path.exists(filepath):
        return jsonify({'error': 'File not found. It may have expired.'}), 404
    
    # Determine MIME type
    if filename.endswith('.xlsx'):
        mimetype = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    elif filename.endswith('.csv'):
        mimetype = 'text/csv'
    elif filename.endswith('.zip'):
        mimetype = 'application/zip'
    else:
        mimetype = 'application/octet-stream'
    
    # Read file and build response manually for maximum browser compatibility
    from flask import make_response
    from urllib.parse import quote
    
    with open(filepath, 'rb') as f:
        data = f.read()
    
    response = make_response(data)
    response.headers['Content-Type'] = mimetype
    response.headers['Content-Length'] = len(data)
    # RFC 5987 compliant Content-Disposition with both ASCII and UTF-8 filenames
    safe_filename = quote(filename)
    response.headers['Content-Disposition'] = (
        f"attachment; filename=\"{filename}\"; filename*=UTF-8''{safe_filename}"
    )
    # Extra header as fallback signal
    response.headers['X-Download-Filename'] = filename
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    
    return response


@app.route('/status')
def status():
    """API endpoint to check system capabilities."""
    from parsers.registry import get_supported_banks
    ocr = check_ocr_available()
    banks = get_supported_banks()
    return jsonify({
        'version': '2.1',
        'banks': [{'code': b['code'], 'name': b['name']} for b in banks],
        'ocr': ocr,
        'llm': llm_parser.get_status(),
        'features': {
            'excel_export': True,
            'csv_export': True,
            'batch_upload': True,
            'monthly_summary': True,
            'password_pdfs': True,
            'ocr_scanned_pdfs': ocr['available'],
            'ai_fallback': llm_parser.is_available(),
            'feedback_collection': True,
        }
    })


# ══════════════════════════════════════════════════════════════
#                    PUBLIC API (v1)
# ══════════════════════════════════════════════════════════════

@app.route('/api/v1/docs')
def api_docs():
    """Serve the API documentation page (no auth required)."""
    from parsers.registry import get_supported_banks
    banks = get_supported_banks()
    rate_limit = int(os.environ.get('API_RATE_LIMIT', '30'))
    base_url = request.host_url.rstrip('/')
    return render_template(
        'api_docs.html',
        banks=banks,
        rate_limit=rate_limit,
        base_url=base_url,
        ai_available=llm_parser.is_available(),
    )


@app.route('/api/v1/status')
def api_status():
    """Public API status — no auth required. Same as /status but under /api/v1."""
    from parsers.registry import get_supported_banks
    ocr = check_ocr_available()
    banks = get_supported_banks()
    return jsonify({
        'version': '2.1',
        'banks': [{'code': b['code'], 'name': b['name']} for b in banks],
        'ocr': ocr,
        'llm': llm_parser.get_status(),
        'features': {
            'excel_export': True,
            'csv_export': True,
            'batch_upload': True,
            'monthly_summary': True,
            'password_pdfs': True,
            'ocr_scanned_pdfs': ocr['available'],
            'ai_fallback': llm_parser.is_available(),
        }
    })


# ── URL Download Helper (for API) ──

def _download_from_url(url: str, upload_folder: str) -> tuple[str, str]:
    """
    Download a file from a URL (Google Drive, Dropbox, OneDrive, or direct).
    
    Returns: (filepath, original_filename)
    Raises: ValueError on failure.
    """
    import urllib.request
    import urllib.error
    from urllib.parse import urlparse, parse_qs
    
    original_url = url
    filename = None
    
    # ── Google Docs/Sheets/Slides URL handling ──
    # These need export URLs (they're not raw files, they're Google's native format)
    if 'docs.google.com' in url:
        # Extract file ID from docs.google.com/spreadsheets/d/ID/edit or similar
        import re as _re
        doc_match = _re.search(r'docs\.google\.com/(?:spreadsheets|document|presentation)/d/([a-zA-Z0-9_-]+)', url)
        if doc_match:
            file_id = doc_match.group(1)
            doc_type = 'spreadsheets' if 'spreadsheets' in url else ('document' if 'document' in url else 'presentation')
            
            if doc_type == 'spreadsheets':
                # Export Google Sheet as XLSX (the parser can handle xlsx via openpyxl)
                # But actually we need CSV for text extraction
                url = f'https://docs.google.com/spreadsheets/d/{file_id}/export?format=csv'
                filename = 'google_sheet_export.csv'
            elif doc_type == 'document':
                url = f'https://docs.google.com/document/d/{file_id}/export?format=pdf'
                filename = 'google_doc_export.pdf'
            else:
                url = f'https://docs.google.com/presentation/d/{file_id}/export/pdf'
                filename = 'google_slides_export.pdf'
            
            print(f"  [API] Google Docs export URL: {url}")
        else:
            raise ValueError('Could not extract file ID from Google Docs URL.')
    
    # ── Google Drive URL handling ──
    elif 'drive.google.com' in url:
        # Extract file ID from various Google Drive URL formats
        file_id = None
        
        if '/file/d/' in url:
            # https://drive.google.com/file/d/FILE_ID/view?usp=sharing
            parts = url.split('/file/d/')
            if len(parts) > 1:
                file_id = parts[1].split('/')[0].split('?')[0]
        elif 'id=' in url:
            # https://drive.google.com/open?id=FILE_ID
            parsed = urlparse(url)
            params = parse_qs(parsed.query)
            file_id = params.get('id', [None])[0]
        
        if not file_id:
            raise ValueError('Could not extract file ID from Google Drive URL. Use format: https://drive.google.com/file/d/FILE_ID/view')
        
        # Convert to direct download URL
        url = f'https://drive.google.com/uc?export=download&id={file_id}'
    
    # ── Dropbox URL handling ──
    elif 'dropbox.com' in url:
        # Replace dl=0 with dl=1 for direct download
        url = url.replace('dl=0', 'dl=1')
        if 'dl=1' not in url:
            url += ('&' if '?' in url else '?') + 'dl=1'
    
    # ── OneDrive URL handling ──
    elif '1drv.ms' in url or 'onedrive.live.com' in url:
        url = url.replace('redir?', 'download?')
    
    # ── Download the file ──
    try:
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (BankStatementParser/2.1)',
        })
        
        with urllib.request.urlopen(req, timeout=60) as response:
            # Check for Google Drive large file confirmation page
            content_type = response.headers.get('Content-Type', '')
            
            if 'text/html' in content_type and 'drive.google.com' in original_url:
                # Google Drive serves an HTML confirmation page for large files
                html = response.read().decode('utf-8', errors='ignore')
                
                # Look for the confirm token
                import re as _re
                confirm_match = _re.search(r'confirm=([0-9A-Za-z_-]+)', html)
                if confirm_match:
                    confirm_token = confirm_match.group(1)
                    # Extract file_id again
                    file_id_match = _re.search(r'id=([0-9A-Za-z_-]+)', url)
                    if file_id_match:
                        confirmed_url = f'https://drive.google.com/uc?export=download&confirm={confirm_token}&id={file_id_match.group(1)}'
                        req2 = urllib.request.Request(confirmed_url, headers={
                            'User-Agent': 'Mozilla/5.0 (BankStatementParser/2.1)',
                        })
                        with urllib.request.urlopen(req2, timeout=60) as resp2:
                            data = resp2.read()
                            # Try to get filename from Content-Disposition
                            cd = resp2.headers.get('Content-Disposition', '')
                            if 'filename=' in cd:
                                filename = cd.split('filename=')[-1].strip('"\'')
                else:
                    raise ValueError('Google Drive file is too large or requires sign-in. Make sure the file is publicly shared.')
            else:
                data = response.read()
                # Try to get filename from Content-Disposition
                cd = response.headers.get('Content-Disposition', '')
                if 'filename=' in cd:
                    filename = cd.split('filename=')[-1].strip('"\'')
        
        # Size check
        max_size = 50 * 1024 * 1024  # 50MB
        if len(data) > max_size:
            raise ValueError(f'File too large ({len(data) / 1024 / 1024:.1f}MB). Maximum: 50MB.')
        
        if len(data) < 100:
            raise ValueError('Downloaded file is too small or empty. Check the URL and sharing permissions.')
        
        # Determine filename and extension
        if not filename:
            # Try to guess from URL path
            parsed = urlparse(original_url)
            path_parts = parsed.path.rstrip('/').split('/')
            for part in reversed(path_parts):
                if '.' in part and not part.startswith('.'):
                    filename = part
                    break
        
        if not filename:
            # Default filename — try to detect from content
            if data[:4] == b'%PDF':
                filename = 'downloaded_statement.pdf'
            elif data[:2] == b'PK':  # ZIP-based (DOCX)
                filename = 'downloaded_statement.docx'
            else:
                filename = 'downloaded_statement.pdf'  # assume PDF
        
        # Save to uploads
        unique_id = str(uuid.uuid4())[:8]
        safe_name = f"api_url_{unique_id}_{filename}"
        filepath = os.path.join(upload_folder, safe_name)
        
        with open(filepath, 'wb') as f:
            f.write(data)
        
        return filepath, filename
        
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise ValueError('File not found at the given URL (404). Check the link.')
        elif e.code == 403:
            raise ValueError('Access denied (403). Make sure the file is publicly shared.')
        else:
            raise ValueError(f'Failed to download file: HTTP {e.code}')
    except urllib.error.URLError as e:
        raise ValueError(f'Could not reach the URL: {str(e.reason)}')
    except TimeoutError:
        raise ValueError('Download timed out (60s). The file may be too large or the server is slow.')


@app.route('/api/v1/parse', methods=['POST'])
@require_api_key
def api_parse():
    """
    Public API: Parse a bank statement and return structured JSON.

    Unlike the web /parse endpoint, this returns the FULL transactions
    array in the JSON response — the core value for automation.

    Form fields:
      - file (optional): The bank statement file upload
      - url (optional): URL to download the file from (Google Drive, Dropbox, direct link)
      - bank (optional, default 'auto'): Bank code or 'auto'
      - password (optional): PDF password
      - format (optional, default 'json'): 'json' or 'excel'
    
    Either 'file' or 'url' must be provided.
    """
    filepath = None
    try:
        # 1. Get file — either from upload or URL download
        file_url = request.form.get('url', '').strip()
        has_file = 'file' in request.files and request.files['file'].filename != ''

        if not has_file and not file_url:
            return jsonify({
                'error': 'No file provided. Upload a file OR provide a URL (Google Drive, Dropbox, direct link).',
                'code': 'NO_FILE',
            }), 400

        if file_url:
            # Download from URL
            try:
                filepath, orig_filename = _download_from_url(file_url, app.config['UPLOAD_FOLDER'])
                _, ext = os.path.splitext(orig_filename)
                ext = ext.lower()
                print(f"  [API] Downloaded from URL: {orig_filename} ({ext})")
            except ValueError as e:
                return jsonify({
                    'error': str(e),
                    'code': 'URL_DOWNLOAD_FAILED',
                }), 400
        else:
            # Standard file upload
            file = request.files['file']
            _, ext = os.path.splitext(file.filename)
            ext = ext.lower()

            if ext not in ['.pdf', '.docx', '.doc', '.txt', '.csv']:
                return jsonify({
                    'error': f'Unsupported file format: {ext}. Supported: PDF, DOCX, TXT, CSV',
                    'code': 'UNSUPPORTED_FORMAT',
                }), 400

            # Save temporarily
            unique_id = str(uuid.uuid4())[:8]
            safe_filename = f"api_{unique_id}_{file.filename}"
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], safe_filename)
            file.save(filepath)

        # Validate extension (for URL-downloaded files too)
        if ext not in ['.pdf', '.docx', '.doc', '.txt', '.csv']:
            return jsonify({
                'error': f'Unsupported file format: {ext}. Supported: PDF, DOCX, TXT, CSV',
                'code': 'UNSUPPORTED_FORMAT',
            }), 400

        # 3. Extract text
        password = request.form.get('password', '').strip() or None

        try:
            raw_text = extract_text(filepath, ext, password=password)
        except ValueError as e:
            error_msg = str(e)
            if 'password' in error_msg.lower():
                return jsonify({
                    'error': error_msg,
                    'code': 'PASSWORD_REQUIRED',
                }), 400
            return jsonify({'error': error_msg, 'code': 'EXTRACTION_FAILED'}), 400

        if not raw_text or len(raw_text.strip()) < 50:
            return jsonify({
                'error': 'Could not extract meaningful text from the file.',
                'code': 'EXTRACTION_FAILED',
            }), 400

        # 4. Detect bank
        bank_code = request.form.get('bank', 'auto')
        used_llm = False

        if bank_code == 'auto':
            bank_code = detect_bank(raw_text)

        # 5. Parse (structured first, then LLM fallback)
        result = None

        if bank_code:
            parser = get_parser(bank_code)
            if parser:
                result = parser.parse(raw_text)
                if result and result.get('transactions'):
                    result = post_process(result)
                else:
                    result = None

        if not result and llm_parser.is_available():
            result = llm_parser.parse_with_llm(raw_text)
            if result and result.get('transactions'):
                used_llm = True
            else:
                result = None

        if not result:
            return jsonify({
                'error': 'Could not parse this bank statement. The format may not be supported yet.',
                'code': 'PARSE_FAILED',
            }), 400

        # 6. Build full transaction list
        transactions = result['transactions']
        output_format = request.form.get('format', 'json').lower()

        # Balance verification
        _parse_balance_value = _pbv  # use centralized version
        prev_bal = None
        v_match = 0
        v_total = 0
        preview_rows = []

        for idx, t in enumerate(transactions):
            bal = _parse_balance_value(t['balance'])
            if idx == 0:
                chk = '○ Opening'
            elif prev_bal is not None and bal is not None:
                try:
                    w = float(str(t['withdrawal']).replace(',', '')) if t['withdrawal'] else 0
                    d = float(str(t['deposit']).replace(',', '')) if t['deposit'] else 0
                except (ValueError, TypeError):
                    w, d = 0, 0
                exp = prev_bal + d - w
                diff = bal - exp
                v_total += 1
                if abs(diff) < 0.02:
                    chk = '✓ Match'
                    v_match += 1
                else:
                    chk = f'✗ Diff: {diff:+,.2f}'
            else:
                chk = '? N/A'
            prev_bal = bal

            # Build preview for first 10
            if idx < 10:
                preview_rows.append({
                    'date': t['date'],
                    'particulars': t['particulars'],
                    'chq_ref': t['chq_ref'],
                    'withdrawal': t['withdrawal'],
                    'deposit': t['deposit'],
                    'balance': t['balance'],
                    'check': chk,
                })

        v_pct = (v_match / v_total * 100) if v_total > 0 else 0

        # 7. Build response
        response_data = {
            'success': True,
            'bank_name': result['bank_name'],
            'account_info': result.get('account_info', {}),
            'total_transactions': len(transactions),
            'period': result.get('period', 'N/A'),
            'parsed_by': 'ai' if used_llm else 'structured',
            'verification': {
                'matched': v_match,
                'total': v_total,
                'percent': round(v_pct, 1),
            },
            'transactions': [
                {
                    'date': t['date'],
                    'particulars': t['particulars'],
                    'chq_ref': t['chq_ref'],
                    'withdrawal': t['withdrawal'],
                    'deposit': t['deposit'],
                    'balance': t['balance'],
                }
                for t in transactions
            ],
            'preview': preview_rows,
        }

        # Optional: generate Excel/CSV and include download link
        if output_format == 'excel':
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            safe_bank_name = re.sub(r'[^\w]+', '_', result['bank_name']).strip('_')

            excel_filename = f"API_Statement_{safe_bank_name}_{timestamp}.xlsx"
            excel_path = os.path.join(app.config['OUTPUT_FOLDER'], excel_filename)
            export_to_excel(result, excel_path)

            csv_filename = f"API_Statement_{safe_bank_name}_{timestamp}.csv"
            csv_path = os.path.join(app.config['OUTPUT_FOLDER'], csv_filename)
            export_to_csv(result, csv_path)

            response_data['download_url'] = url_for('api_download', filename=excel_filename)
            response_data['excel_filename'] = excel_filename
            response_data['csv_download_url'] = url_for('api_download', filename=csv_filename)
            response_data['csv_filename'] = csv_filename

        # Log API usage
        label = getattr(request, '_api_key_label', 'unknown')
        print(f"  [API] Parsed {len(transactions)} txns for '{label}' "
              f"(bank={result['bank_name']}, parser={'ai' if used_llm else 'structured'})")

        return jsonify(response_data)

    except Exception as e:
        traceback.print_exc()
        return jsonify({'error': f'Server error: {str(e)}', 'code': 'SERVER_ERROR'}), 500

    finally:
        try:
            if filepath and os.path.exists(filepath):
                os.remove(filepath)
        except:
            pass


@app.route('/api/v1/download/<filename>')
@require_api_key
def api_download(filename):
    """Download a generated file (auth required). Reuses the main download logic."""
    filepath = os.path.join(app.config['OUTPUT_FOLDER'], filename)
    if not os.path.exists(filepath):
        return jsonify({'error': 'File not found. It may have expired (files are deleted after 1 hour).', 'code': 'NOT_FOUND'}), 404

    if filename.endswith('.xlsx'):
        mimetype = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    elif filename.endswith('.csv'):
        mimetype = 'text/csv'
    elif filename.endswith('.zip'):
        mimetype = 'application/zip'
    else:
        mimetype = 'application/octet-stream'

    from flask import make_response
    from urllib.parse import quote

    with open(filepath, 'rb') as f:
        data = f.read()

    response = make_response(data)
    response.headers['Content-Type'] = mimetype
    response.headers['Content-Length'] = len(data)
    safe_filename = quote(filename)
    response.headers['Content-Disposition'] = (
        f'attachment; filename="{filename}"; filename*=UTF-8\'\'\'{safe_filename}'
    )
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'

    return response


if __name__ == '__main__':
    print("\n" + "=" * 60)
    print("  Bank Statement Parser v2.1")
    print("  Open http://localhost:5000 in your browser")
    print("=" * 60)
    
    # Check OCR status
    ocr = check_ocr_available()
    if ocr['available']:
        print("  [OK] OCR: Available (Tesseract)")
    else:
        print(f"  [!!] OCR: {ocr['message']}")
    
    # Check LLM status
    llm_status = llm_parser.get_status()
    if llm_status['available']:
        print(f"  [OK] AI Fallback: Ready ({llm_status['remaining']}/{llm_status['daily_limit']} calls remaining)")
        print(f"       Cached: {llm_status['cached_results']} | Learned: {llm_status['learned_profiles']} banks")
    else:
        print("  [--] AI Fallback: Not configured (set GEMINI_API_KEY)")
    
    print("=" * 60 + "\n")
    app.run(debug=True, port=5000)
