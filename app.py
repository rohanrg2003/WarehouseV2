import sqlite3, os
from flask import Flask, render_template, request, redirect, session, flash

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "wh_secret_2026")

# Vercel allows write access ONLY in /tmp
DB_PATH = "/tmp/warehouse.db"


# ───────────────── DATABASE ─────────────────

def get_conn():
    con = sqlite3.connect(DB_PATH, timeout=10, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    return con


def query(sql, args=(), one=False):
    con = get_conn()
    cur = con.execute(sql, args)
    rows = cur.fetchall()
    con.close()
    return (rows[0] if rows else None) if one else rows


def run(sql, args=()):
    con = get_conn()
    cur = con.execute(sql, args)
    con.commit()
    last_id = cur.lastrowid
    con.close()
    return last_id


def init_db():
    con = get_conn()
    con.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        seller_name TEXT,
        username TEXT UNIQUE,
        password TEXT,
        role TEXT DEFAULT 'seller',
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS categories (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        specs TEXT,
        seller_id INTEGER
    );

    CREATE TABLE IF NOT EXISTS products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        sku TEXT UNIQUE,
        price REAL DEFAULT 0,
        quantity INTEGER DEFAULT 0,
        expiry TEXT,
        seller_id INTEGER
    );

    CREATE TABLE IF NOT EXISTS product_categories (
        product_id INTEGER,
        category_id INTEGER,
        PRIMARY KEY(product_id, category_id)
    );

    CREATE TABLE IF NOT EXISTS transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        seller_id INTEGER,
        product_id INTEGER,
        quantity INTEGER,
        price REAL,
        total_price REAL,
        type TEXT DEFAULT 'Sale',
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    );
    """)

    # create admin if not exists
    try:
        con.execute(
            "INSERT INTO users (seller_name, username, password, role) VALUES (?,?,?,?)",
            ("Administrator", "admin", "admin123", "admin"),
        )
        con.commit()
    except:
        pass

    con.close()


# ensure DB exists on cold start
if not os.path.exists(DB_PATH):
    init_db()


# ───────────────── HELPERS ─────────────────

def is_seller(): return session.get("role") == "seller"
def is_admin(): return session.get("role") == "admin"


# ───────────────── AUTH ─────────────────

@app.route("/")
def home():
    return redirect("/login")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        u = query(
            "SELECT * FROM users WHERE username=? AND password=?",
            (request.form["username"], request.form["password"]),
            one=True,
        )
        if u:
            session["user_id"] = u["id"]
            session["role"] = u["role"]
            session["seller_name"] = u["seller_name"]
            return redirect("/admin" if u["role"] == "admin" else "/seller")
        return render_template("login.html", error="Invalid login")

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")


# ───────────────── ADMIN ─────────────────

@app.route("/admin")
def admin():
    if not is_admin():
        return redirect("/login")

    products = query("SELECT * FROM products")
    revenue = query("SELECT COALESCE(SUM(total_price),0) r FROM transactions", one=True)["r"]

    return render_template("admin.html", products=products, revenue=revenue)


# ───────────────── SELLER DASHBOARD ─────────────────

@app.route("/seller")
def seller():
    if not is_seller():
        return redirect("/login")

    sid = session["user_id"]
    products = query("SELECT * FROM products WHERE seller_id=?", (sid,))
    cats = query("SELECT * FROM categories WHERE seller_id=?", (sid,))

    return render_template("seller.html", products=products, cats=cats)


# ───────────────── ADD PRODUCT ─────────────────

@app.route("/add_product", methods=["POST"])
def add_product():
    if not is_seller():
        return redirect("/login")

    try:
        run(
            "INSERT INTO products(name,sku,price,quantity,expiry,seller_id) VALUES(?,?,?,?,?,?)",
            (
                request.form["name"],
                request.form["sku"],
                request.form["price"],
                request.form["quantity"],
                request.form.get("expiry"),
                session["user_id"],
            ),
        )
        flash("Product added!", "success")
    except Exception as e:
        flash(str(e), "error")

    return redirect("/seller")


# ───────────────── TEST ROUTE ─────────────────
# Use this to verify deployment quickly

@app.route("/test")
def test():
    return "Vercel deployment working ✅"