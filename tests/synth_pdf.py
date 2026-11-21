"""
A text PDF made by hand, for the tests (bankbest-b, b-3).

No library: one Courier font (fixed width, so a right-aligned figure lands
exactly where a bank's column would), text drawn where it is told. pdfplumber
reads it the way it reads a bank's own PDF: words with their positions.

    pdf_bytes([[(x, y, 'text'), ...], ...])        one list of items per page
    statement_pdf(rows, layout=...)                 a whole statement, see below

Karr's daily watch builds the same kind of file in JavaScript
(scripts/bank-read-watch.mjs in the Karr repo); nothing here is a real account.
"""

FONT_SIZE = 9
CHAR_W = 0.6 * FONT_SIZE  # Courier: every glyph is 600/1000 of the size


def _esc(s):
    return s.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)')


def pdf_bytes(pages, width=842, height=595):
    """A PDF of the given pages; each page is a list of (x, y, text), y from the bottom."""
    objs = []

    def add(body):
        objs.append(body)
        return len(objs)

    font = add(b'<< /Type /Font /Subtype /Type1 /BaseFont /Courier /Encoding /WinAnsiEncoding >>')
    pages_id = len(objs) + 1 + 2 * len(pages)  # reserved: after every page and its content
    kids = []
    for items in pages:
        stream = ''.join(f'BT /F1 {FONT_SIZE} Tf {x:.2f} {y:.2f} Td ({_esc(t)}) Tj ET\n' for x, y, t in items).encode('latin-1')
        content = add(b'<< /Length %d >>\nstream\n' % len(stream) + stream + b'endstream')
        kids.append(add(('<< /Type /Page /Parent %d 0 R /MediaBox [0 0 %d %d] /Resources << /Font << /F1 %d 0 R >> >> /Contents %d 0 R >>'
                         % (pages_id, width, height, font, content)).encode()))
    assert add(('<< /Type /Pages /Kids [%s] /Count %d >>' % (' '.join(f'{k} 0 R' for k in kids), len(kids))).encode()) == pages_id
    catalog = add(b'<< /Type /Catalog /Pages %d 0 R >>' % pages_id)

    out = bytearray(b'%PDF-1.4\n')
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += b'%d 0 obj\n' % i + body + b'\nendobj\n'
    xref = len(out)
    out += b'xref\n0 %d\n0000000000 65535 f \n' % (len(objs) + 1)
    for off in offsets:
        out += b'%010d 00000 n \n' % off
    out += b'trailer\n<< /Size %d /Root %d 0 R >>\nstartxref\n%d\n%%%%EOF\n' % (len(objs) + 1, catalog, xref)
    return bytes(out)


def right(x_right, text, y):
    """Text whose last character ends at x_right, as a bank prints a figure."""
    return (x_right - len(text) * CHAR_W, y, text)


def money(v):
    """1234567.5 -> '12,34,567.50' (Indian grouping)."""
    neg = v < 0
    whole, frac = f'{abs(v):.2f}'.split('.')
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ','.join(groups + [tail])
    return ('-' if neg else '') + whole + '.' + frac


def statement_pdf(rows, *, opening, layout='two_columns', newest_first=False, title='ACCOUNT STATEMENT',
                  header_lines=None, rows_per_page=30, narration_wrap=None, headings=None, date_fmt=None):
    """
    A statement no bank parser knows. rows: [(day 'DD/MM/YYYY', narration, ref, withdrawal, deposit)].
    layout:
      'two_columns'  Date | Narration | Ref | Withdrawal | Deposit | Balance
      'signed'       Date | Narration | Ref | Amount (minus = out) | Balance
      'dr_cr'        Date | Narration | Ref | Amount | Dr/Cr | Balance
    The balances are worked out here from the opening, so the chain holds by construction.
    narration_wrap: a second narration line under each row (a continuation line).
    """
    bal = opening
    lines = []
    for day, narr, ref, wd, dep in rows:
        bal = round(bal - wd + dep, 2)
        lines.append((date_fmt(day) if date_fmt else day, narr, ref, wd, dep, bal))
    if newest_first:
        lines.reverse()

    x_date, x_narr, x_ref = 30, 110, 380
    if layout == 'two_columns':
        heads = headings or ['Date', 'Narration', 'Ref No', 'Withdrawal', 'Deposit', 'Balance']
        rights = [None, None, None, 560, 660, 780]
    elif layout == 'signed':
        heads = headings or ['Date', 'Description', 'Reference', 'Amount', 'Balance']
        rights = [None, None, None, 620, 780]
    else:
        heads = headings or ['Txn Date', 'Particulars', 'Chq No', 'Amount', 'Dr/Cr', 'Balance']
        rights = [None, None, None, 600, 650, 780]

    def header_items(y):
        items = [(x_date, y, heads[0]), (x_narr, y, heads[1]), (x_ref, y, heads[2])]
        for h, r in zip(heads[3:], rights[3:]):
            items.append(right(r, h, y))
        return items

    pages, items, y, n_on_page = [], [], 560, 0

    def new_page(first):
        nonlocal items, y, n_on_page
        if items:
            pages.append(items)
        items, y, n_on_page = [], 560, 0
        if first:
            items.append((30, y, title))
            y -= 14
            for hl in (header_lines or []):
                items.append((30, y, hl))
                y -= 12
            y -= 6
        items.extend(header_items(y))
        y -= 16

    new_page(True)
    for day, narr, ref, wd, dep, b in lines:
        if n_on_page >= rows_per_page:
            items.append((30, 20, f'Page {len(pages) + 1}'))
            new_page(False)
        items += [(x_date, y, day), (x_narr, y, narr)]
        if ref:
            items.append((x_ref, y, ref))
        if layout == 'two_columns':
            if wd:
                items.append(right(560, money(wd), y))
            if dep:
                items.append(right(660, money(dep), y))
        elif layout == 'signed':
            items.append(right(620, money(dep if dep else -wd), y))
        else:
            items.append(right(600, money(wd or dep), y))
            items.append(right(650, 'Dr' if wd else 'Cr', y))
        items.append(right(780, money(b), y))
        y -= 12
        if narration_wrap:
            items.append((x_narr, y, narration_wrap))
            y -= 12
        n_on_page += 1
    items.append((30, 20, f'Page {len(pages) + 1}'))
    pages.append(items)
    return pdf_bytes(pages), lines
