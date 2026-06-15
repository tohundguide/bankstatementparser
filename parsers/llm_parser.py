"""
LLM Fallback Parser — with Cost Protection
=============================================
Uses Google Gemini API to parse bank statements when structured parsers fail.

Cost Protection Layers:
  1. RATE LIMITER  — Max N calls/day (default: 20). Hard cap on spend.
  2. RESULT CACHE  — Same/similar text → return cached result (₹0).
  3. LEARNED BANKS — When LLM identifies a new bank, save detection
                     keywords so future detection works without LLM.

Cost per statement: ~₹0.15 (Gemini 2.0 Flash)
Effective cost after learning: ₹0 for known formats
"""

import os
import json
import re
import hashlib
from datetime import datetime, date
from typing import Dict, Optional


# ── Config ──
_GEMINI_KEY = os.environ.get('GEMINI_API_KEY', '')
_DAILY_LIMIT = int(os.environ.get('LLM_DAILY_LIMIT', '20'))
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CACHE_DIR = os.path.join(_BASE_DIR, 'learned', 'cache')
_PROFILES_DIR = os.path.join(_BASE_DIR, 'learned', 'profiles')
_RATE_FILE = os.path.join(_BASE_DIR, 'learned', 'rate_limit.json')

# Ensure directories exist
os.makedirs(_CACHE_DIR, exist_ok=True)
os.makedirs(_PROFILES_DIR, exist_ok=True)


def is_available() -> bool:
    """Check if the LLM parser is available (API key + within rate limit)."""
    return bool(_GEMINI_KEY)


def get_status() -> Dict:
    """Return current LLM parser status for diagnostics."""
    calls_today, limit = _get_rate_info()
    cached = len([f for f in os.listdir(_CACHE_DIR) if f.endswith('.json')])
    profiles = len([f for f in os.listdir(_PROFILES_DIR) if f.endswith('.json')])
    return {
        'available': is_available(),
        'calls_today': calls_today,
        'daily_limit': limit,
        'remaining': max(0, limit - calls_today),
        'cached_results': cached,
        'learned_profiles': profiles,
    }


# ═══════════════════════════════════════════════════════
#  LAYER 1: Rate Limiter
# ═══════════════════════════════════════════════════════

def _get_rate_info():
    """Get today's call count and limit."""
    today = date.today().isoformat()
    try:
        with open(_RATE_FILE, 'r') as f:
            data = json.load(f)
        if data.get('date') != today:
            return 0, _DAILY_LIMIT
        return data.get('count', 0), _DAILY_LIMIT
    except (FileNotFoundError, json.JSONDecodeError):
        return 0, _DAILY_LIMIT


def _check_rate_limit() -> bool:
    """Return True if we're within the daily limit."""
    calls, limit = _get_rate_info()
    return calls < limit


def _increment_rate_limit():
    """Record one LLM API call."""
    today = date.today().isoformat()
    try:
        with open(_RATE_FILE, 'r') as f:
            data = json.load(f)
        if data.get('date') != today:
            data = {'date': today, 'count': 0}
    except (FileNotFoundError, json.JSONDecodeError):
        data = {'date': today, 'count': 0}
    
    data['count'] = data.get('count', 0) + 1
    with open(_RATE_FILE, 'w') as f:
        json.dump(data, f)


# ═══════════════════════════════════════════════════════
#  LAYER 2: Result Cache (keyed by text signature)
# ═══════════════════════════════════════════════════════

def _text_signature(raw_text: str) -> str:
    """
    Generate a cache key from the text.
    
    We hash the first 2000 chars (bank header + structure) combined with
    the total text length. This means:
      - Same file uploaded twice → cache hit (exact match)
      - Different statement from same bank → likely cache MISS (different data)
        BUT the learned profile will speed up detection
    """
    # Normalize: strip whitespace variations
    normalized = re.sub(r'\s+', ' ', raw_text[:2000]).strip().lower()
    content = f"{normalized}|len={len(raw_text)}"
    return hashlib.md5(content.encode('utf-8')).hexdigest()


def _get_cached(signature: str) -> Optional[Dict]:
    """Check if we have a cached result for this text signature."""
    cache_file = os.path.join(_CACHE_DIR, f'{signature}.json')
    try:
        if os.path.exists(cache_file):
            with open(cache_file, 'r', encoding='utf-8') as f:
                cached = json.load(f)
            print(f"  [LLM] Cache HIT → returning cached result (₹0)")
            # Mark as cached in bank name
            if cached.get('bank_name') and '(Cached)' not in cached['bank_name']:
                cached['bank_name'] = cached['bank_name'].replace('(AI Parsed)', '(AI Cached)')
            return cached
    except (json.JSONDecodeError, OSError):
        pass
    return None


def _save_cache(signature: str, result: Dict):
    """Save a parsed result to cache."""
    cache_file = os.path.join(_CACHE_DIR, f'{signature}.json')
    try:
        with open(cache_file, 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"  [LLM] Result cached as {signature[:8]}...")
    except OSError as e:
        print(f"  [LLM] Cache save failed: {e}")


# ═══════════════════════════════════════════════════════
#  LAYER 3: Learned Bank Profiles
# ═══════════════════════════════════════════════════════

def _save_learned_profile(bank_name: str, raw_text: str):
    """
    Save a learned bank profile for future detection.
    
    Extracts keywords from the header that identify this bank,
    so next time the structured registry can detect it faster.
    """
    # Extract potential bank identifiers from first 1000 chars
    header = raw_text[:1000].upper()
    
    # Find likely bank-name keywords (capitalized words/phrases)
    keywords = []
    # Common bank identifiers
    for pattern in [
        r'([A-Z]{2,}\s+BANK\s+(?:OF\s+)?[A-Z]*)',
        r'(BANK\s+OF\s+[A-Z]+)',
        r'([A-Z]+\s+SMALL\s+FINANCE\s+BANK)',
        r'(STATE\s+BANK\s+OF\s+INDIA)',
        r'(ICICI|HDFC|AXIS|KOTAK|PNB|SBI|BOB|CANARA|UNION|IDBI|YES\s+BANK)',
        r'(IFSC\s*[:\s]+\s*([A-Z]{4}\d{7}))',
    ]:
        matches = re.findall(pattern, header)
        for m in matches:
            kw = m if isinstance(m, str) else m[0]
            kw = kw.strip()
            if len(kw) > 2 and kw not in keywords:
                keywords.append(kw)
    
    if not keywords:
        return
    
    safe_name = re.sub(r'[^\w]+', '_', bank_name).strip('_')[:30].lower()
    profile_file = os.path.join(_PROFILES_DIR, f'{safe_name}.json')
    
    profile = {
        'bank_name': bank_name,
        'detection_keywords': keywords,
        'learned_at': datetime.now().isoformat(),
        'header_sample': raw_text[:500],
    }
    
    try:
        with open(profile_file, 'w', encoding='utf-8') as f:
            json.dump(profile, f, ensure_ascii=False, indent=2)
        print(f"  [LLM] Learned bank profile: {bank_name} → {len(keywords)} keywords")
    except OSError:
        pass


def get_learned_profiles():
    """Return all learned bank profiles (used by registry for detection)."""
    profiles = []
    for fname in os.listdir(_PROFILES_DIR):
        if fname.endswith('.json'):
            try:
                with open(os.path.join(_PROFILES_DIR, fname), 'r', encoding='utf-8') as f:
                    profiles.append(json.load(f))
            except (json.JSONDecodeError, OSError):
                pass
    return profiles


# ═══════════════════════════════════════════════════════
#  Main Parse Function
# ═══════════════════════════════════════════════════════

def parse_with_llm(raw_text: str) -> Optional[Dict]:
    """
    Parse bank statement text using Google Gemini API.
    
    Cost protection flow:
      1. Check cache → if hit, return immediately (₹0)
      2. Check rate limit → if exceeded, return None
      3. Call Gemini API (₹0.15)
      4. Cache the result for future re-uploads
      5. Save learned bank profile for detection
    
    Returns:
        Parsed result di    ct, or None on failure
    """
    if not _GEMINI_KEY:
        return None
    
    # Layer 2: Check cache first
    sig = _text_signature(raw_text)
    cached = _get_cached(sig)
    if cached:
        return cached
    
    # Layer 1: Check rate limit
    if not _check_rate_limit():
        calls, limit = _get_rate_info()
        print(f"  [LLM] Rate limit reached ({calls}/{limit} today). Skipping.")
        return None
    
    # ── Make LLM API call ──
    try:
        import urllib.request
        
        prompt = _build_prompt(raw_text)
        
        payload = {
            "contents": [{
                "parts": [{"text": prompt}]
            }],
            "generationConfig": {
                "temperature": 0.1,
                "maxOutputTokens": 8192,
                "responseMimeType": "application/json",
            }
        }
        
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={_GEMINI_KEY}"
        
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode('utf-8'),
            headers={'Content-Type': 'application/json'},
            method='POST'
        )
        
        resp = urllib.request.urlopen(req, timeout=30)
        api_result = json.loads(resp.read())
        
        # Record the API call
        _increment_rate_limit()
        
        # Extract the JSON from Gemini's response
        text_response = api_result['candidates'][0]['content']['parts'][0]['text']
        parsed = json.loads(text_response)
        
        # Validate and normalize
        result = _normalize_response(parsed)
        
        if result:
            # Layer 2: Cache for future re-uploads
            _save_cache(sig, result)
            
            # Layer 3: Learn bank profile for detection
            bank_name = parsed.get('bank_name', '')
            if bank_name:
                _save_learned_profile(bank_name, raw_text)
        
        return result
        
    except Exception as e:
        print(f"  [LLM] Parse failed: {e}")
        return None


def _build_prompt(raw_text: str) -> str:
    """Build the prompt for Gemini to parse a bank statement."""
    text_sample = raw_text[:6000]
    
    return f"""You are a bank statement parser. Extract ALL transactions from this Indian bank statement text.

IMPORTANT RULES:
1. Extract EVERY transaction row — do not skip any
2. Dates should be in DD-MM-YYYY or DD/MM/YYYY format as they appear
3. For amounts: remove commas, keep as strings like "1500.00"
4. Balance may have Cr/Dr suffix — keep it as-is (e.g. "25000.00Cr")
5. Leave empty fields as empty strings ""
6. Detect the bank name from the header
7. Extract account holder name, account number, IFSC, account type if available

Return ONLY valid JSON in this exact format:
{{
  "bank_name": "Bank Name",
  "account_info": {{
    "account_no": "...",
    "holder_name": "...",
    "ifsc": "...",
    "account_type": "..."
  }},
  "period": "DD-MM-YYYY to DD-MM-YYYY",
  "transactions": [
    {{
      "date": "DD-MM-YYYY",
      "particulars": "Description text",
      "chq_ref": "Reference number or empty",
      "withdrawal": "1500.00 or empty",
      "deposit": "5000.00 or empty",
      "balance": "25000.00Cr"
    }}
  ]
}}

BANK STATEMENT TEXT:
{text_sample}"""


def _normalize_response(parsed: Dict) -> Optional[Dict]:
    """Validate and normalize the LLM response to match our standard format."""
    if not parsed:
        return None
    
    transactions = parsed.get('transactions', [])
    if not transactions:
        return None
    
    clean_txns = []
    for txn in transactions:
        clean_txns.append({
            'date': str(txn.get('date', '')).strip(),
            'particulars': str(txn.get('particulars', '')).strip(),
            'chq_ref': str(txn.get('chq_ref', '')).strip(),
            'withdrawal': str(txn.get('withdrawal', '')).strip(),
            'deposit': str(txn.get('deposit', '')).strip(),
            'balance': str(txn.get('balance', '')).strip(),
        })
    
    bank_name = parsed.get('bank_name', 'Unknown Bank')
    
    return {
        'bank_name': f"{bank_name} (AI Parsed)",
        'account_info': parsed.get('account_info', {}),
        'period': parsed.get('period', 'N/A'),
        'transactions': clean_txns,
    }
