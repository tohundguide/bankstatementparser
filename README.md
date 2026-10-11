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

## API v1 contract and `allow_ai`

`POST /api/v1/parse` answers the shape in `contract/v1/response.schema.json`
(sample: `contract/v1/sample-v1-scan.json`). Beside the fields it always had, it
says `parser_version` (also the `X-Parser-Version` header), `account_number`,
`ifsc`, `period_from`/`period_to` (the period the bank printed, ISO),
`pages {seen, stated}`, `read_by` (`pdf_text`, `ocr`, `text`, `docx`, `ai`), a
`seq` on every line, and marks every figure it made up itself:
`balance_source: computed` on a balance the statement did not print,
`amount_source: inferred` on an amount taken from the balance movement, with
the counts in `filled`. Send `allow_ai=false` and a layout no parser knows is
refused (`PARSE_FAILED`) instead of being read by AI; nothing sent through the
API is kept, cached or learned from either way. Deploys: `DEPLOY_RAILWAY.md`.
