"""
Text Extractor Module
=====================
Extracts raw text from various file formats: PDF, DOCX, TXT, CSV.
Supports:
  - Regular PDFs (text-based) via pdfplumber / PyPDF2
  - Scanned/image PDFs via OCR (Tesseract – free, open-source)
  - Password-protected PDFs
  - DOCX files
  - Plain text / CSV files
"""

import os
import re
import logging

# Limit Tesseract to 1 OpenMP thread per job.
# Must be set BEFORE importing pytesseract or calling tesseract.
# Without this, Tesseract uses ALL CPU cores — 2 concurrent jobs = 100% CPU saturation.
os.environ['OMP_THREAD_LIMIT'] = '1'
os.environ['OMP_NUM_THREADS'] = '1'

logger = logging.getLogger(__name__)


def extract_text(filepath: str, ext: str, password: str = None) -> str:
    """
    Extract raw text from a file based on its extension.
    
    Args:
        filepath: Absolute path to the file
        ext: File extension (lowercase, with dot, e.g. '.pdf')
        password: Optional password for encrypted PDFs
    
    Returns:
        Raw text content of the file
    """
    ext = ext.lower()
    
    if ext in ['.txt', '.csv']:
        return _extract_from_text(filepath)
    elif ext in ['.docx', '.doc']:
        return _extract_from_docx(filepath)
    elif ext == '.pdf':
        return _extract_from_pdf(filepath, password=password)
    else:
        raise ValueError(f"Unsupported file format: {ext}")


def _extract_from_text(filepath: str) -> str:
    """Extract text from plain text or CSV files."""
    encodings = ['utf-8', 'utf-8-sig', 'latin-1', 'cp1252', 'ascii']
    
    for encoding in encodings:
        try:
            with open(filepath, 'r', encoding=encoding) as f:
                return f.read()
        except (UnicodeDecodeError, UnicodeError):
            continue
    
    # Fallback: read as binary and decode with errors='replace'
    with open(filepath, 'rb') as f:
        return f.read().decode('utf-8', errors='replace')


def _extract_from_docx(filepath: str) -> str:
    """Extract text from DOCX files, preserving line structure."""
    try:
        from docx import Document
    except ImportError:
        raise ImportError("python-docx is required for DOCX files. Install: pip install python-docx")
    
    doc = Document(filepath)
    lines = []
    for para in doc.paragraphs:
        lines.append(para.text)
    
    # Also extract text from tables (bank statements often use tables in DOCX)
    for table in doc.tables:
        for row in table.rows:
            row_text = []
            for cell in row.cells:
                cell_text = cell.text.strip()
                if cell_text:
                    row_text.append(cell_text)
            if row_text:
                lines.append('  '.join(row_text))
    
    return '\n'.join(lines)


def _extract_from_pdf(filepath: str, password: str = None) -> str:
    """
    Extract text from PDF files.
    Pipeline:
      1. Try pdfplumber (best for structured/table PDFs)
      2. Try PyPDF2 as fallback
      3. If both fail (scanned PDF), try OCR with Tesseract
    
    Handles password-protected PDFs automatically.
    """
    text = ""
    
    # --- Step 1: Try pdfplumber ---
    try:
        import pdfplumber
        open_kwargs = {}
        if password:
            open_kwargs['password'] = password
        
        with pdfplumber.open(filepath, **open_kwargs) as pdf:
            pages = []
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    pages.append(page_text)
                # Release this page's parsed objects/layout cache before moving on.
                # Without this, pdfplumber retains every page's objects in RAM, so a
                # large multi-hundred-page PDF grows memory linearly and can OOM.
                # With it, peak memory stays roughly flat regardless of page count.
                try:
                    page.flush_cache()
                except Exception:
                    pass
            text = '\n'.join(pages)
            if text.strip():
                logger.info(f"Extracted {len(text)} chars via pdfplumber")
                return text
    except Exception as e:
        logger.debug(f"pdfplumber failed: {e}")
    
    # --- Step 2: Try PyPDF2 ---
    try:
        from PyPDF2 import PdfReader
        reader = PdfReader(filepath)
        
        # Handle encrypted PDFs
        if reader.is_encrypted:
            if password:
                reader.decrypt(password)
            else:
                # Try common bank statement passwords
                common_passwords = _get_common_passwords(filepath)
                decrypted = False
                for pwd in common_passwords:
                    try:
                        if reader.decrypt(pwd):
                            decrypted = True
                            logger.info(f"PDF decrypted with common password pattern")
                            break
                    except Exception:
                        continue
                if not decrypted:
                    raise ValueError(
                        "This PDF is password-protected. Please enter the password. "
                        "Common passwords for bank statements: your account number, DOB (DDMMYYYY), or PAN number."
                    )
        
        pages = []
        for page in reader.pages:
            page_text = page.extract_text()
            if page_text:
                pages.append(page_text)
        text = '\n'.join(pages)
        if text.strip():
            logger.info(f"Extracted {len(text)} chars via PyPDF2")
            return text
    except ValueError:
        raise  # Re-raise password errors
    except Exception as e:
        logger.debug(f"PyPDF2 failed: {e}")
    
    # --- Step 3: OCR fallback for scanned PDFs ---
    if not text.strip():
        logger.info("No text extracted — attempting OCR...")
        try:
            text = _extract_via_ocr(filepath)
            if text.strip():
                logger.info(f"Extracted {len(text)} chars via OCR")
                return text
        except ImportError as e:
            raise ValueError(
                f"This appears to be a scanned/image-based PDF. "
                f"OCR is required but dependencies are missing: {str(e)}. "
                f"Install: pip install pytesseract pdf2image Pillow "
                f"and install Tesseract from https://github.com/UB-Mannheim/tesseract/wiki"
            )
        except Exception as e:
            raise ValueError(
                f"Could not extract text from PDF (scanned/image-based). "
                f"OCR failed: {str(e)}. "
                f"Ensure Tesseract is installed and in your PATH."
            )
    
    raise ValueError("Could not extract text from PDF. The file may be empty or corrupted.")


def _get_common_passwords(filepath: str) -> list:
    """
    Generate common bank statement passwords to try.
    Banks usually use: account number, DOB, PAN, etc.
    Returns a list of passwords to attempt.
    """
    passwords = ['', ' ']
    
    # Extract potential account numbers from filename
    filename = os.path.basename(filepath)
    # Find sequences of digits in filename
    digit_sequences = re.findall(r'\d{4,}', filename)
    passwords.extend(digit_sequences)
    
    return passwords


def _extract_via_ocr(filepath: str) -> str:
    """
    Extract text from a scanned/image-based PDF using Tesseract OCR.
    
    Requirements (all free):
      - pytesseract: pip install pytesseract
      - pdf2image: pip install pdf2image  
      - Pillow: pip install Pillow
      - Tesseract: https://github.com/UB-Mannheim/tesseract/wiki (free installer)
      - Poppler (for pdf2image): bundled with pdf2image on Windows
    """
    try:
        import pytesseract
    except ImportError:
        raise ImportError(
            "pytesseract is required for OCR. Install: pip install pytesseract"
        )
    
    try:
        from pdf2image import convert_from_path
    except ImportError:
        raise ImportError(
            "pdf2image is required for OCR. Install: pip install pdf2image"
        )
    
    # Check if Tesseract is installed
    tesseract_paths = [
        r'C:\Program Files\Tesseract-OCR\tesseract.exe',
        r'C:\Program Files (x86)\Tesseract-OCR\tesseract.exe',
        r'C:\Users\User\AppData\Local\Tesseract-OCR\tesseract.exe',
    ]
    
    for tpath in tesseract_paths:
        if os.path.exists(tpath):
            pytesseract.pytesseract.tesseract_cmd = tpath
            break
    
    # Find poppler binaries (needed by pdf2image on Windows)
    poppler_path = None
    poppler_search_paths = [
        r'C:\poppler\poppler-24.08.0\Library\bin',
        r'C:\Program Files\poppler\Library\bin',
        r'C:\Program Files\poppler-24.08.0\Library\bin',
        r'C:\poppler\Library\bin',
    ]
    for pp in poppler_search_paths:
        if os.path.exists(pp):
            poppler_path = pp
            break
    
    # Convert PDF pages to images
    # DPI 150 is sufficient for bank statement text (was 300 — caused excessive CPU/RAM)
    try:
        images = convert_from_path(filepath, dpi=150, poppler_path=poppler_path, grayscale=True)
    except Exception as e:
        # Try with even lower DPI as fallback
        try:
            images = convert_from_path(filepath, dpi=100, poppler_path=poppler_path, grayscale=True)
        except Exception:
            raise RuntimeError(
                f"Could not convert PDF to images: {str(e)}. "
                f"Ensure poppler is installed. On Windows, download poppler from "
                f"https://github.com/oschwartz10612/poppler-windows/releases "
                f"and extract to C:\\poppler"
            )
    
    # OCR each page
    all_text = []
    for i, img in enumerate(images):
        try:
            # OMP_THREAD_LIMIT=1 is set at module level to cap CPU per job.
            custom_config = r'--oem 3 --psm 6'
            page_text = pytesseract.image_to_string(img, config=custom_config)
            if page_text.strip():
                all_text.append(page_text)
            logger.info(f"  OCR page {i+1}: {len(page_text)} chars")
        except Exception as e:
            logger.warning(f"  OCR page {i+1} failed: {e}")
    
    return '\n'.join(all_text)


def check_ocr_available() -> dict:
    """
    Check if OCR dependencies are available.
    Returns a dict with status info.
    """
    import shutil

    result = {
        'available': False,
        'pytesseract': False,
        'pdf2image': False,
        'tesseract_binary': False,
        'poppler_binary': False,
        'message': ''
    }

    try:
        import pytesseract
        result['pytesseract'] = True
    except ImportError:
        pass

    try:
        from pdf2image import convert_from_path
        result['pdf2image'] = True
    except ImportError:
        pass

    # Check Tesseract binary
    tesseract_paths = [
        r'C:\Program Files\Tesseract-OCR\tesseract.exe',
        r'C:\Program Files (x86)\Tesseract-OCR\tesseract.exe',
    ]

    for tpath in tesseract_paths:
        if os.path.exists(tpath):
            result['tesseract_binary'] = True
            break

    # Also check if it's in PATH
    if not result['tesseract_binary']:
        if shutil.which('tesseract'):
            result['tesseract_binary'] = True

    # Check Poppler (pdftoppm/pdfinfo) — pdf2image shells out to it. Without this,
    # OCR fails at PDF->image conversion even though the Python imports succeed.
    # This is the common silent failure on a fresh Linux VPS (apt: poppler-utils).
    if shutil.which('pdftoppm') or shutil.which('pdfinfo'):
        result['poppler_binary'] = True
    else:
        poppler_search_paths = [
            r'C:\poppler\poppler-24.08.0\Library\bin',
            r'C:\Program Files\poppler\Library\bin',
            r'C:\Program Files\poppler-24.08.0\Library\bin',
            r'C:\poppler\Library\bin',
        ]
        for pp in poppler_search_paths:
            if os.path.exists(os.path.join(pp, 'pdftoppm.exe')):
                result['poppler_binary'] = True
                break

    result['available'] = all([
        result['pytesseract'], result['pdf2image'],
        result['tesseract_binary'], result['poppler_binary'],
    ])

    if result['available']:
        result['message'] = 'OCR is fully available (Tesseract + Poppler + pdf2image)'
    else:
        missing = []
        if not result['pytesseract']:
            missing.append('pip install pytesseract')
        if not result['pdf2image']:
            missing.append('pip install pdf2image Pillow')
        if not result['tesseract_binary']:
            missing.append('Install Tesseract (Linux: apt install tesseract-ocr)')
        if not result['poppler_binary']:
            missing.append('Install Poppler (Linux: apt install poppler-utils)')
        result['message'] = 'Missing: ' + ', '.join(missing)

    return result
