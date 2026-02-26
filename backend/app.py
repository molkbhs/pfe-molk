# -*- coding: utf-8 -*-
"""
Flask Backend — app.py complet et corrigé
KPI + Prévisions intégrés (sans jwt_required, utilise get_connection)
"""
from authlib.integrations.flask_client import OAuth
from flask import redirect, url_for
from datetime import datetime, timedelta
from pathlib import Path
import re
import shutil
import gc
import time
import json
import warnings
import importlib.util as _ilu

from flask import Flask, request, jsonify, send_from_directory, Response
from flask_cors import CORS
from werkzeug.security import check_password_hash, generate_password_hash
from db import get_connection

warnings.filterwarnings("ignore")

app = Flask(__name__)
CORS(app,
     resources={r"/api/*": {"origins": "*"}},
     supports_credentials=False,
     allow_headers=["Content-Type", "Authorization"],
     methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"])

# Cherche le dossier frontend de manière flexible
_base = Path(__file__).resolve().parent
_candidates = [
    _base.parent / "frontend",
    _base / "frontend",
    _base,
]
FRONTEND = next(
    (p for p in _candidates if p.exists() and (p / "dash.html").exists()),
    _base.parent / "frontend"
)

# =====================================================
# SESSION & OAUTH CONFIG
# =====================================================
app.secret_key = "super_secret_key"

oauth = OAuth(app)

google = oauth.register(
    name='google',
    client_id='714067888906-r7iqfn0v80s1el45cc678u5m3lvep6bv.apps.googleusercontent.com',
    client_secret='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',
    access_token_url='https://oauth2.googleapis.com/token',
    authorize_url='https://accounts.google.com/o/oauth2/auth',
    api_base_url='https://www.googleapis.com/oauth2/v2/',
    client_kwargs={'scope': 'openid email profile'},
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration'
)

# =====================================================
# DATABASE HELPERS
# =====================================================

def run_query(query, params=None, fetch_one=False):
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(query, params or ())
        result = cursor.fetchone() if fetch_one else cursor.fetchall()
        cursor.close()
        return result
    finally:
        if conn:
            conn.close()


def run_update(query, params=None):
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(query, params or ())
        conn.commit()
        last_id = cursor.lastrowid
        cursor.close()
        return last_id
    finally:
        if conn:
            conn.close()


# =====================================================
# AUTH HELPER — vérifie le token Bearer dans Authorization
# (remplace jwt_required sans dépendance flask_jwt_extended)
# =====================================================

def get_current_user():
    """
    Lit le user_id depuis le header Authorization: Bearer <user_id>
    ou depuis le body/query param 'user_id'.
    Retourne l'id (int) ou None.
    """
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        token = auth[7:].strip()
        # Si c'est un entier brut (stocké dans localStorage comme user.id)
        if token.isdigit():
            return int(token)
        # Sinon essayer de décoder JSON simple {"id": ...}
        try:
            payload = json.loads(token)
            return int(payload.get("id", 0)) or None
        except Exception:
            return None
    # Fallback : user_id dans le body
    try:
        data = request.get_json(silent=True) or {}
        uid  = data.get("user_id") or request.args.get("user_id")
        return int(uid) if uid else None
    except Exception:
        return None


# =====================================================
# KPI TABLES — création automatique au démarrage
# =====================================================

def create_kpi_tables():
    """Crée valeur_kpi et previsions si elles n'existent pas."""
    conn = None
    try:
        conn   = get_connection()
        cursor = conn.cursor()

        # ── VALEUR KPI ────────────────────────────────
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS valeur_kpi (
                id            INT AUTO_INCREMENT PRIMARY KEY,
                kpiNom        VARCHAR(255)   NOT NULL,
                periode       VARCHAR(50)    NOT NULL,
                valeur        FLOAT          NOT NULL,
                evolution     FLOAT          DEFAULT 0,
                departementId INT            DEFAULT NULL,
                source        VARCHAR(255)   DEFAULT 'etl',
                stat_type     VARCHAR(20)    DEFAULT 'sum',
                created_at    DATETIME       DEFAULT CURRENT_TIMESTAMP,
                updated_at    DATETIME       DEFAULT CURRENT_TIMESTAMP
                                             ON UPDATE CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """)

        # ── PREVISIONS ────────────────────────────────
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS previsions (
                id            INT AUTO_INCREMENT PRIMARY KEY,
                type          VARCHAR(100)   NOT NULL,
                dateDebut     DATE           NOT NULL,
                dateFin       DATE           NOT NULL,
                resultats     JSON           NOT NULL,
                departementId INT            DEFAULT NULL,
                created_by    INT            DEFAULT NULL,
                created_at    DATETIME       DEFAULT CURRENT_TIMESTAMP,
                updated_at    DATETIME       DEFAULT CURRENT_TIMESTAMP
                                             ON UPDATE CURRENT_TIMESTAMP,
                FOREIGN KEY (created_by)
                    REFERENCES users(id)
                    ON DELETE SET NULL
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """)

        conn.commit()
        cursor.close()
        print("[app] ✅ Tables valeur_kpi et previsions OK")
    except Exception as e:
        print(f"[app] ⚠️  create_kpi_tables : {e}")
    finally:
        if conn:
            conn.close()


# =====================================================
# GOOGLE OAUTH ROUTES
# =====================================================

def _oauth_redirect_html(user_data: dict, redirect_url: str) -> str:
    return f"""<!DOCTYPE html><html><head><title>Connexion réussie</title></head>
<body><div style="text-align:center;padding:50px;font-family:Arial,sans-serif;">
  <h2>Connexion réussie !</h2><p>Redirection...</p>
</div>
<script>
  localStorage.setItem('user', JSON.stringify({json.dumps(user_data)}));
  setTimeout(function(){{ window.location.href = '{redirect_url}'; }}, 800);
</script></body></html>"""


@app.route("/api/auth/google")
def google_login():
    redirect_uri = url_for('google_callback', _external=True)
    return google.authorize_redirect(redirect_uri)


@app.route("/api/auth/google/callback")
def google_callback():
    try:
        token     = google.authorize_access_token()
        user_info = token.get('userinfo') or {}
        if not user_info and token.get('access_token'):
            resp      = google.get('userinfo', token=token)
            user_info = resp.json() if resp else {}

        email     = (user_info.get("email") or "").strip().lower()
        firstname = user_info.get("given_name",  "")
        lastname  = user_info.get("family_name", "")

        if not email:
            raise ValueError("Google n'a pas fourni d'email")

        base_url  = request.url_root.rstrip('/')
        login_url = f"{base_url}/index.html"

        try:
            user = run_query(
                """SELECT id, firstname, lastname, email,
                          COALESCE(login_type, 'email') as login_type,
                          COALESCE(role, 'user') as role
                   FROM users WHERE LOWER(TRIM(email))=%s""",
                (email,), True
            )
        except Exception:
            user = run_query(
                "SELECT id, firstname, lastname, email FROM users WHERE LOWER(TRIM(email))=%s",
                (email,), True
            )
            if user:
                user["login_type"] = "email"
                user["role"]       = "user"

        if user and (user.get("login_type") or "email") != "google":
            return redirect(login_url)

        if user and (user.get("login_type") or "") == "google":
            username     = email.split('@')[0]
            role         = user.get("role", "user")
            user_data    = {
                "id": user["id"], "username": username,
                "firstname": user["firstname"], "lastname": user["lastname"],
                "email": user["email"], "role": role
            }
            redirect_url = f"{base_url}/admin.html" if role == "admin" else f"{base_url}/dash.html"
            return _oauth_redirect_html(user_data, redirect_url)

        try:
            user_id = run_update(
                """INSERT INTO users (firstname, lastname, email, password, login_type, role, created_at)
                   VALUES (%s, %s, %s, %s, 'google', 'user', NOW())""",
                (firstname, lastname, email, "")
            )
        except Exception:
            user_id = run_update(
                """INSERT INTO users (firstname, lastname, email, password, login_type, created_at)
                   VALUES (%s, %s, %s, %s, 'google', NOW())""",
                (firstname, lastname, email, "")
            )

        username  = email.split('@')[0]
        user_data = {
            "id": user_id, "username": username,
            "firstname": firstname, "lastname": lastname,
            "email": email, "role": "user"
        }
        return _oauth_redirect_html(user_data, f"{base_url}/dash.html")

    except Exception as e:
        base_url  = request.url_root.rstrip('/')
        login_url = f"{base_url}/index.html"
        return f"""<!DOCTYPE html><html><head><title>Erreur Auth</title></head>
<body><div style="text-align:center;padding:50px;font-family:Arial,sans-serif;">
  <h2 style="color:red;">Erreur d'authentification</h2><p>{str(e)}</p>
</div>
<script>setTimeout(function(){{ window.location.href='{login_url}'; }}, 3000);</script>
</body></html>"""


# =====================================================
# CONTACT
# =====================================================

@app.route("/api/contact", methods=["POST"])
def contact():
    try:
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "Données manquantes"}), 400
        name    = (data.get("name")    or "").strip()
        email   = (data.get("email")   or "").strip()
        subject = (data.get("subject") or "").strip()
        message = (data.get("message") or "").strip()
        if not name or not email or not subject or not message:
            return jsonify({"success": False, "error": "Tous les champs sont requis"}), 400
        return jsonify({"success": True, "message": "Message envoyé avec succès"}), 201
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# =====================================================
# AUTH
# =====================================================

@app.route("/api/register", methods=["POST"])
def register():
    data      = request.get_json()
    firstname = data.get("firstname", "").strip()
    lastname  = data.get("lastname",  "").strip()
    email     = data.get("email",     "").strip()
    password  = data.get("password",  "")

    if not firstname or not lastname or not email or not password:
        return jsonify({"error": "Tous les champs sont requis"}), 400
    if len(password) < 6:
        return jsonify({"error": "Mot de passe minimum 6 caractères"}), 400
    if run_query("SELECT id FROM users WHERE email=%s", (email,), True):
        return jsonify({"error": "Email déjà utilisé"}), 409

    hashed = generate_password_hash(password)
    try:
        user_id = run_update(
            """INSERT INTO users (firstname, lastname, email, password, role, created_at)
               VALUES (%s, %s, %s, %s, 'user', NOW())""",
            (firstname, lastname, email, hashed)
        )
    except Exception:
        user_id = run_update(
            """INSERT INTO users (firstname, lastname, email, password, created_at)
               VALUES (%s, %s, %s, %s, NOW())""",
            (firstname, lastname, email, hashed)
        )

    return jsonify({
        "message": "Inscription réussie",
        "user": {"id": user_id, "firstname": firstname, "lastname": lastname, "email": email}
    }), 201


@app.route("/api/login", methods=["POST"])
def login():
    data     = request.get_json()
    email    = data.get("email",    "").strip()
    password = data.get("password", "")

    if not email or not password:
        return jsonify({"error": "Email et mot de passe requis"}), 400

    try:
        user = run_query(
            """SELECT id, firstname, lastname, email, password,
                      COALESCE(role, 'user') as role
               FROM users WHERE email=%s""",
            (email,), True
        )
    except Exception:
        user = run_query(
            "SELECT id, firstname, lastname, email, password FROM users WHERE email=%s",
            (email,), True
        )
        if user:
            user["role"] = "user"

    if not user or not check_password_hash(user["password"], password):
        return jsonify({"error": "Email ou mot de passe incorrect"}), 401

    username = email.split('@')[0]
    role     = user.get("role", "user")

    return jsonify({
        "message": "Connexion réussie",
        "user": {
            "id":        user["id"],
            "username":  username,
            "firstname": user["firstname"],
            "lastname":  user["lastname"],
            "email":     user["email"],
            "role":      role
        }
    })


# =====================================================
# PROFILE
# =====================================================

@app.route("/api/profile/<int:user_id>", methods=["GET"])
def get_profile(user_id):
    try:
        user = run_query(
            """SELECT id, firstname, lastname, email, created_at,
                      COALESCE(role, 'user') as role,
                      COALESCE(login_type, 'email') as login_type
               FROM users WHERE id=%s""",
            (user_id,), True
        )
    except Exception:
        user = run_query(
            "SELECT id, firstname, lastname, email, created_at FROM users WHERE id=%s",
            (user_id,), True
        )
        if user:
            user["role"]       = "user"
            user["login_type"] = "email"

    if not user:
        return jsonify({"error": "Utilisateur non trouvé"}), 404
    if user["created_at"]:
        user["created_at"] = user["created_at"].strftime("%Y-%m-%d %H:%M")
    return jsonify(user)


@app.route("/api/profile/<int:user_id>", methods=["PUT"])
def update_profile(user_id):
    data      = request.get_json() or {}
    firstname = (data.get("firstname") or "").strip()
    lastname  = (data.get("lastname")  or "").strip()
    email     = (data.get("email")     or "").strip().lower()

    if not firstname or not lastname or not email:
        return jsonify({"error": "Tous les champs sont requis"}), 400

    existing_user = run_query("SELECT id FROM users WHERE id=%s", (user_id,), True)
    if not existing_user:
        return jsonify({"error": "Utilisateur non trouvé"}), 404

    email_owner = run_query("SELECT id FROM users WHERE LOWER(TRIM(email))=%s", (email,), True)
    if email_owner and int(email_owner["id"]) != int(user_id):
        return jsonify({"error": "Cet email est déjà utilisé"}), 409

    try:
        run_update(
            "UPDATE users SET firstname=%s, lastname=%s, email=%s WHERE id=%s",
            (firstname, lastname, email, user_id)
        )
        updated_user = run_query(
            """SELECT id, firstname, lastname, email, created_at,
                      COALESCE(role, 'user') as role,
                      COALESCE(login_type, 'email') as login_type
               FROM users WHERE id=%s""",
            (user_id,), True
        )
        if updated_user and updated_user.get("created_at"):
            updated_user["created_at"] = updated_user["created_at"].strftime("%Y-%m-%d %H:%M")
        return jsonify({"message": "Profil mis à jour", "user": updated_user}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/profile/<int:user_id>", methods=["DELETE"])
def delete_account(user_id):
    try:
        run_update("DELETE FROM users WHERE id=%s", (user_id,))
        return jsonify({"message": "Compte supprimé"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# =====================================================
# DASHBOARD STATS
# =====================================================

@app.route("/api/stats", methods=["GET"])
def stats():
    try:
        total       = run_query("SELECT COUNT(*) as n FROM users", fetch_one=True)["n"]
        today       = datetime.now().strftime("%Y-%m-%d")
        today_count = run_query("SELECT COUNT(*) as n FROM users WHERE DATE(created_at)=%s", (today,), True)["n"]
        week_ago    = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        week_count  = run_query("SELECT COUNT(*) as n FROM users WHERE created_at >= %s", (week_ago,), True)["n"]
        try:
            google_users = run_query("SELECT COUNT(*) as n FROM users WHERE login_type='google'", fetch_one=True)["n"]
            email_users  = run_query("SELECT COUNT(*) as n FROM users WHERE login_type='email' OR login_type IS NULL OR login_type=''", fetch_one=True)["n"]
            admin_count  = run_query("SELECT COUNT(*) as n FROM users WHERE role='admin'", fetch_one=True)["n"]
        except Exception:
            google_users = 0
            email_users  = total
            admin_count  = 0
        return jsonify({
            "total_users": total, "new_today": today_count, "active_week": week_count,
            "google_users": google_users, "email_users": email_users, "admin_count": admin_count
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# =====================================================
# USERS LIST
# =====================================================

@app.route("/api/users", methods=["GET"])
def users_list():
    try:
        users = run_query(
            """SELECT id, firstname, lastname, email, created_at,
                      COALESCE(login_type, 'email') as login_type,
                      COALESCE(role, 'user') as role
               FROM users ORDER BY id DESC"""
        )
    except Exception:
        users = run_query("SELECT id, firstname, lastname, email, created_at FROM users ORDER BY id DESC")
        for u in users:
            u["login_type"] = "email"
            u["role"]       = "user"
    for u in users:
        if u["created_at"]:
            u["created_at"] = u["created_at"].strftime("%Y-%m-%d %H:%M")
    return jsonify(users)


# =====================================================
# ADMIN
# =====================================================

@app.route("/api/admin/users/<int:target_id>/role", methods=["PUT"])
def update_user_role(target_id):
    data     = request.get_json() or {}
    new_role = (data.get("role") or "").strip()
    if new_role not in ("user", "admin"):
        return jsonify({"error": "Rôle invalide - valeurs acceptées : 'user' ou 'admin'"}), 400
    target = run_query("SELECT id, firstname, lastname FROM users WHERE id=%s", (target_id,), True)
    if not target:
        return jsonify({"error": "Utilisateur non trouvé"}), 404
    try:
        run_update("UPDATE users SET role=%s WHERE id=%s", (new_role, target_id))
        return jsonify({
            "message": f"Rôle de {target['firstname']} {target['lastname']} → {new_role}",
            "user_id": target_id, "role": new_role
        })
    except Exception as e:
        err = str(e)
        if "Unknown column 'role'" in err:
            return jsonify({"error": "Migration SQL non effectuée"}), 500
        return jsonify({"error": err}), 500


@app.route("/api/admin/users", methods=["POST"])
def create_user_admin():
    data      = request.get_json() or {}
    firstname = (data.get("firstname") or "").strip()
    lastname  = (data.get("lastname")  or "").strip()
    email     = (data.get("email")     or "").strip().lower()
    password  = data.get("password")   or ""
    role      = (data.get("role")      or "user").strip().lower()

    if not firstname or not lastname or not email or not password:
        return jsonify({"error": "Tous les champs sont requis"}), 400
    if len(password) < 6:
        return jsonify({"error": "Mot de passe minimum 6 caractères"}), 400
    if role not in ("user", "admin"):
        return jsonify({"error": "Rôle invalide"}), 400
    if run_query("SELECT id FROM users WHERE LOWER(TRIM(email))=%s", (email,), True):
        return jsonify({"error": "Email déjà utilisé"}), 409

    hashed = generate_password_hash(password)
    try:
        user_id = run_update(
            """INSERT INTO users (firstname, lastname, email, password, role, login_type, created_at)
               VALUES (%s, %s, %s, %s, %s, 'email', NOW())""",
            (firstname, lastname, email, hashed, role)
        )
    except Exception:
        user_id = run_update(
            """INSERT INTO users (firstname, lastname, email, password, created_at)
               VALUES (%s, %s, %s, %s, NOW())""",
            (firstname, lastname, email, hashed)
        )
        role = "user"

    return jsonify({
        "message": "Utilisateur créé",
        "user": {"id": user_id, "firstname": firstname, "lastname": lastname,
                 "email": email, "role": role, "login_type": "email"}
    }), 201


@app.route("/api/users/export", methods=["GET"])
def export_users():
    try:
        users = run_query(
            """SELECT id, firstname, lastname, email, created_at,
                      COALESCE(role, 'user') as role,
                      COALESCE(login_type, 'email') as login_type
               FROM users ORDER BY id"""
        )
    except Exception:
        users = run_query("SELECT id, firstname, lastname, email, created_at FROM users ORDER BY id")
        for u in users:
            u["role"] = "user"; u["login_type"] = "email"
    lines = ["id,firstname,lastname,email,role,login_type,created_at"]
    for u in users:
        created = u["created_at"]
        if hasattr(created, "strftime"):
            created = created.strftime("%Y-%m-%d %H:%M")
        lines.append(f"{u['id']},{u['firstname']},{u['lastname']},{u['email']},"
                     f"{u.get('role','user')},{u.get('login_type','email')},{created}")
    return Response("\n".join(lines), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment;filename=users.csv"})


# =====================================================
# ETL GÉNÉRIQUE
# =====================================================

UPLOAD_FOLDER = Path(__file__).resolve().parent / "uploads_etl"
UPLOAD_FOLDER.mkdir(exist_ok=True)

ETL_ENGINE_PATH = Path(__file__).resolve().parent / "etl_generic.py"
_spec    = _ilu.spec_from_file_location("etl_generic", str(ETL_ENGINE_PATH))
_etl_mod = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_etl_mod)
run_generic_etl = _etl_mod.run_generic_etl


def _safe_copy(src: Path, dest: Path):
    gc.collect()
    time.sleep(0.3)
    try:
        shutil.copy2(str(src), str(dest))
    except PermissionError:
        with open(str(src), "rb") as f_in:
            data = f_in.read()
        with open(str(dest), "wb") as f_out:
            f_out.write(data)


@app.route("/api/etl/ping", methods=["GET"])
def etl_ping():
    return jsonify({"status": "ok", "message": "Backend Flask opérationnel"})


@app.route("/api/etl/upload", methods=["POST"])
def etl_upload():
    if "file" not in request.files:
        return jsonify({"error": "Aucun fichier reçu"}), 400
    file  = request.files["file"]
    fname = file.filename or ""
    if not any(fname.lower().endswith(e) for e in (".csv", ".xlsx", ".xls")):
        return jsonify({"error": "Format accepté : CSV, XLSX, XLS"}), 400
    safe_fname = re.sub(r"[^\w._-]", "_", fname)
    save_path  = UPLOAD_FOLDER / safe_fname
    file.save(str(save_path))
    try:
        result = run_generic_etl(str(save_path))
    except Exception as e:
        import traceback
        return jsonify({"error": f"ETL échoué : {str(e)}", "detail": traceback.format_exc()}), 500
    if not result.get("success"):
        return jsonify({"error": result.get("log", ["Erreur inconnue"])[-1]}), 500
    cleaned_src  = save_path.parent / "donnees_nettoyees.csv"
    cleaned_dest = UPLOAD_FOLDER / "donnees_nettoyees.csv"
    if cleaned_src.exists():
        _safe_copy(cleaned_src, cleaned_dest)
    return jsonify({
        "success":       True,
        "stats":         result["stats"],
        "preview":       result["preview"],
        "total_preview": result["total_preview"],
        "schema":        result.get("schema", {}),
        "db_result": {
            "db_name":       result["db_result"]["db_name"],
            "table_name":    result["db_result"]["table_name"],
            "rows_inserted": result["db_result"]["rows_inserted"],
            "marts_detail":  result["db_result"].get("marts_detail", {}),
        }
    })


@app.route("/api/etl/schema", methods=["GET"])
def etl_schema():
    try:
        from sqlalchemy import create_engine, inspect as sa_inspect
        engine = create_engine("mysql+pymysql://root:@127.0.0.1:3306/etl_data", echo=False)
        insp   = sa_inspect(engine)
        tables = insp.get_table_names()
        result = {t: [{"name": c["name"], "type": str(c["type"])} for c in insp.get_columns(t)] for t in tables}
        return jsonify({"success": True, "tables": result})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/etl/download", methods=["GET"])
def etl_download():
    cleaned_path = UPLOAD_FOLDER / "donnees_nettoyees.csv"
    if not cleaned_path.exists():
        return jsonify({"error": "Aucun fichier nettoyé disponible"}), 404
    return send_from_directory(
        str(UPLOAD_FOLDER), "donnees_nettoyees.csv",
        as_attachment=True, download_name="donnees_nettoyees.csv", mimetype="text/csv"
    )


# =====================================================
# KPI — valeur_kpi
# =====================================================

@app.route("/api/kpi/save", methods=["POST"])
def save_kpis():
    """
    POST /api/kpi/save
    Body JSON : { "kpis": [...], "source": "fichier.csv", "replace": true }
    """
    data       = request.get_json(force=True) or {}
    kpis       = data.get("kpis", [])
    source     = data.get("source", "etl")
    do_replace = data.get("replace", False)

    if not kpis:
        return jsonify({"success": False, "error": "Aucun KPI fourni"}), 400

    conn = None
    try:
        conn     = get_connection()
        cursor   = conn.cursor()
        inserted = replaced = 0

        for kpi in kpis:
            nom       = str(kpi.get("kpiNom",     "")).strip()
            periode   = str(kpi.get("periode",    "global")).strip()
            valeur    = float(kpi.get("valeur",    0))
            evolution = float(kpi.get("evolution", 0))
            dept_id   = kpi.get("departementId",  None)
            stat_type = str(kpi.get("stat_type",  "sum")).strip()

            if not nom:
                continue

            if do_replace:
                cursor.execute(
                    "DELETE FROM valeur_kpi WHERE kpiNom=%s AND periode=%s",
                    (nom, periode)
                )
                replaced += cursor.rowcount

            cursor.execute(
                """INSERT INTO valeur_kpi
                       (kpiNom, periode, valeur, evolution, departementId, source, stat_type)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                (nom, periode, valeur, evolution, dept_id, source, stat_type)
            )
            inserted += 1

        conn.commit()
        cursor.close()
        return jsonify({
            "success":  True,
            "inserted": inserted,
            "replaced": replaced,
            "message":  f"{inserted} KPI(s) sauvegardés"
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        if conn:
            conn.close()


@app.route("/api/kpi", methods=["GET"])
def get_kpis():
    """GET /api/kpi?kpiNom=...&periode=...&limit=100"""
    kpi_nom = request.args.get("kpiNom")
    periode = request.args.get("periode")
    limit   = int(request.args.get("limit", 100))

    conn = None
    try:
        conn   = get_connection()
        cursor = conn.cursor(dictionary=True)
        q      = "SELECT * FROM valeur_kpi WHERE 1=1"
        p      = []
        if kpi_nom: q += " AND kpiNom=%s";  p.append(kpi_nom)
        if periode: q += " AND periode=%s"; p.append(periode)
        q += " ORDER BY created_at DESC LIMIT %s"; p.append(limit)
        cursor.execute(q, p)
        kpis = cursor.fetchall()
        cursor.close()
        for k in kpis:
            for f in ["created_at", "updated_at"]:
                if k.get(f): k[f] = k[f].isoformat()
        return jsonify({"success": True, "kpis": kpis, "total": len(kpis)})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        if conn:
            conn.close()


# =====================================================
# PRÉVISIONS
# =====================================================

@app.route("/api/previsions", methods=["POST"])
def create_prevision():
    """
    POST /api/previsions
    Body : { "type", "dateDebut", "dateFin", "resultats", "departementId" }
    """
    data       = request.get_json(force=True) or {}
    user_id    = get_current_user()
    type_prev  = (data.get("type")          or "").strip()
    date_debut = data.get("dateDebut")
    date_fin   = data.get("dateFin")
    resultats  = data.get("resultats",       {})
    dept_id    = data.get("departementId",   None)

    if not type_prev or not date_debut or not date_fin:
        return jsonify({"success": False, "error": "type, dateDebut et dateFin sont requis"}), 400

    conn = None
    try:
        conn   = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """INSERT INTO previsions (type, dateDebut, dateFin, resultats, departementId, created_by)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (type_prev, date_debut, date_fin,
             json.dumps(resultats, ensure_ascii=False), dept_id, user_id)
        )
        conn.commit()
        new_id = cursor.lastrowid
        cursor.close()
        return jsonify({"success": True, "id": new_id, "message": f"Prévision #{new_id} créée"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        if conn:
            conn.close()


@app.route("/api/previsions", methods=["GET"])
def get_previsions():
    """GET /api/previsions?departementId=1&type=budget&limit=50"""
    dept_id = request.args.get("departementId")
    type_p  = request.args.get("type")
    limit   = int(request.args.get("limit", 50))

    conn = None
    try:
        conn   = get_connection()
        cursor = conn.cursor(dictionary=True)
        q      = "SELECT * FROM previsions WHERE 1=1"
        p      = []
        if dept_id: q += " AND departementId=%s"; p.append(dept_id)
        if type_p:  q += " AND type=%s";          p.append(type_p)
        q += " ORDER BY created_at DESC LIMIT %s"; p.append(limit)
        cursor.execute(q, p)
        rows = cursor.fetchall()
        cursor.close()
        for r in rows:
            for f in ["created_at", "updated_at"]:
                if r.get(f): r[f] = r[f].isoformat()
            for f in ["dateDebut", "dateFin"]:
                if r.get(f): r[f] = str(r[f])
            if isinstance(r.get("resultats"), str):
                try:    r["resultats"] = json.loads(r["resultats"])
                except: pass
        return jsonify({"success": True, "previsions": rows, "total": len(rows)})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        if conn:
            conn.close()


@app.route("/api/previsions/<int:prev_id>", methods=["DELETE"])
def delete_prevision(prev_id):
    conn = None
    try:
        conn   = get_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM previsions WHERE id=%s", (prev_id,))
        conn.commit()
        deleted = cursor.rowcount
        cursor.close()
        if deleted:
            return jsonify({"success": True, "message": f"Prévision #{prev_id} supprimée"})
        return jsonify({"success": False, "error": "Prévision non trouvée"}), 404
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        if conn:
            conn.close()


@app.route("/api/init-tables", methods=["POST"])
def init_tables():
    """Force la création des tables KPI (utile pour debug)."""
    try:
        create_kpi_tables()
        return jsonify({"success": True, "message": "Tables créées ou déjà existantes"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# =====================================================
# STATIC FILES
# =====================================================

@app.route("/")
def home():
    return send_from_directory(FRONTEND, "index.html")


@app.route("/<path:filename>")
def serve_static(filename):
    return send_from_directory(FRONTEND, filename)


# =====================================================
# RUN
# =====================================================

if __name__ == "__main__":
    # Créer les tables KPI au démarrage
    create_kpi_tables()
    app.run(host="0.0.0.0", port=5000, debug=True)