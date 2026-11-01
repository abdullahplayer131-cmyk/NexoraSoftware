import os, json, hmac, hashlib, secrets, sqlite3
from datetime import datetime, timezone
from functools import wraps
from urllib.parse import urlparse
import requests
from flask import Flask, g, request, jsonify, render_template, session, redirect, url_for, abort, flash, Response
from werkzeug.utils import secure_filename

app=Flask(__name__)
app.secret_key=os.environ.get("FLASK_SECRET_KEY",secrets.token_hex(32))
app.config["DATABASE"]=os.environ.get("DATABASE_PATH","/tmp/nexora.db")
app.config["MAX_CONTENT_LENGTH"]=100*1024*1024
ADMIN_PASSWORD=os.environ.get("NEXORA_ADMIN_PASSWORD","change-this-admin-password")
PATRINS_KEY=os.environ.get("PATRINS_API_KEY","").strip()
BASE="https://patrins.com/api/"
ALLOWED={".apk",".exe",".zip"}

SEED=[
("Windows 11 Pro Key","Windows Software",2499,"Windows","Activation license for a compatible Windows PC.","Lifetime license • Instant delivery","win11"),
("Office Productivity Suite","Windows Software",2999,"Office","A productivity suite license for work and study.","License • PC apps","office"),
("Android Utility Toolkit","Android APKs",299,"Android","A collection of useful Android utilities.","Android • Utility","android"),
("Security Essentials","Windows Software",1499,"Security","Security software for supported Windows devices.","1 year • 1 device","security"),
("Creative Design Toolkit","Windows Software",399,"Design","Design resources and tools for creative projects.","Digital product • Instant access","design"),
("PC Utility Bundle","Bundles",999,"Bundle","A bundle of useful PC productivity utilities.","Bundle • Great value","bundle")]

def db():
    if "db" not in g:
        p=app.config["DATABASE"]; os.makedirs(os.path.dirname(os.path.abspath(p)),exist_ok=True)
        g.db=sqlite3.connect(p); g.db.row_factory=sqlite3.Row
    return g.db

@app.teardown_appcontext
def close_db(_):
    c=g.pop("db",None)
    if c: c.close()

def init_db():
    p=app.config["DATABASE"]; os.makedirs(os.path.dirname(os.path.abspath(p)),exist_ok=True)
    c=sqlite3.connect(p)
    c.execute("""CREATE TABLE IF NOT EXISTS products(
    id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL,category TEXT NOT NULL,
    price INTEGER NOT NULL,tag TEXT DEFAULT '',description TEXT DEFAULT '',
    subtitle TEXT DEFAULT '',icon TEXT DEFAULT 'software',download_url TEXT DEFAULT '',
    active INTEGER NOT NULL DEFAULT 1)""")
    c.execute("""CREATE TABLE IF NOT EXISTS orders(
    id INTEGER PRIMARY KEY AUTOINCREMENT,customer_name TEXT NOT NULL,email TEXT NOT NULL,
    items_json TEXT NOT NULL,total INTEGER NOT NULL,status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL)""")
    for table,col,definition in [
        ("products","download_file_id","TEXT DEFAULT ''"),
        ("products","download_file_name","TEXT DEFAULT ''"),
        ("products","download_file_size","INTEGER DEFAULT 0"),
        ("orders","download_token_hash","TEXT DEFAULT ''")]:
        cols={r[1] for r in c.execute(f"PRAGMA table_info({table})")}
        if col not in cols: c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {definition}")
    if c.execute("SELECT COUNT(*) FROM products").fetchone()[0]==0:
        c.executemany("""INSERT INTO products(name,category,price,tag,description,subtitle,icon)
        VALUES(?,?,?,?,?,?,?)""",SEED)
    c.commit(); c.close()

def admin_required(fn):
    @wraps(fn)
    def wrap(*a,**kw):
        if not session.get("admin"): return redirect(url_for("admin_login"))
        return fn(*a,**kw)
    return wrap

def auth_headers():
    if not PATRINS_KEY: raise RuntimeError("PATRINS_API_KEY is missing in Hostless environment variables.")
    return {"Authorization":"Bearer "+PATRINS_KEY}

def upload_to_patrins(fs):
    name=secure_filename(fs.filename or "")
    ext=os.path.splitext(name)[1].lower()
    if not name or ext not in ALLOWED: raise ValueError("Only APK, EXE, or ZIP files are allowed.")
    data=fs.read(app.config["MAX_CONTENT_LENGTH"]+1)
    if not data: raise ValueError("Selected file is empty.")
    if len(data)>app.config["MAX_CONTENT_LENGTH"]: raise ValueError("Maximum upload size is 100 MB.")
    mime={".apk":"application/vnd.android.package-archive",".exe":"application/vnd.microsoft.portable-executable",".zip":"application/zip"}[ext]
    headers=auth_headers()
    r=requests.post(BASE+"upload-direct/init",headers={**headers,"Content-Type":"application/json"},
        json={"fileName":name,"fileSize":len(data),"mimeType":mime},timeout=(20,300))
    r.raise_for_status(); init=r.json()
    upload_id=init.get("uploadId"); upload_url=init.get("uploadUrl")
    if not upload_id or not upload_url: raise RuntimeError("Patrins init response is missing uploadId/uploadUrl.")
    if upload_url.startswith("/"): upload_url="https://patrins.com"+upload_url
    u=urlparse(upload_url)
    if u.scheme!="https" or u.hostname!="patrins.com": raise RuntimeError("Unexpected Patrins upload URL.")
    r=requests.put(upload_url,headers={**headers,"Content-Type":"application/octet-stream"},data=data,timeout=(20,300))
    r.raise_for_status()
    r=requests.post(BASE+"upload-finalize",headers={**headers,"Content-Type":"application/json"},
        json={"uploadId":upload_id},timeout=(20,300))
    r.raise_for_status(); result=r.json(); f=result.get("file") or {}
    if not result.get("success") or not f.get("id"): raise RuntimeError("Patrins did not confirm file upload.")
    return {"id":str(f["id"]),"name":f.get("name") or name,"size":int(f.get("size") or len(data))}

def hash_token(t): return hashlib.sha256(t.encode()).hexdigest()
def get_order(order_id,token):
    if not token or len(token)>200: return None
    o=db().execute("SELECT * FROM orders WHERE id=?",(order_id,)).fetchone()
    if not o or not o["download_token_hash"]: return None
    return o if hmac.compare_digest(o["download_token_hash"],hash_token(token)) else None

@app.get("/")
def home():
    p=db().execute("SELECT * FROM products WHERE active=1 ORDER BY id DESC").fetchall()
    return render_template("index.html",products=p)

@app.get("/api/products")
def products_api():
    rows=db().execute("""SELECT id,name,category,price,tag,description,subtitle,icon
    FROM products WHERE active=1 ORDER BY id DESC""").fetchall()
    return jsonify([dict(x) for x in rows])

@app.post("/api/orders")
def create_order():
    d=request.get_json(silent=True) or {}; name=(d.get("name") or "").strip()
    email=(d.get("email") or "").strip(); items=d.get("items")
    if not name or len(name)>120 or "@" not in email or len(email)>254 or not isinstance(items,list) or not items or len(items)>100:
        return jsonify(error="Please provide a name, valid email, and at least one item."),400
    ids=[]
    for x in items:
        try: ids.append(int(x.get("id")))
        except (ValueError,TypeError,AttributeError): return jsonify(error="Invalid cart item."),400
    unique=list(set(ids)); marks=",".join("?" for _ in unique)
    rows=db().execute(f"SELECT id,name,price FROM products WHERE active=1 AND id IN ({marks})",unique).fetchall()
    byid={x["id"]:x for x in rows}
    if len(byid)!=len(unique): return jsonify(error="A product is unavailable. Refresh and try again."),400
    counts={}
    for i in ids:
        counts[i]=counts.get(i,0)+1
        if counts[i]>20: return jsonify(error="Maximum quantity exceeded."),400
    normalized=[{"id":i,"name":byid[i]["name"],"quantity":q,"unit_price":byid[i]["price"]} for i,q in counts.items()]
    total=sum(x["quantity"]*x["unit_price"] for x in normalized); token=secrets.token_urlsafe(32)
    cur=db().execute("""INSERT INTO orders(customer_name,email,items_json,total,status,created_at,download_token_hash)
    VALUES(?,?,?,?,?,?,?)""",(name,email,json.dumps(normalized),total,"pending",datetime.now(timezone.utc).isoformat(timespec="seconds"),hash_token(token)))
    db().commit()
    return jsonify(order_id=cur.lastrowid,total=total,status="pending",download_token=token,
        message="Demo order saved; no payment has been taken. Download appears after the store confirms payment."),201

@app.get("/api/orders/<int:order_id>/status")
def order_status_api(order_id):
    o=get_order(order_id,request.args.get("token",""))
    if not o: return jsonify(error="Order not found."),404
    return jsonify(status=o["status"])

@app.get("/api/orders/<int:order_id>/download")
def download_order(order_id):
    o=get_order(order_id,request.args.get("token",""))
    if not o: return jsonify(error="Order not found."),404
    if o["status"]!="paid": return jsonify(error="Payment has not been confirmed."),403
    try:
        items=json.loads(o["items_json"]); ids=list(dict.fromkeys(int(x["id"]) for x in items))
        marks=",".join("?" for _ in ids)
        rows=db().execute(f"SELECT id,download_file_id FROM products WHERE id IN ({marks})",ids).fetchall()
        mapping={x["id"]:x["download_file_id"] for x in rows}
        file_ids=[mapping.get(i) for i in ids]
        if not file_ids or any(not x for x in file_ids):
            return jsonify(error="A product file is missing. Contact the store owner."),409
        r=requests.post(BASE+"download/bulkDownload",headers={**auth_headers(),"Content-Type":"application/json"},
            json={"fileIds":file_ids},stream=True,timeout=(20,300))
        if r.status_code>=400:
            r.close(); return jsonify(error="Patrins could not prepare the ZIP download."),502
        def stream():
            try:
                for chunk in r.iter_content(65536):
                    if chunk: yield chunk
            finally: r.close()
        return Response(stream(),mimetype="application/zip",headers={
            "Content-Disposition":f'attachment; filename="Nexora-order-{order_id}.zip"',
            "Cache-Control":"no-store","X-Content-Type-Options":"nosniff"})
    except (requests.RequestException,ValueError,KeyError,TypeError):
        app.logger.exception("Download error")
        return jsonify(error="Download service temporarily unavailable."),502

@app.get("/admin")
def admin_login():
    if session.get("admin"): return redirect(url_for("admin_dashboard"))
    return render_template("admin_login.html")

@app.post("/admin/login")
def admin_login_post():
    pw=request.form.get("password","")
    if secrets.compare_digest(pw,ADMIN_PASSWORD):
        session["admin"]=True; return redirect(url_for("admin_dashboard"))
    flash("Incorrect password."); return redirect(url_for("admin_login"))

@app.post("/admin/logout")
def admin_logout():
    session.clear(); return redirect(url_for("home"))

@app.get("/admin/dashboard")
@admin_required
def admin_dashboard():
    products=db().execute("SELECT * FROM products ORDER BY id DESC").fetchall()
    orders=db().execute("SELECT * FROM orders ORDER BY id DESC LIMIT 100").fetchall()
    return render_template("admin.html",products=products,orders=orders)

@app.post("/admin/products")
@admin_required
def add_product():
    name=(request.form.get("name") or "").strip(); category=(request.form.get("category") or "Windows Software").strip()
    desc=(request.form.get("description") or "").strip(); tag=(request.form.get("tag") or "New").strip()
    icon=(request.form.get("icon") or "software").strip()
    try: price=int(request.form.get("price","0"))
    except ValueError: price=0
    fs=request.files.get("product_file")
    if not name or len(name)>120 or price<1 or price>10000000:
        flash("Enter a product name and valid price."); return redirect(url_for("admin_dashboard"))
    if not fs or not fs.filename:
        flash("Choose an APK, EXE, or ZIP file."); return redirect(url_for("admin_dashboard"))
    try: f=upload_to_patrins(fs)
    except requests.RequestException:
        app.logger.exception("Patrins upload request failed")
        flash("Patrins upload failed. Check API key and hosting logs."); return redirect(url_for("admin_dashboard"))
    except (ValueError,RuntimeError) as e:
        flash(str(e)); return redirect(url_for("admin_dashboard"))
    db().execute("""INSERT INTO products(name,category,price,tag,description,subtitle,icon,download_file_id,download_file_name,download_file_size)
    VALUES(?,?,?,?,?,?,?,?,?,?)""",(name,category,price,tag,desc,"Digital product • ZIP delivery",icon,f["id"],f["name"],f["size"]))
    db().commit(); flash("Product and file uploaded to Patrins."); return redirect(url_for("admin_dashboard"))

@app.post("/admin/products/<int:product_id>/toggle")
@admin_required
def toggle_product(product_id):
    db().execute("UPDATE products SET active=CASE active WHEN 1 THEN 0 ELSE 1 END WHERE id=?",(product_id,))
    db().commit(); return redirect(url_for("admin_dashboard"))

@app.post("/admin/orders/<int:order_id>/status")
@admin_required
def update_order_status(order_id):
    status=request.form.get("status")
    if status not in ("pending","paid","cancelled"): abort(400)
    db().execute("UPDATE orders SET status=? WHERE id=?",(status,order_id)); db().commit()
    flash("Order status updated. Paid orders can download their ZIP."); return redirect(url_for("admin_dashboard"))

@app.get("/health")
def health():
    try: db().execute("SELECT 1").fetchone(); return jsonify(status="ok"),200
    except sqlite3.Error: return jsonify(status="database_error"),500

@app.errorhandler(413)
def too_large(_e): return "File too large. Maximum upload size is 100 MB.",413

init_db()
if __name__=="__main__":
    app.run(host="0.0.0.0",port=int(os.environ.get("PORT",5000)),debug=False)
