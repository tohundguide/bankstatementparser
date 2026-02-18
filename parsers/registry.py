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


def detect_bank(raw_text: str) -> Optional[str]:
    """
    Auto-detect which bank the statement belongs to.
    
    Args:
        raw_text: Raw text extracted from the statement file
    
    Returns:
        Bank code string, or None if no parser matches
    """
    if not _PARSER_REGISTRY:
        _discover_parsers()
    
    best_match = None
    best_score = 0
    
    for code, parser_class in _PARSER_REGISTRY.items():
        if parser_class.can_parse(raw_text):
            # Count keyword matches for scoring
            text_upper = raw_text.upper()
            score = sum(1 for kw in parser_class.DETECTION_KEYWORDS if kw.upper() in text_upper)
            if score > best_score:
                best_score = score
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
