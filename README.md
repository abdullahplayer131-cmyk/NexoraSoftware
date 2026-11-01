# Nexora Software — starter store

A responsive full-stack starter store for Windows software and Android APKs.

## Included
- Premium black-and-gold storefront
- Product catalogue and search/category filters
- Cart stored in the browser
- Python Flask backend with SQLite products/orders
- Admin page to add products (demo admin password)
- Checkout creates a pending order; no payment is collected yet
- Download URLs are placeholders until you configure secure file storage

## Run locally (Windows)
1. Install Python 3.11+ from https://www.python.org/downloads/
2. Open this folder in VS Code.
3. In a terminal run:
   `py -m venv .venv`
   ` .venv\Scripts\activate`
   `py -m pip install -r requirements.txt`
   `py app.py`
4. Open http://127.0.0.1:5000

Demo admin:
- URL: http://127.0.0.1:5000/admin
- Password: change-this-admin-password

IMPORTANT BEFORE PUBLIC LAUNCH
- Change `ADMIN_PASSWORD` in app.py to a long unique secret, preferably through an environment variable.
- This starter does NOT process real payments. Connect an approved payment gateway on the server and verify signed webhooks before marking orders paid.
- Do not upload copyrighted apps, cracked software, malware, or products you lack permission to distribute.
- Implement private object storage / expiring download links before selling files. Never put paid files in `static/`.
- Add privacy policy, terms, refund policy, tax/business details, HTTPS, backups, rate limits, and production server configuration.
