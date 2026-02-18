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
from parsers import llm_parser
from exporters.excel_exporter import export_to_excel, export_to_csv

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB max
app.config['UPLOAD_FOLDER'] = os.path.join(os.path.dirname(__file__), 'uploads')
app.config['OUTPUT_FOLDER'] = os.path.join(os.path.dirname(__file__), 'output')
app.config['FEEDBACK_FOLDER'] = os.path.join(os.path.dirname(__file__), 'feedback')

# Create necessary directories
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(app.config['OUTPUT_FOLDER'], exist_ok=True)
os.makedirs(app.config['FEEDBACK_FOLDER'], exist_ok=True)


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
                if not result or not result.get('transactions'):
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
        from exporters.excel_exporter import _parse_balance_value
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
            'download_url': f'/download/{excel_filename}',
            'excel_filename': excel_filename,
            'csv_download_url': f'/download/{csv_filename}',
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
            
            download_url = f'/download/{zip_filename}'
            download_name = zip_filename
        else:
            download_url = f'/download/{os.path.basename(excel_paths[0])}'
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
