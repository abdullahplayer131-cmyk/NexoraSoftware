# Nexora Software

Flask store for genuine digital software. Admin uploads files to Patrins; customers order and download after payment is confirmed.

## Hostless settings
- Start command: `gunicorn app:app --bind 0.0.0.0:$PORT`
- Environment variables (set in Hostless, never in code): `NEXORA_ADMIN_PASSWORD`, `FLASK_SECRET_KEY`, `PATRINS_API_KEY`, `DATABASE_PATH`

## Notes
- `/tmp` storage is temporary: products/orders are lost on restart until a persistent volume or database is added.
- Payments are not automated yet. The admin marks orders Paid only after confirming payment independently.
- Never commit `.db` files, `.env` files, or API keys.
