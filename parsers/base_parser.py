"""
Base Parser
===========
Abstract base class that all bank parsers must implement.
This ensures a consistent interface across all bank-specific parsers.
"""

from abc import ABC, abstractmethod
from typing import Dict, List, Optional


class BaseBankParser(ABC):
    """
    Abstract base class for bank statement parsers.
    
    Every bank parser must:
    1. Define a unique BANK_CODE (e.g., 'jk_bank')
    2. Define a BANK_NAME (e.g., 'Jammu & Kashmir Bank')
    3. Define DETECTION_KEYWORDS - strings that identify this bank's statements
    4. Implement the parse() method
    """
    
    BANK_CODE: str = ""
    BANK_NAME: str = ""
    DETECTION_KEYWORDS: List[str] = []
    
    @abstractmethod
    def parse(self, raw_text: str) -> Dict:
        """
        Parse raw text into structured transaction data.
        
        Args:
            raw_text: The complete raw text extracted from the statement file.
        
        Returns:
            A dictionary with the following structure:
            {
                'bank_name': str,           # Human-readable bank name
                'bank_code': str,           # Internal bank code
                'account_info': {           # Account metadata
                    'account_number': str,
                    'account_holder': str,
                    'branch': str,
                    'ifsc': str,
                    'type': str,
                },
                'period': str,              # Statement period
                'transactions': [           # List of parsed transactions
                    {
                        'date': str,            # DD-MM-YYYY
                        'particulars': str,     # Full description (joined from multi-line)
                        'chq_ref': str,         # Cheque/Reference number
                        'withdrawal': str,      # Amount or empty
                        'deposit': str,         # Amount or empty
                        'balance': str,         # Balance with Dr/Cr suffix
                    },
                    ...
                ]
            }
        """
        pass
    
    @classmethod
    def can_parse(cls, raw_text: str) -> bool:
        """
        Check if this parser can handle the given text.
        Returns True if the text matches this bank's format.
        """
        text_upper = raw_text.upper()
        matches = sum(1 for kw in cls.DETECTION_KEYWORDS if kw.upper() in text_upper)
        # Require at least 2 keyword matches for reliable detection
        return matches >= 2
