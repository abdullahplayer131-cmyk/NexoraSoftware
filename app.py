import os, sqlite3, json, secrets
from datetime import datetime
from functools import wraps
from flask import Flask, g, request, jsonify, render_template, session, redirect, url_for, abort, flash

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", secrets.token_hex(32))
app.config["DATABASE"] = os.path.join(os.path.dirname(__file__), "nexora.db")
ADMIN_PASSWORD = os.environ.get("NEXORA_ADMIN_PASSWORD", "change-this-admin-password")

SEED = [
    ("Windows 11 Pro Key", "Windows Software", 2499, "Windows", "Activation license for a compatible Windows PC.", "Lifetime license • Instant delivery", "win11"),
    ("Office Productivity Suite", "Windows Software", 2999, "Office", "A productivity suite license for work and study.", "License • PC apps", "office"),
    ("Android Utility Toolkit", "Android APKs", 299, "Android", "A collection of useful Android utilities.", "Android • Utility", "android"),
    ("Security Essentials", "Windows Software", 1499, "Security", "Security software for supported Windows devices.", "1 year • 1 device", "security"),
    ("Creative Design Toolkit", "Windows Software", 399, "Design", "Design resources and tools for creative projects.", "Digital product • Instant access", "design"),
    ("PC Utility Bundle", "Bundles", 999, "Bundle", "A bundle of useful PC productivity utilities.", "Bundle • Great value", "bundle"),
]

def db():
    if "db" not in g:
        g.db = sqlite3.connect(app.config["DATABASE"])
        g.db.row_factory = sqlite3.Row
    return g.db

@app.teardown_appcontext
def close_db(_exc):
    conn = g.pop("db", None)
    if conn is not None: conn.close()

def init_db():
    conn = sqlite3.connect(app.config["DATABASE"])
    conn.execute("""CREATE TABLE IF NOT EXISTS products (
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, category TEXT NOT NULL,
        price INTEGER NOT NULL, tag TEXT DEFAULT '', description TEXT DEFAULT '',
        subtitle TEXT DEFAULT '', icon TEXT DEFAULT 'software', download_url TEXT DEFAULT '',
        active INTEGER NOT NULL DEFAULT 1)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT, customer_name TEXT NOT NULL, email TEXT NOT NULL,
        items_json TEXT NOT NULL, total INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
        created_at TEXT NOT NULL)""")
    if conn.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 0:
        conn.executemany("INSERT INTO products (name,category,price,tag,description,subtitle,icon) VALUES (?,?,?,?,?,?,?)", SEED)
    conn.commit(); conn.close()

def admin_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("admin"): return redirect(url_for("admin_login"))
        return fn(*args, **kwargs)
    return wrapper

@app.get("/")
def home():
    products = db().execute("SELECT * FROM products WHERE active=1 ORDER BY id DESC").fetchall()
    return render_template("index.html", products=products)

@app.get("/api/products")
def products_api():
    rows = db().execute("SELECT id,name,category,price,tag,description,subtitle,icon FROM products WHERE active=1 ORDER BY id DESC").fetchall()
    return jsonify([dict(r) for r in rows])

@app.post("/api/orders")
def create_order():
    data = request.get_json(silent=True) or {}
    name, email, items = (data.get("name") or "").strip(), (data.get("email") or "").strip(), data.get("items")
    if not name or len(name) > 120 or "@" not in email or len(email) > 254 or not isinstance(items, list) or not items:
        return jsonify(error="Please provide a name, valid email, and at least one item."), 400
    ids = []
    for item in items:
        try: ids.append(int(item.get("id")))
        except (ValueError, TypeError, AttributeError): return jsonify(error="Invalid cart item."), 400
    unique_ids = list(set(ids))
    placeholders = ",".join("?" for _ in unique_ids)
    rows = db().execute(f"SELECT id,name,price FROM products WHERE active=1 AND id IN ({placeholders})", unique_ids).fetchall()
    by_id = {r["id"]: r for r in rows}
    if len(by_id) != len(unique_ids): return jsonify(error="A product is unavailable. Refresh the page and try again."), 400
    counts = {}
    for pid in ids: counts[pid] = counts.get(pid, 0) + 1
    normalized = [{"id": pid, "name": by_id[pid]["name"], "quantity": qty, "unit_price": by_id[pid]["price"]} for pid, qty in counts.items()]
    total = sum(x["quantity"] * x["unit_price"] for x in normalized)
    cur = db().execute("INSERT INTO orders (customer_name,email,items_json,total,status,created_at) VALUES (?,?,?,?,?,?)",
        (name,email,json.dumps(normalized),total,"pending",datetime.utcnow().isoformat(timespec="seconds")+"Z"))
    db().commit()
    return jsonify(order_id=cur.lastrowid, total=total, status="pending",
                   message="Order saved. This demo has not taken payment; contact the store owner to complete payment."), 201

@app.get("/admin")
def admin_login():
    if session.get("admin"): return redirect(url_for("admin_dashboard"))
    return render_template("admin_login.html")

@app.post("/admin/login")
def admin_login_post():
    password = request.form.get("password", "")
    if secrets.compare_digest(password, ADMIN_PASSWORD):
        session["admin"] = True
        return redirect(url_for("admin_dashboard"))
    flash("Incorrect password.")
    return redirect(url_for("admin_login"))

@app.post("/admin/logout")
def admin_logout():
    session.clear()
    return redirect(url_for("home"))

@app.get("/admin/dashboard")
@admin_required
def admin_dashboard():
    products = db().execute("SELECT * FROM products ORDER BY id DESC").fetchall()
    orders = db().execute("SELECT * FROM orders ORDER BY id DESC LIMIT 100").fetchall()
    return render_template("admin.html", products=products, orders=orders)

@app.post("/admin/products")
@admin_required
def add_product():
    name = (request.form.get("name") or "").strip()
    category = (request.form.get("category") or "Windows Software").strip()
    desc = (request.form.get("description") or "").strip()
    tag = (request.form.get("tag") or "New").strip()
    icon = (request.form.get("icon") or "software").strip()
    try: price = int(request.form.get("price", "0"))
    except ValueError: price = 0
    if not name or len(name)>120 or price < 1 or price > 10000000:
        flash("Enter a product name and a valid price."); return redirect(url_for("admin_dashboard"))
    db().execute("INSERT INTO products (name,category,price,tag,description,subtitle,icon) VALUES (?,?,?,?,?,?,?)",
                 (name,category,price,tag,desc,"Digital product",icon))
    db().commit(); flash("Product added."); return redirect(url_for("admin_dashboard"))

@app.post("/admin/products/<int:product_id>/toggle")
@admin_required
def toggle_product(product_id):
    db().execute("UPDATE products SET active=CASE active WHEN 1 THEN 0 ELSE 1 END WHERE id=?", (product_id,))
    db().commit(); return redirect(url_for("admin_dashboard"))

@app.post("/admin/orders/<int:order_id>/status")
@admin_required
def order_status(order_id):
    status = request.form.get("status")
    if status not in ("pending","paid","cancelled"): abort(400)
    # Manual status change only. A live payment gateway must verify a signed webhook server-side.
    db().execute("UPDATE orders SET status=? WHERE id=?", (status,order_id)); db().commit()
    flash("Order status updated."); return redirect(url_for("admin_dashboard"))

if __name__ == "__main__":
    init_db()
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", 5000)), debug=False)