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
app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024
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
MAX_FILE = 100 * 1024 * 1024
CHUNK_SIZE = 256 * 1024
UPLOAD_TEMP = "/tmp/nexora_upload_chunks"
STUCK_AFTER = 30 * 60  # seconds before an "uploading" job is reported as failed

SEED = [
("Windows 11 Pro Key","Windows Software",2499,"Windows","Activation license for a compatible Windows PC.","Lifetime license • Instant delivery","win11"),
("Office Productivity Suite","Windows Software",2999,"Office","A productivity suite license for work and study.","License • PC apps","office"),
("Android Utility Toolkit","Android APKs",299,"Android","A collection of useful Android utilities.","Android • Utility","android"),
("Security Essentials","Windows Software",1499,"Security","Security software for supported Windows devices.","1 year • 1 device","security"),
("Creative Design Toolkit","Windows Software",399,"Design","Design resources and tools for creative projects.","Digital product • Instant access","design"),
("PC Utility Bundle","Bundles",999,"Bundle","A bundle of useful PC productivity utilities.","Bundle • Great value","bundle")]

INSERT_PRODUCT = """INSERT INTO products(name,category,price,tag,description,subtitle,icon,download_file_id,download_file_name,download_file_size)
VALUES(?,?,?,?,?,?,?,?,?,?)"""

def db():
    if "db" not in g:
        p = app.config["DATABASE"]
        os.makedirs(os.path.dirname(os.path.abspath(p)), exist_ok=True)