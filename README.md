# Bank Statement Parser

A free, open-source tool that converts Indian bank statements (PDF, DOCX, TXT) into clean, formatted Excel files with auto-balance-verification, monthly summaries, and VLOOKUP-ready narration mapping.

## Features

- **Auto-detect bank format** — just upload and go
- **10+ banks supported** — JK Bank, ICICI, HDFC, SBI, Equitas, PNB, and more
- **AI Fallback** — unsupported bank? AI parses it automatically (optional, uses Gemini)
- **OCR support** — handles scanned/image-based PDFs via Tesseract
- **Password-protected PDFs** — enter the password and parse
- **Excel + CSV export** — formatted workbook with summary, charts, and balance verification
- **Narration mapping** — VLOOKUP-powered sheet for transaction categorization
- **100% free** — no API costs for structured parsing, optional AI at ~₹0.15/statement

## Quick Start (Local)

```bash
# Clone and install
git clone https://github.com/your-username/BankStatementParser.git
cd BankStatementParser
pip install -r requirements.txt

# Run
python app.py
# Open http://localhost:5000
```

## Deploy to Render.com (Free)

1. Push to GitHub
2. Go to [render.com](https://render.com) → New → Web Service
3. Connect your GitHub repo
4. Settings:
   - **Build Command**: (auto-detected from Dockerfile)
   - **Environment**: Docker
   - **Plan**: Free
5. Add environment variable: `GEMINI_API_KEY` (optional, for AI fallback)
6. Deploy!

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `GEMINI_API_KEY` | No | — | Enables AI fallback parser for unsupported banks |
| `LLM_DAILY_LIMIT` | No | `20` | Max AI calls per day (cost cap) |

## Architecture

```
app.py              → Flask web server + API routes
parsers/            → Bank-specific parsers (plugin system)
  base_parser.py    → Base class for all parsers
  registry.py       → Auto-discovers and registers parsers
  llm_parser.py     → AI fallback with cost protection
  *_parser.py       → Bank-specific parsers
extractors/         → File format handlers (PDF, DOCX, TXT, OCR)
exporters/          → Excel/CSV export with formatting
templates/          → Frontend HTML
learned/            → AI cache and learned bank profiles
feedback/           → User-submitted files for improvement
```

## Adding a New Bank Parser

1. Create `parsers/yourbank_parser.py`
2. Extend `BaseBankParser`
3. Set `BANK_CODE`, `BANK_NAME`, `DETECTION_KEYWORDS`
4. Implement `parse()` method
5. Done — auto-discovered on restart!

## License

MIT
