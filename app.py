import sqlite3, os
from flask import Flask, render_template, request, redirect, session, flash

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "wh_secret_2026")

DB_PATH = "/tmp/warehouse.db" if os.environ.get("VERCEL") else "warehouse.db"


# ── database ─────────────────────────────────────────────────

def get_conn():
    con = sqlite3.connect(DB_PATH, timeout=10, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    return con

def query(sql, args=(), one=False):
    con = get_conn()
    rows = con.execute(sql, args).fetchall()
    con.close()
    return (rows[0] if rows else None) if one else rows

def run(sql, args=()):
    con = get_conn()
    cur = con.execute(sql, args)
    con.commit()
    lid = cur.lastrowid
    con.close()
    return lid

def init_db():
    con = get_conn()
    con.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            seller_name TEXT    NOT NULL DEFAULT '',
            username    TEXT    NOT NULL UNIQUE,
            password    TEXT    NOT NULL,
            role        TEXT    NOT NULL DEFAULT 'seller',
            created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS categories (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT    NOT NULL,
            specs       TEXT    DEFAULT '',
            seller_id   INTEGER NOT NULL,
            created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS products (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT    NOT NULL,
            sku         TEXT    UNIQUE,
            price       REAL    NOT NULL DEFAULT 0,
            quantity    INTEGER NOT NULL DEFAULT 0,
            expiry      TEXT,
            seller_id   INTEGER NOT NULL,
            created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS product_categories (
            product_id  INTEGER NOT NULL,
            category_id INTEGER NOT NULL,
            PRIMARY KEY (product_id, category_id)
        );
        CREATE TABLE IF NOT EXISTS transactions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            seller_id   INTEGER NOT NULL,
            product_id  INTEGER NOT NULL,
            quantity    INTEGER NOT NULL,
            price       REAL    NOT NULL,
            total_price REAL    NOT NULL,
            type        TEXT    NOT NULL DEFAULT 'Sale',
            created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
        );
    """)
    try:
        con.execute(
            "INSERT INTO users (seller_name, username, password, role) VALUES (?,?,?,?)",
            ("Administrator", "admin", "admin123", "admin")
        )
        con.commit()
    except Exception:
        pass
    con.close()


# ── helpers ──────────────────────────────────────────────────

def is_seller(): return session.get("role") == "seller"
def is_admin():  return session.get("role") == "admin"


# ── auth ─────────────────────────────────────────────────────

@app.route("/")
def home():
    return redirect("/login")

@app.route("/login", methods=["GET", "POST"])
def login():
    if is_admin():  return redirect("/admin")
    if is_seller(): return redirect("/seller")

    if request.method == "POST":
        u = query(
            "SELECT * FROM users WHERE username=? AND password=?",
            (request.form["username"], request.form["password"]),
            one=True
        )
        if u:
            session["user_id"]     = u["id"]
            session["role"]        = u["role"]
            session["seller_name"] = u["seller_name"]
            session["username"]    = u["username"]
            return redirect("/admin" if u["role"] == "admin" else "/seller")
        return render_template("login.html", error="Wrong username or password")

    return render_template("login.html")

@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        try:
            run(
                "INSERT INTO users (seller_name, username, password, role) VALUES (?,?,?,'seller')",
                (request.form["seller_name"], request.form["username"], request.form["password"])
            )
            flash("Account created! Sign in.", "success")
            return redirect("/login")
        except Exception:
            return render_template("signup.html", error="Username already taken")
    return render_template("signup.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")


# ── admin ────────────────────────────────────────────────────

@app.route("/admin")
def admin():
    if not is_admin(): return redirect("/login")

    products = query("SELECT * FROM products ORDER BY quantity ASC")
    txns = query("""
        SELECT t.*, p.name AS product_name, u.seller_name
        FROM transactions t
        JOIN products p ON t.product_id = p.id
        JOIN users    u ON t.seller_id  = u.id
        ORDER BY t.created_at DESC LIMIT 100
    """)
    sellers = query("SELECT COUNT(*) AS c FROM users WHERE role='seller'", one=True)["c"]
    revenue = query("SELECT COALESCE(SUM(total_price),0) AS r FROM transactions", one=True)["r"]

    return render_template("admin.html",
        products=products, txns=txns, sellers=sellers, revenue=revenue)


# ── seller dashboard ─────────────────────────────────────────

@app.route("/seller")
def seller():
    if not is_seller(): return redirect("/login")

    sid      = session["user_id"]
    products = [dict(p) for p in query(
        "SELECT * FROM products WHERE seller_id=? ORDER BY name", (sid,)
    )]

    for p in products:
        rows      = query("""
            SELECT c.name FROM categories c
            JOIN product_categories pc ON c.id = pc.category_id
            WHERE pc.product_id = ?
        """, (p["id"],))
        p["cats"] = [r["name"] for r in rows]

    cats     = query("SELECT * FROM categories WHERE seller_id=? ORDER BY name", (sid,))
    tx_count = query("SELECT COUNT(*) AS c FROM transactions WHERE seller_id=?",
                     (sid,), one=True)["c"]
    revenue  = query("SELECT COALESCE(SUM(total_price),0) AS r FROM transactions WHERE seller_id=?",
                     (sid,), one=True)["r"]

    return render_template("seller.html",
        products=products, cats=cats, tx_count=tx_count, revenue=revenue)


# ── products CRUD ────────────────────────────────────────────

@app.route("/add_product", methods=["POST"])
def add_product():
    if not is_seller(): return redirect("/login")

    name    = request.form.get("name", "").strip()
    sku     = request.form.get("sku", "").strip() or None
    price   = request.form.get("price", "0")
    qty     = request.form.get("quantity", "0")
    expiry  = request.form.get("expiry") or None
    cat_ids = request.form.getlist("cat_ids")

    if not name:
        flash("Product name is required.", "error")
        return redirect("/seller")

    try:
        pid = run(
            "INSERT INTO products (name, sku, price, quantity, expiry, seller_id) VALUES (?,?,?,?,?,?)",
            (name, sku, price, qty, expiry, session["user_id"])
        )
        for cid in cat_ids:
            run("INSERT INTO product_categories (product_id, category_id) VALUES (?,?)", (pid, cid))
        flash("Product added!", "success")
    except Exception as e:
        flash(f"Could not add product: {e}", "error")

    return redirect("/seller")

@app.route("/edit_product/<int:pid>", methods=["POST"])
def edit_product(pid):
    if not is_seller(): return redirect("/login")

    name    = request.form.get("name", "").strip()
    sku     = request.form.get("sku", "").strip() or None
    price   = request.form.get("price", "0")
    qty     = request.form.get("quantity", "0")
    expiry  = request.form.get("expiry") or None
    cat_ids = request.form.getlist("cat_ids")

    try:
        run(
            "UPDATE products SET name=?, sku=?, price=?, quantity=?, expiry=? WHERE id=? AND seller_id=?",
            (name, sku, price, qty, expiry, pid, session["user_id"])
        )
        run("DELETE FROM product_categories WHERE product_id=?", (pid,))
        for cid in cat_ids:
            run("INSERT INTO product_categories (product_id, category_id) VALUES (?,?)", (pid, cid))
        flash("Product updated!", "success")
    except Exception as e:
        flash(f"Could not update: {e}", "error")

    return redirect("/seller")

@app.route("/delete_product/<int:pid>")
def delete_product(pid):
    if not is_seller(): return redirect("/login")

    count = query("SELECT COUNT(*) AS c FROM transactions WHERE product_id=?", (pid,), one=True)["c"]
    if count > 0:
        flash(f"Cannot delete — {count} transaction(s) linked to this product.", "error")
    else:
        run("DELETE FROM products WHERE id=? AND seller_id=?", (pid, session["user_id"]))
        run("DELETE FROM product_categories WHERE product_id=?", (pid,))
        flash("Product deleted.", "success")

    return redirect("/seller")


# ── categories CRUD ──────────────────────────────────────────

@app.route("/add_category", methods=["POST"])
def add_category():
    if not is_seller(): return redirect("/login")

    name  = request.form.get("name", "").strip()
    specs = request.form.get("specs", "").strip()

    if not name:
        flash("Category name is required.", "error")
        return redirect("/seller")

    run("INSERT INTO categories (name, specs, seller_id) VALUES (?,?,?)",
        (name, specs, session["user_id"]))
    flash("Category added!", "success")
    return redirect("/seller")

@app.route("/edit_category/<int:cid>", methods=["POST"])
def edit_category(cid):
    if not is_seller(): return redirect("/login")

    run("UPDATE categories SET name=?, specs=? WHERE id=? AND seller_id=?",
        (request.form.get("name", ""), request.form.get("specs", ""), cid, session["user_id"]))
    flash("Category updated!", "success")
    return redirect("/seller")

@app.route("/delete_category/<int:cid>")
def delete_category(cid):
    if not is_seller(): return redirect("/login")

    run("DELETE FROM categories WHERE id=? AND seller_id=?", (cid, session["user_id"]))
    flash("Category deleted.", "success")
    return redirect("/seller")


# ── transactions ─────────────────────────────────────────────

@app.route("/seller/transactions")
def transactions():
    if not is_seller(): return redirect("/login")

    sid  = session["user_id"]
    txns = query("""
        SELECT t.*, p.name AS product_name, p.sku, p.quantity AS stock_left
        FROM transactions t
        JOIN products p ON t.product_id = p.id
        WHERE t.seller_id = ?
        ORDER BY t.created_at DESC
    """, (sid,))
    products = query("SELECT * FROM products WHERE seller_id=? ORDER BY name", (sid,))
    revenue  = query("SELECT COALESCE(SUM(total_price),0) AS r FROM transactions WHERE seller_id=?",
                     (sid,), one=True)["r"]

    return render_template("transactions.html",
        txns=txns, products=products, revenue=revenue)

@app.route("/create_transaction", methods=["POST"])
def create_transaction():
    if not is_seller(): return redirect("/login")

    sid     = session["user_id"]
    pid     = request.form.get("product_id", "").strip()
    qty_str = request.form.get("quantity", "").strip()

    if not pid:
        flash("Please select a product.", "error")
        return redirect("/seller/transactions")

    if not qty_str.isdigit() or int(qty_str) <= 0:
        flash("Enter a valid quantity.", "error")
        return redirect("/seller/transactions")

    qty = int(qty_str)
    p   = query("SELECT * FROM products WHERE id=? AND seller_id=?", (pid, sid), one=True)

    if not p:
        flash("Product not found.", "error")
    elif qty > p["quantity"]:
        flash(f'Only {p["quantity"]} units of "{p["name"]}" left in stock!', "error")
    else:
        total = round(p["price"] * qty, 2)
        run("UPDATE products SET quantity = quantity - ? WHERE id=?", (qty, pid))
        run("""INSERT INTO transactions (seller_id, product_id, quantity, price, total_price, type)
               VALUES (?,?,?,?,?,'Sale')""",
            (sid, pid, qty, p["price"], total))
        flash(f'Sale recorded — {qty} × {p["name"]} = ₹{total:.2f}', "success")

    return redirect("/seller/transactions")


# ── init ──────────────────────────────────────────────────────

init_db()

if __name__ == "__main__":
    app.run(debug=True)