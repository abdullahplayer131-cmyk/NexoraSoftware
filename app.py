import os, json, hmac, hashlib, secrets, sqlite3, uuid, shutil, threading, time
from datetime import datetime, timezone, timedelta
from functools import wraps
from urllib.parse import urlparse
import requests
from flask import Flask, g, request, jsonify, render_template, session, redirect, url_for, abort, flash, Response
from werkzeug.utils import secure_filename

app = Flask(__name__)

_secret = os.environ.get("FLASK_SECRET_KEY", "").strip()
if not _secret:
    _secret = secrets.token_hex(32)
    print("WARNING: FLASK_SECRET_KEY is not set. Admin sessions will reset on every restart.", flush=True)
app.secret_key = _secret

app.config["DATABASE"] = os.environ.get("DATABASE_PATH", "").strip() or "/tmp/nexora.db"
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024  # limit for ONE request (a chunk), not for the whole file
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = True
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=8)

# No default password: if the environment variable is missing, admin login stays disabled.
ADMIN_PASSWORD = os.environ.get("NEXORA_ADMIN_PASSWORD", "")
PATRINS_KEY = os.environ.get("PATRINS_API_KEY", "").strip()
BASE = "https://patrins.com/api/"
ALLOWED = {".apk", ".exe", ".zip"}
MIME = {".apk": "application/vnd.android.package-archive",
        ".exe": "application/vnd.microsoft.portable-executable",
        ".zip": "application/zip"}
try:
    MAX_UPLOAD_MB = max(1, min(2048, int(os.environ.get("MAX_UPLOAD_MB", "600"))))
except ValueError:
    MAX_UPLOAD_MB = 600
MAX_FILE = MAX_UPLOAD_MB * 1024 * 1024
CHUNK_SIZE = 1024 * 1024  # 1 MB per request
UPLOAD_TEMP = "/tmp/nexora_upload_chunks"
STUCK_AFTER = 90 * 60  # seconds before an "uploading" job is reported as failed

INSERT_PRODUCT = """INSERT INTO products(name,category,price,tag,description,subtitle,icon,download_file_id,download_file_name,download_file_size)
VALUES(?,?,?,?,?,?,?,?,?,?)"""

def db():
    if "db" not in g:
        p = app.config["DATABASE"]
        os.makedirs(os.path.dirname(os.path.abspath(p)), exist_ok=True)
        g.db = sqlite3.connect(p, timeout=30)
        g.db.row_factory = sqlite3.Row
    return g.db

@app.teardown_appcontext
def close_db(_):
    c = g.pop("db", None)
    if c: c.close()

def init_db():
    p = app.config["DATABASE"]
    os.makedirs(os.path.dirname(os.path.abspath(p)), exist_ok=True)
    c = sqlite3.connect(p, timeout=30)
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
        cols = {r[1] for r in c.execute(f"PRAGMA table_info({table})")}
        if col not in cols: c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {definition}")
    c.commit()
    c.close()

def admin_required(fn):
    @wraps(fn)
    def wrap(*a, **kw):
        if not session.get("admin"):
            # Upload routes are called by JavaScript fetch(): they must get JSON, not a redirect to an HTML page.
            if request.path.startswith("/admin/chunk-upload/"):
                return jsonify(error="Session expired. Please sign in again."), 401
            return redirect(url_for("admin_login"))
        return fn(*a, **kw)
    return wrap

def auth_headers():
    if not PATRINS_KEY: raise RuntimeError("PATRINS_API_KEY is missing in Hostless environment variables.")
    return {"Authorization": "Bearer " + PATRINS_KEY}

def patrins_error(r, step):
    body = (r.text or "")[:200].replace("\n", " ")
    return RuntimeError(f"Patrins {step} failed (HTTP {r.status_code}): {body}")

def upload_to_patrins(path, filename):
    """Upload a file that is already on disk. The file is streamed; it is never loaded fully into RAM."""
    name = secure_filename(filename or "")
    ext = os.path.splitext(name)[1].lower()
    if not name or ext not in ALLOWED: raise ValueError("Only APK, EXE, or ZIP files are allowed.")
    size = os.path.getsize(path)
    if size < 1: raise ValueError("Selected file is empty.")
    if size > MAX_FILE: raise ValueError(f"Maximum upload size is {MAX_UPLOAD_MB} MB.")
    headers = auth_headers()
    r = requests.post(BASE+"upload-direct/init",
        headers={**headers,"Content-Type":"application/json"},
        json={"fileName":name,"fileSize":size,"mimeType":MIME[ext]},timeout=(20,120))
    if r.status_code >= 400: raise patrins_error(r, "init")
    try: init = r.json()
    except ValueError: raise RuntimeError("Patrins init returned an invalid response.")
    upload_id, upload_url = init.get("uploadId"), init.get("uploadUrl")
    if not upload_id or not upload_url: raise RuntimeError("Patrins init response is missing uploadId/uploadUrl.")
    if upload_url.startswith("/"): upload_url = "https://patrins.com" + upload_url
    u = urlparse(upload_url)
    if u.scheme != "https" or u.hostname != "patrins.com": raise RuntimeError("Unexpected Patrins upload URL.")
    with open(path, "rb") as fh:
        # data=<file object> makes requests stream the file from disk with a Content-Length header.
        r = requests.put(upload_url,headers={**headers,"Content-Type":"application/octet-stream"},
                         data=fh,timeout=(20,900))
    if r.status_code >= 400: raise patrins_error(r, "file upload")
    r = requests.post(BASE+"upload-finalize",
        headers={**headers,"Content-Type":"application/json"},
        json={"uploadId":upload_id},timeout=(20,120))
    if r.status_code >= 400: raise patrins_error(r, "finalize")
    try: result = r.json()
    except ValueError: raise RuntimeError("Patrins finalize returned an invalid response.")
    f = result.get("file") or {}
    if not result.get("success") or not f.get("id"): raise RuntimeError("Patrins did not confirm file upload.")
    return {"id":str(f["id"]),"name":f.get("name") or name,"size":int(f.get("size") or size)}

def hash_token(t): return hashlib.sha256(t.encode()).hexdigest()

def get_order(order_id, token):
    if not token or len(token) > 200: return None
    o = db().execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
    if not o or not o["download_token_hash"]: return None
    return o if hmac.compare_digest(o["download_token_hash"], hash_token(token)) else None

@app.get("/")
def home():
    p = db().execute("SELECT * FROM products WHERE active=1 ORDER BY id DESC").fetchall()
    return render_template("index.html", products=p)

@app.get("/api/products")
def products_api():
    rows = db().execute("""SELECT id,name,category,price,tag,description,subtitle,icon
    FROM products WHERE active=1 ORDER BY id DESC""").fetchall()
    return jsonify([dict(x) for x in rows])

@app.post("/api/orders")
def create_order():
    d = request.get_json(silent=True) or {}
    name, email, items = (d.get("name") or "").strip(), (d.get("email") or "").strip(), d.get("items")
    if not name or len(name)>120 or "@" not in email or len(email)>254 or not isinstance(items,list) or not items or len(items)>100:
        return jsonify(error="Please provide a name, valid email, and at least one item."),400
    ids = []
    for x in items:
        try: ids.append(int(x.get("id")))
        except (ValueError,TypeError,AttributeError): return jsonify(error="Invalid cart item."),400
    unique = list(set(ids)); marks = ",".join("?" for _ in unique)
    rows = db().execute(f"SELECT id,name,price FROM products WHERE active=1 AND id IN ({marks})",unique).fetchall()
    byid = {x["id"]:x for x in rows}
    if len(byid) != len(unique): return jsonify(error="A product is unavailable. Refresh and try again."),400
    counts = {}
    for i in ids:
        counts[i] = counts.get(i,0)+1
        if counts[i]>20: return jsonify(error="Maximum quantity exceeded."),400
    normalized = [{"id":i,"name":byid[i]["name"],"quantity":q,"unit_price":byid[i]["price"]} for i,q in counts.items()]
    total = sum(x["quantity"]*x["unit_price"] for x in normalized)
    token = secrets.token_urlsafe(32)
    cur = db().execute("""INSERT INTO orders(customer_name,email,items_json,total,status,created_at,download_token_hash)
    VALUES(?,?,?,?,?,?,?)""",(name,email,json.dumps(normalized),total,"pending",
    datetime.now(timezone.utc).isoformat(timespec="seconds"),hash_token(token)))
    db().commit()
    return jsonify(order_id=cur.lastrowid,total=total,status="pending",download_token=token,
        message="Demo order saved; no payment has been taken. Download appears after payment is confirmed."),201

@app.get("/api/orders/<int:order_id>/status")
def order_status_api(order_id):
    o = get_order(order_id,request.args.get("token",""))
    if not o: return jsonify(error="Order not found."),404
    return jsonify(status=o["status"])

@app.get("/api/orders/<int:order_id>/download")
def download_order(order_id):
    o = get_order(order_id,request.args.get("token",""))
    if not o: return jsonify(error="Order not found."),404
    if o["status"] != "paid": return jsonify(error="Payment has not been confirmed."),403
    try:
        items = json.loads(o["items_json"]); ids = list(dict.fromkeys(int(x["id"]) for x in items))
        marks = ",".join("?" for _ in ids)
        rows = db().execute(f"SELECT id,download_file_id FROM products WHERE id IN ({marks})",ids).fetchall()
        mapping = {x["id"]:x["download_file_id"] for x in rows}
        file_ids = [mapping.get(i) for i in ids]
        if not file_ids or any(not x for x in file_ids):
            return jsonify(error="A product file is missing. Contact the store owner."),409
        r = requests.post(BASE+"download/bulkDownload",
            headers={**auth_headers(),"Content-Type":"application/json"},
            json={"fileIds":file_ids},stream=True,timeout=(20,300))
        if r.status_code >= 400:
            r.close()
            return jsonify(error="Patrins could not prepare the ZIP download."),502
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
    if not ADMIN_PASSWORD:
        flash("Admin login is disabled: NEXORA_ADMIN_PASSWORD is not set on the server.")
        return redirect(url_for("admin_login"))
    pw = request.form.get("password","")
    if hmac.compare_digest(pw.encode("utf-8"), ADMIN_PASSWORD.encode("utf-8")):
        session.clear()
        session["admin"] = True
        session.permanent = True
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
    return render_template("admin.html",products=products,orders=orders,max_upload_mb=MAX_UPLOAD_MB)

# Legacy single-request route retained for compatibility (the dashboard now uses the chunk uploader).
@app.post("/admin/products")
@admin_required
def add_product():
    name = (request.form.get("name") or "").strip()
    category = (request.form.get("category") or "Windows Software").strip()
    desc = (request.form.get("description") or "").strip()
    tag = (request.form.get("tag") or "New").strip()
    icon = (request.form.get("icon") or "software").strip()
    try: price = int(request.form.get("price","0"))
    except ValueError: price = 0
    fs = request.files.get("product_file")
    if not name or len(name)>120 or price<1 or price>10000000:
        flash("Enter a product name and valid price.")
        return redirect(url_for("admin_dashboard"))
    if not fs or not fs.filename:
        flash("Choose an APK, EXE, or ZIP file.")
        return redirect(url_for("admin_dashboard"))
    os.makedirs(UPLOAD_TEMP, exist_ok=True)
    tmp_path = os.path.join(UPLOAD_TEMP, "legacy_" + uuid.uuid4().hex)
    try:
        fs.save(tmp_path)  # streamed to disk, not read into RAM
        f = upload_to_patrins(tmp_path, fs.filename)
    except requests.RequestException:
        app.logger.exception("Patrins upload request failed")
        flash("Patrins upload failed. Check API key and hosting logs.")
        return redirect(url_for("admin_dashboard"))
    except (ValueError,RuntimeError) as e:
        flash(str(e))
        return redirect(url_for("admin_dashboard"))
    finally:
        try: os.remove(tmp_path)
        except OSError: pass
    db().execute(INSERT_PRODUCT,(name,category,price,tag,desc,"Digital product • ZIP delivery",icon,f["id"],f["name"],f["size"]))
    db().commit()
    flash("Product and file uploaded to Patrins.")
    return redirect(url_for("admin_dashboard"))

# ---------------- Chunk upload ----------------
# Flow: start -> part 0..N-1 -> finish (assemble on disk, then a background thread uploads to Patrins)
#       -> browser polls /status until state is "done" or "error".

def chunk_folder(upload_id):
    if len(upload_id)!=32 or any(c not in "0123456789abcdef" for c in upload_id): abort(400)
    return os.path.join(UPLOAD_TEMP,upload_id)

def status_path(folder): return os.path.join(folder,"status.json")

def write_status(folder, **data):
    data["updated"] = time.time()
    tmp = os.path.join(folder,"status.json.tmp")
    with open(tmp,"w",encoding="utf-8") as f: json.dump(data,f)
    os.replace(tmp,status_path(folder))

def read_status(folder):
    try:
        with open(status_path(folder),encoding="utf-8") as f: return json.load(f)
    except (OSError,ValueError):
        return None

def cleanup_old_uploads():
    if not os.path.isdir(UPLOAD_TEMP): return
    cutoff = time.time() - 2*3600
    for n in os.listdir(UPLOAD_TEMP):
        p = os.path.join(UPLOAD_TEMP,n)
        try:
            if os.path.getmtime(p) < cutoff:
                shutil.rmtree(p,ignore_errors=True) if os.path.isdir(p) else os.remove(p)
        except OSError:
            pass

def assemble_file(folder, meta):
    """Join chunk files into one file on disk (streamed), deleting each chunk right after it is copied."""
    total_parts = (meta["size"]+CHUNK_SIZE-1)//CHUNK_SIZE
    assembled_path = os.path.join(folder,"assembled.upload")
    total = 0
    with open(assembled_path,"wb") as out:
        for part in range(total_parts):
            path = os.path.join(folder,f"{part:06d}.part")
            if not os.path.isfile(path): raise ValueError(f"Chunk {part+1} is missing. Please retry the upload.")
            expected = min(CHUNK_SIZE,meta["size"]-total)
            if os.path.getsize(path)!=expected: raise ValueError(f"Chunk {part+1} has an invalid size. Please retry.")
            with open(path,"rb") as src: shutil.copyfileobj(src,out,1024*1024)
            os.remove(path)
            total += expected
    if os.path.getsize(assembled_path)!=meta["size"]: raise ValueError("Uploaded file size does not match.")
    return assembled_path

def run_patrins_job(folder, meta):
    """Runs in a background thread: assemble on disk, stream to Patrins, save the product.
    The browser request returns immediately, so slow disks or slow uploads never hit the gunicorn/Cloudflare timeouts."""
    assembled = os.path.join(folder,"assembled.upload")
    started = time.time()
    try:
        write_status(folder,state="assembling",started=started,message="Preparing file on server...")
        assemble_file(folder,meta)
        write_status(folder,state="uploading",started=started,message="Uploading to Patrins...")
        uploaded = upload_to_patrins(assembled, meta["filename"])
        conn = sqlite3.connect(app.config["DATABASE"], timeout=30)
        try:
            cur = conn.execute(INSERT_PRODUCT,(meta["name"],meta["category"],meta["price"],meta["tag"],meta["description"],
                "Digital product • ZIP delivery",meta["icon"],uploaded["id"],uploaded["name"],uploaded["size"]))
            conn.commit()
            product_id = cur.lastrowid
        finally:
            conn.close()
        write_status(folder,state="done",started=started,product_id=product_id,
                     message="Product uploaded and added to the store.")
    except requests.RequestException:
        app.logger.exception("Could not reach Patrins")
        write_status(folder,state="error",started=started,error="Could not reach Patrins (network/timeout). Check Hostless logs.")
    except (ValueError,RuntimeError) as e:
        app.logger.error("Upload job failed: %s", e)
        write_status(folder,state="error",started=started,error=str(e))
    except OSError:
        app.logger.exception("Disk error during upload")
        try: write_status(folder,state="error",started=started,error="Server disk error (probably out of space). Try a smaller file.")
        except OSError: pass
    except Exception:
        app.logger.exception("Unexpected upload error")
        try: write_status(folder,state="error",started=started,error="Unexpected server error. Check Hostless logs.")
        except OSError: pass
    finally:
        try:
            for n in os.listdir(folder):
                if n.endswith(".part") or n=="assembled.upload":
                    try: os.remove(os.path.join(folder,n))
                    except OSError: pass
        except OSError:
            pass

@app.post("/admin/chunk-upload/start")
@admin_required
def chunk_upload_start():
    d = request.get_json(silent=True) or {}
    name = (d.get("name") or "").strip()
    filename = secure_filename(d.get("filename") or "")
    category = (d.get("category") or "Windows Software").strip()
    desc = (d.get("description") or "").strip()
    tag = (d.get("tag") or "New").strip()
    icon = (d.get("icon") or "software").strip()
    try:
        price, size = int(d.get("price",0)), int(d.get("size",0))
    except (ValueError,TypeError):
        return jsonify(error="Invalid price or file size."),400
    ext = os.path.splitext(filename)[1].lower()
    if not name or len(name)>120 or price<1 or price>10000000:
        return jsonify(error="Enter a valid product name and price."),400
    if ext not in ALLOWED: return jsonify(error="Only APK, EXE, or ZIP files are allowed."),400
    if size<1 or size>MAX_FILE: return jsonify(error=f"File must be between 1 byte and {MAX_UPLOAD_MB} MB."),400
    os.makedirs(UPLOAD_TEMP,exist_ok=True)
    cleanup_old_uploads()
    free = shutil.disk_usage(UPLOAD_TEMP).free
    if free < size + 64*1024*1024:
        return jsonify(error=f"Server temporary disk has only {free//(1024*1024)} MB free; this file needs about {size//(1024*1024)+64} MB. Use a smaller file."),507
    upload_id = uuid.uuid4().hex
    folder = chunk_folder(upload_id)
    os.makedirs(folder,exist_ok=False)
    with open(os.path.join(folder,"metadata.json"),"w",encoding="utf-8") as f:
        json.dump({"name":name,"filename":filename,"category":category,"description":desc,
                   "tag":tag,"icon":icon,"price":price,"size":size},f)
    return jsonify(upload_id=upload_id,chunk_size=CHUNK_SIZE),200

@app.post("/admin/chunk-upload/<upload_id>/<int:part>")
@admin_required
def chunk_upload_part(upload_id,part):
    folder = chunk_folder(upload_id)
    metadata_path = os.path.join(folder,"metadata.json")
    if not os.path.isfile(metadata_path):
        return jsonify(error="Upload session not found. Start again."),404
    try:
        with open(metadata_path,encoding="utf-8") as f: meta = json.load(f)
    except (OSError,ValueError):
        return jsonify(error="Upload session is damaged. Start again."),400
    total_parts = (meta["size"]+CHUNK_SIZE-1)//CHUNK_SIZE
    if part<0 or part>=total_parts: return jsonify(error="Invalid chunk number."),400
    chunk = request.files.get("chunk")
    if not chunk: return jsonify(error="Chunk is missing."),400
    data = chunk.read(CHUNK_SIZE+1)
    if not data or len(data)>CHUNK_SIZE: return jsonify(error="Invalid chunk size."),400
    final = os.path.join(folder,f"{part:06d}.part")
    with open(final+".tmp","wb") as f: f.write(data)
    os.replace(final+".tmp",final)
    return jsonify(success=True,part=part),200

@app.post("/admin/chunk-upload/<upload_id>/finish")
@admin_required
def chunk_upload_finish(upload_id):
    folder = chunk_folder(upload_id)
    metadata_path = os.path.join(folder,"metadata.json")
    if not os.path.isfile(metadata_path): return jsonify(error="Upload session not found. Start again."),404
    # Only one finish call per upload: a double click must not create a duplicate product.
    try:
        fd = os.open(os.path.join(folder,"finish.lock"),os.O_CREAT|os.O_EXCL|os.O_WRONLY)
        os.close(fd)
    except FileExistsError:
        return jsonify(success=True,state="uploading",message="Already processing."),202
    try:
        with open(metadata_path,encoding="utf-8") as f: meta = json.load(f)
        write_status(folder,state="assembling",started=time.time(),message="Preparing file on server...")
        threading.Thread(target=run_patrins_job,args=(folder,meta),daemon=True).start()
        return jsonify(success=True,state="assembling"),202
    except (KeyError,TypeError,ValueError,OSError):
        app.logger.exception("Could not start upload job")
        shutil.rmtree(folder,ignore_errors=True)
        return jsonify(error="Could not start processing. Check Hostless logs."),500

@app.get("/admin/chunk-upload/<upload_id>/status")
@admin_required
def chunk_upload_status(upload_id):
    folder = chunk_folder(upload_id)
    st = read_status(folder)
    if st is None: return jsonify(error="Upload session not found."),404
    if st.get("state") in ("assembling","uploading") and time.time()-st.get("started",st["updated"])>STUCK_AFTER:
        st = {"state":"error","error":"Upload timed out or the server restarted. Please try again."}
    return jsonify(st)

@app.post("/admin/products/<int:product_id>/toggle")
@admin_required
def toggle_product(product_id):
    db().execute("UPDATE products SET active=CASE active WHEN 1 THEN 0 ELSE 1 END WHERE id=?",(product_id,))
    db().commit()
    return redirect(url_for("admin_dashboard"))

@app.post("/admin/orders/<int:order_id>/status")
@admin_required
def update_order_status(order_id):
    status = request.form.get("status")
    if status not in ("pending","paid","cancelled"): abort(400)
    db().execute("UPDATE orders SET status=? WHERE id=?",(status,order_id))
    db().commit()
    flash("Order status updated. Paid orders can download their ZIP.")
    return redirect(url_for("admin_dashboard"))

@app.get("/health")
def health():
    try:
        db().execute("SELECT 1").fetchone()
        return jsonify(status="ok"),200
    except sqlite3.Error:
        return jsonify(status="database_error"),500

@app.errorhandler(413)
def too_large(_e):
    return "Request too large. Upload using the chunk uploader.",413

init_db()
if __name__ == "__main__":
    app.run(host="0.0.0.0",port=int(os.environ.get("PORT",5000)),debug=False)
