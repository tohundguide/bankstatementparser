"""
Parser Registry
================
Auto-discovers and registers all bank parsers.
New parsers are automatically detected when placed in the parsers/ directory.

To add a new bank:
1. Create a new file in parsers/ (e.g., parsers/sbi_parser.py)
2. Create a class that extends BaseBankParser
3. Set BANK_CODE, BANK_NAME, DETECTION_KEYWORDS
4. Implement the parse() method
5. That's it! The parser will be auto-discovered.
"""

import os
import re
import importlib
import inspect
from typing import Dict, List, Optional, Tuple
from parsers.base_parser import BaseBankParser


# Global registry of parser classes
_PARSER_REGISTRY: Dict[str, type] = {}


def _discover_parsers():
    """
    Scan the parsers/ directory and register all BaseBankParser subclasses.
    Called once at startup.
    """
    global _PARSER_REGISTRY
    
    parsers_dir = os.path.dirname(__file__)
    
    for filename in os.listdir(parsers_dir):
        if filename.endswith('_parser.py') and filename != 'base_parser.py':
            module_name = f"parsers.{filename[:-3]}"
            try:
                module = importlib.import_module(module_name)
                
                # Find all BaseBankParser subclasses in the module
                for name, obj in inspect.getmembers(module, inspect.isclass):
                    if (issubclass(obj, BaseBankParser) 
                        and obj is not BaseBankParser 
                        and obj.BANK_CODE):
                        _PARSER_REGISTRY[obj.BANK_CODE] = obj
                        print(f"  ✓ Registered parser: {obj.BANK_NAME} ({obj.BANK_CODE})")
            except Exception as e:
                print(f"  ✗ Failed to load parser {filename}: {e}")


# The statement's OWN routing code always appears right after an IFSC label in
# the header (e.g. "RTGS/NEFT IFSC: HDFC0004139" or "IFS Code : SBIN0001234").
# Counterparty IFSCs buried in NEFT/UPI narrations ("NEFT DR-SBIN0007407-...")
# carry no such label, so anchoring detection on the *labelled* IFSC stops a
# counterparty's code from hijacking detection to the wrong bank.
_IFSC_LABEL_RE = re.compile(
    r'(?:IFSC|IFS[\s.]*CODE)\b[^A-Za-z0-9]{0,4}([A-Z]{4}0[A-Z0-9]{6})',
    re.IGNORECASE,
)


def _own_ifsc(raw_text: str) -> Optional[str]:
    """Return the statement's own IFSC (the one following an IFSC label), or None."""
    m = _IFSC_LABEL_RE.search(raw_text or "")
    return m.group(1).upper() if m else None


def _anchor_matches_ifsc(parser_class, ifsc: str) -> bool:
    """True if one of the parser's IFSC-prefix anchor rules matches the given IFSC."""
    for token, weight, is_regex in parser_class.DETECTION_RULES:
        if is_regex and weight >= 10 and re.search(token, ifsc, re.IGNORECASE):
            return True
    return False


def detect_bank(raw_text: str) -> Optional[str]:
    """
    Auto-detect which bank the statement belongs to.

    Uses weighted scoring: ANCHOR (IFSC, domain) > STRONG (bank name) > WEAK (generic).
    Picks the parser with the highest (score, anchor_hits) tuple.

    Before scoring, the statement's own labelled IFSC is consulted: if exactly one
    parser claims that IFSC prefix as an anchor, it wins outright. This is the
    authoritative signal and prevents a counterparty IFSC inside a transaction
    narration from out-scoring the issuing bank.

    Args:
        raw_text: Raw text extracted from the statement file

    Returns:
        Bank code string, or None if no parser matches
    """
    if not _PARSER_REGISTRY:
        _discover_parsers()

    own = _own_ifsc(raw_text)
    if own:
        ifsc_owners = [code for code, pc in _PARSER_REGISTRY.items()
                       if _anchor_matches_ifsc(pc, own)]
        if len(ifsc_owners) == 1:
            return ifsc_owners[0]

    best_match = None
    best_key = (-1, -1)  # (score, anchor_hits)
    
    for code, parser_class in _PARSER_REGISTRY.items():
        if not parser_class.can_parse(raw_text):
            continue
        
        # Use weighted scoring if available, else fall back to keyword count
        if parser_class.DETECTION_RULES:
            score, anchors = parser_class.detection_score(raw_text)
            key = (score, anchors)
        else:
            text_upper = raw_text.upper()
            score = sum(1 for kw in parser_class.DETECTION_KEYWORDS if kw.upper() in text_upper)
            key = (score, 0)
        
        if key > best_key:
            best_key = key
            best_match = code
    
    return best_match


def get_parser(bank_code: str) -> Optional[BaseBankParser]:
    """
    Get an instance of the parser for the given bank code.
    
    Args:
        bank_code: The bank identifier (e.g., 'jk_bank')
    
    Returns:
        An instance of the appropriate parser, or None
    """
    if not _PARSER_REGISTRY:
        _discover_parsers()
    
    parser_class = _PARSER_REGISTRY.get(bank_code)
    if parser_class:
        return parser_class()
    return None


def get_supported_banks() -> List[Dict[str, str]]:
    """
    Get a list of all supported banks.
    
    Returns:
        List of dicts with 'code' and 'name' keys
    """
    if not _PARSER_REGISTRY:
        _discover_parsers()
    
    return [
        {'code': code, 'name': cls.BANK_NAME}
        for code, cls in sorted(_PARSER_REGISTRY.items(), key=lambda x: x[1].BANK_NAME)
    ]
