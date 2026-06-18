# Deploying the Bank Statement Parser to a VPS

This app is a Flask service that, for **scanned/image-only PDFs** (like the Seiza
HDFC statement), depends on **OCR**. The Docker image already bundles everything
needed — `tesseract-ocr` + `poppler-utils` + all Python deps — so OCR works out of
the box inside the container. Do **not** try to run it with bare `python app.py` on
the VPS; use Docker so OCR is guaranteed present.

## What you get

- `Dockerfile` — production image (gunicorn, Tesseract, Poppler).
- `docker-compose.yml` — runs the app + a Caddy reverse proxy with auto-HTTPS.
- `Caddyfile` — TLS + reverse proxy config (edit the domain).
- `.env` — your secrets/config (copied from `.env.example`).

## Requirements

- A VPS with **≥ 2 GB RAM** (OCR of a 14-page scanned statement needs headroom;
  1 GB will OOM on large files).
- A domain or subdomain you can point at the VPS (for HTTPS). Optional — you can
  also run on `http://VPS_IP:8080` without a domain.

---

## 1. Install Docker on the VPS (Ubuntu/Debian)

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER   # log out/in so `docker` works without sudo
```

## 2. Get the code onto the VPS

```bash
git clone <your-repo-url> bankparser
cd bankparser/bankstatementparser     # the app lives in this subfolder
```

## 3. Configure

```bash
cp .env.example .env
nano .env        # optional: GEMINI_API_KEY (AI fallback), API_KEYS, limits
```

`.env` keys (all optional — the structured parsers, incl. the hardened HDFC one,
work without any of them):

| Key | Purpose |
|-----|---------|
| `GEMINI_API_KEY` | Enables the AI fallback parser for unknown formats |
| `LLM_DAILY_LIMIT` | Cap AI calls/day (default 20) |
| `API_KEYS` | `key:label,key2:label2` for the `/api/v1/parse` public API |
| `API_RATE_LIMIT` | Requests/hour per API key (default 30) |

## 4a. Deploy WITH a domain (recommended — automatic HTTPS)

1. Point a DNS **A record** (e.g. `parser.yourdomain.com`) at the VPS public IP.
2. Edit `Caddyfile` — replace `parser.example.com` with that domain.
3. Launch:

```bash
docker compose up -d --build
```

Caddy fetches a Let's Encrypt certificate automatically. Visit
`https://parser.yourdomain.com`.

## 4b. Deploy WITHOUT a domain (quick test, HTTP only)

In `docker-compose.yml`: comment out the whole `caddy` service and uncomment the
`ports: ["8080:8080"]` block under `app`. Then:

```bash
docker compose up -d --build
```

Open `http://VPS_IP:8080`. (Open the firewall: `sudo ufw allow 8080`.)

---

## 5. Verify

```bash
# health endpoint — reports OCR + AI status
curl -s https://parser.yourdomain.com/status      # or http://VPS_IP:8080/status

# parse a statement end-to-end
curl -F "file=@Seiza.pdf" https://parser.yourdomain.com/parse
```

A healthy `/status` shows OCR available. If it reports Poppler/Tesseract missing,
you're not running inside the Docker image — rebuild with `docker compose build`.

## 6. Operations

```bash
docker compose logs -f app        # tail logs
docker compose restart app        # restart after .env change
git pull && docker compose up -d --build   # deploy an update
```

Persistent data (mounted as volumes, survives rebuilds):
`./learned` (learned bank profiles), `./feedback` (failed-parse uploads),
`./output` (generated Excel/CSV).

---

## Notes & tuning

- **Performance:** OCR is CPU-bound. A 14-page scanned PDF takes ~30–60 s. The
  image runs `gunicorn` with 1 worker / 2 threads and a 120 s timeout — fine for a
  2 GB / 2-vCPU box with light concurrent load. For heavier traffic, give the VPS
  more vCPUs and raise `--workers` in the `Dockerfile` `CMD` (budget ~1 GB RAM per
  worker because of OCR).
- **Caddy timeouts** are set to 180 s to comfortably exceed gunicorn's 120 s.
- **Reusing an existing nginx** instead of Caddy: drop the `caddy` service, keep
  `app` on an internal port, and `proxy_pass http://127.0.0.1:8080;` from your
  nginx server block (also raise `client_max_body_size 50m;` and
  `proxy_read_timeout 180s;`).
- **Subpath hosting** (e.g. `yourdomain.com/tools/bankstatementparser`): set
  `SCRIPT_NAME` back to that path in `docker-compose.yml` and have your proxy send
  the prefix. Serving at a subdomain root (the default here) avoids subpath asset
  edge cases entirely.
