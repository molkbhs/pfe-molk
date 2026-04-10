# -*- coding: utf-8 -*-
"""
Flask Backend — app.py
- ETL stable sans 500 après succès
- retour complet before_rows / after_rows
- historique des imports complet :
  id, user_id, nom_fichier, date_import, nb_lignes, nb_erreurs,
  statut, departement, importe_par, details, data
- enregistrement succès / échec
- historique groupé par utilisateur
- compression gzip+base64 pour stocker de gros payloads dans details/data
- JSON safe (NaN, Timestamp, numpy types)
"""

from authlib.integrations.flask_client import OAuth
from flask import Flask, request, jsonify, send_from_directory, Response, redirect, url_for
from flask_cors import CORS
from werkzeug.security import check_password_hash, generate_password_hash
from datetime import datetime, timedelta
from pathlib import Path
import re
import shutil
import gc
import time
import json
import warnings
import importlib.util as _ilu
import math
import traceback
import gzip
import base64

import pandas as pd
import numpy as np

from db import get_connection

warnings.filterwarnings("ignore")

app = Flask(__name__)
CORS(
    app,
    resources={r"/api/*": {"origins": "*"}},
    supports_credentials=False,
    allow_headers=["Content-Type", "Authorization"],
    methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
)

# -------------------------------------------------------------------
# FRONTEND
# -------------------------------------------------------------------
_base = Path(__file__).resolve().parent
_candidates = [
    _base.parent / "frontend",
    _base / "frontend",
    _base,
]
FRONTEND = next(
    (p for p in _candidates if p.exists() and (p / "dash.html").exists()),
    _base.parent / "frontend",
)

# -------------------------------------------------------------------
# SESSION & OAUTH
# -------------------------------------------------------------------
app.secret_key = "super_secret_key"

oauth = OAuth(app)

google = oauth.register(
    name="google",
    client_id="714067888906-r7iqfn0v80s1el45cc678u5m3lvep6bv.apps.googleusercontent.com",
    client_secret="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",
    access_token_url="https://oauth2.googleapis.com/token",
    authorize_url="https://accounts.google.com/o/oauth2/auth",
    api_base_url="https://www.googleapis.com/oauth2/v2/",
    client_kwargs={"scope": "openid email profile"},
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
)

# -------------------------------------------------------------------
# DATABASE HELPERS
# -------------------------------------------------------------------
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


def make_json_safe(value):
    if isinstance(value, dict):
        return {str(k): make_json_safe(v) for k, v in value.items()}

    if isinstance(value, (list, tuple)):
        return [make_json_safe(v) for v in value]

    try:
        if pd.isna(value):
            return None
    except Exception:
        pass

    if isinstance(value, np.integer):
        return int(value)

    if isinstance(value, np.floating):
        f = float(value)
        if math.isnan(f) or math.isinf(f):
            return None
        return f

    if isinstance(value, np.bool_):
        return bool(value)

    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return value

    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except Exception:
            pass

    if hasattr(value, "item"):
        try:
            return make_json_safe(value.item())
        except Exception:
            return str(value)

    if isinstance(value, (str, int, bool)) or value is None:
        return value

    return str(value)


def compress_json_payload(payload):
    raw = json.dumps(make_json_safe(payload), ensure_ascii=False).encode("utf-8")
    compressed = gzip.compress(raw, compresslevel=9)
    return base64.b64encode(compressed).decode("ascii")


def decompress_json_payload(payload_text):
    if not payload_text:
        return None
    try:
        compressed = base64.b64decode(payload_text.encode("ascii"))
        raw = gzip.decompress(compressed).decode("utf-8")
        return json.loads(raw)
    except Exception:
        return None
def append_large_text_in_chunks(conn, import_id, field_name, text_value, chunk_size=700000):
    """
    Écrit un texte volumineux dans un champ LONGTEXT par petits morceaux
    pour éviter l'erreur max_allowed_packet.
    """
    if field_name not in ("details", "data"):
        raise ValueError("Champ invalide pour écriture chunkée")

    if not text_value:
        return

    cursor = conn.cursor()
    try:
        for i in range(0, len(text_value), chunk_size):
            chunk = text_value[i:i + chunk_size]
            cursor.execute(
                f"""
                UPDATE historique_imports
                SET {field_name} = CONCAT(COALESCE({field_name}, ''), %s)
                WHERE id = %s
                """,
                (chunk, import_id)
            )
        conn.commit()
    finally:
        cursor.close()

def read_table_rows(file_path: Path):
    ext = file_path.suffix.lower()
    if ext == ".csv":
        for enc in ["utf-8", "utf-8-sig", "latin-1", "cp1252"]:
            try:
                return pd.read_csv(str(file_path), encoding=enc, low_memory=False)
            except UnicodeDecodeError:
                continue
        raise ValueError("Impossible de lire le CSV")
    if ext in [".xlsx", ".xls"]:
        return pd.read_excel(str(file_path))
    raise ValueError("Format non supporté")


# -------------------------------------------------------------------
# AUTH HELPER
# -------------------------------------------------------------------
def get_current_user():
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        token = auth[7:].strip()
        if token.isdigit():
            return int(token)
        try:
            payload = json.loads(token)
            return int(payload.get("id", 0)) or None
        except Exception:
            return None

    try:
        data = request.get_json(silent=True) or {}
        uid = data.get("user_id") or request.args.get("user_id")
        return int(uid) if uid else None
    except Exception:
        return None


def get_user_display_name(user_id):
    try:
        user = run_query(
            "SELECT COALESCE(firstname, email) AS display_name FROM users WHERE id=%s",
            (user_id,),
            fetch_one=True
        )
        return user["display_name"] if user else None
    except Exception:
        return None


def save_import_history(user_id, filename, stats=None, log=None, cleaned_data=None, success=True):
    stats = stats or {}
    log = log or []
    cleaned_data = cleaned_data or []

    nb_lignes = int(stats.get("lignes", 0) or 0)
    nb_erreurs = int(stats.get("nb_erreurs", 0) or 0)
    departement = stats.get("departement")
    importe_par = get_user_display_name(user_id) if user_id else None
    statut = "succes" if success else "echec"

    details_payload = {
        "compressed": True,
        "format": "gzip+base64+json"
    }

    data_payload = {
        "compressed": True,
        "format": "gzip+base64+json",
        "total_rows": nb_lignes
    }

    details_compressed = compress_json_payload(log)
    data_compressed = compress_json_payload(cleaned_data)

    details_json = json.dumps(
        {**details_payload, "content": details_compressed},
        ensure_ascii=False
    )
    data_json = json.dumps(
        {**data_payload, "content": data_compressed},
        ensure_ascii=False
    )

    conn = None
    try:
        conn = get_connection()
        try:
            conn.ping(reconnect=True, attempts=3, delay=2)
        except Exception:
            pass

        cursor = conn.cursor()

        cursor.execute(
            """
            INSERT INTO historique_imports
            (user_id, nom_fichier, date_import, nb_lignes, nb_erreurs, statut, departement, importe_par, details, data)
            VALUES (%s, %s, NOW(), %s, %s, %s, %s, %s, '', '')
            """,
            (
                user_id,
                filename,
                nb_lignes,
                nb_erreurs,
                statut,
                departement,
                importe_par,
            )
        )

        import_id = cursor.lastrowid
        conn.commit()
        cursor.close()

        append_large_text_in_chunks(conn, import_id, "details", details_json, chunk_size=700000)
        append_large_text_in_chunks(conn, import_id, "data", data_json, chunk_size=700000)

        print(f"[HISTO] Insert OK | import_id={import_id} | file={filename} | rows={nb_lignes} | status={statut}")

    except Exception as e:
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
        print(f"[HISTO] Insert FAILED: {e}")
        raise
    finally:
        if conn:
            conn.close()
# -------------------------------------------------------------------
# KPI / PREVISIONS TABLES
# -------------------------------------------------------------------
def create_kpi_tables():
    import mysql.connector
    from config import DB_CONFIG

    conf_no_db = DB_CONFIG.copy()
    db_name = conf_no_db.pop("database", "pfe_bd")
    conn = None

    try:
        conn = mysql.connector.connect(**conf_no_db)
        cursor = conn.cursor()
        cursor.execute(f"CREATE DATABASE IF NOT EXISTS {db_name}")
        cursor.execute(f"USE {db_name}")

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id           INT AUTO_INCREMENT PRIMARY KEY,
                firstname    VARCHAR(100),
                lastname     VARCHAR(100),
                email        VARCHAR(150) UNIQUE NOT NULL,
                password     VARCHAR(255),
                role         VARCHAR(50) DEFAULT 'user',
                login_type   VARCHAR(50) DEFAULT 'email',
                created_at   DATETIME DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS valeur_kpi (
                id            INT AUTO_INCREMENT PRIMARY KEY,
                kpiNom        VARCHAR(255) NOT NULL,
                periode       VARCHAR(50) NOT NULL,
                valeur        FLOAT NOT NULL,
                evolution     FLOAT DEFAULT 0,
                departementId INT DEFAULT NULL,
                source        VARCHAR(255) DEFAULT 'etl',
                stat_type     VARCHAR(20) DEFAULT 'sum',
                created_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at    DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS previsions (
                id            INT AUTO_INCREMENT PRIMARY KEY,
                type          VARCHAR(100) NOT NULL,
                dateDebut     DATE NOT NULL,
                dateFin       DATE NOT NULL,
                resultats     JSON NOT NULL,
                departementId INT DEFAULT NULL,
                created_by    INT DEFAULT NULL,
                created_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at    DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE SET NULL
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS historique_imports (
                id            INT AUTO_INCREMENT PRIMARY KEY,
                user_id       INT DEFAULT NULL,
                nom_fichier   VARCHAR(255) NOT NULL,
                date_import   DATETIME DEFAULT CURRENT_TIMESTAMP,
                nb_lignes     INT DEFAULT 0,
                nb_erreurs    INT DEFAULT 0,
                statut        ENUM('succes','partiel','echec') NOT NULL DEFAULT 'succes',
                departement   VARCHAR(255) DEFAULT NULL,
                importe_par   VARCHAR(255) DEFAULT NULL,
                details       LONGTEXT DEFAULT NULL,
                data          LONGTEXT DEFAULT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL ON UPDATE CASCADE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """
        )

        conn.commit()
        cursor.close()
        print("[app] ✅ Tables KPI / historique OK")
    except Exception as e:
        print(f"[app] ⚠ create_kpi_tables : {e}")
    finally:
        if conn:
            conn.close()


# -------------------------------------------------------------------
# CHATBOT
# -------------------------------------------------------------------
@app.route("/api/chatbot/ask", methods=["POST"])
def chatbot_ask():
    try:
        data = request.get_json() or {}
        msg = (data.get("message") or "").strip().lower()

        if not msg:
            return jsonify({"answer": "Posez-moi une question sur vos données !"})

        if any(w in msg for w in ["revenu", "argent", "gagné", "ca"]):
            res = run_query(
                "SELECT SUM(valeur) as total FROM valeur_kpi WHERE kpiNom LIKE '%revenu%'",
                fetch_one=True,
            )
            total = res["total"] if res and res["total"] else 0
            return jsonify({"answer": f"Le revenu total détecté est de {total:,.2f} €."})

        if any(w in msg for w in ["dépense", "depense", "coût", "cout", "perdu"]):
            res = run_query(
                "SELECT SUM(valeur) as total FROM valeur_kpi WHERE kpiNom LIKE '%depense%'",
                fetch_one=True,
            )
            total = res["total"] if res and res["total"] else 0
            return jsonify({"answer": f"Les dépenses s'élèvent à {total:,.2f} €."})

        if any(w in msg for w in ["utilisateur", "client", "membre"]):
            res = run_query("SELECT COUNT(*) as n FROM users", fetch_one=True)
            return jsonify({"answer": f"Il y a actuellement {res['n']} utilisateurs enregistrés."})

        return jsonify({"answer": "Je peux vous aider sur les revenus, dépenses ou utilisateurs."})
    except Exception as e:
        return jsonify({"answer": f"Désolé, j'ai rencontré une erreur : {str(e)}"}), 500


# -------------------------------------------------------------------
# HISTORIQUE IMPORTS
# -------------------------------------------------------------------
# -------------------------------------------------------------------
# HISTORIQUE IMPORTS
# -------------------------------------------------------------------
@app.route("/api/etl/history", methods=["GET"])
def get_etl_history():
    try:
        current_user_id = get_current_user()
        if not current_user_id:
            return jsonify({"error": "Auth requis"}), 401

        current_user = run_query(
            "SELECT id, firstname, lastname, email, COALESCE(role, 'user') as role FROM users WHERE id=%s",
            (current_user_id,),
            fetch_one=True,
        )

        if not current_user:
            return jsonify({"error": "Utilisateur introuvable"}), 404

        if current_user.get("role") == "admin":
            rows = run_query(
                """
                SELECT 
                    h.id,
                    h.user_id,
                    h.nom_fichier,
                    h.date_import,
                    h.nb_lignes,
                    h.nb_erreurs,
                    h.statut,
                    h.departement,
                    h.importe_par,
                    h.details,
                    h.data,
                    u.firstname,
                    u.lastname,
                    u.email
                FROM historique_imports h
                LEFT JOIN users u ON u.id = h.user_id
                ORDER BY COALESCE(u.firstname, u.email) ASC, h.date_import DESC
                """
            )
        else:
            rows = run_query(
                """
                SELECT 
                    h.id,
                    h.user_id,
                    h.nom_fichier,
                    h.date_import,
                    h.nb_lignes,
                    h.nb_erreurs,
                    h.statut,
                    h.departement,
                    h.importe_par,
                    h.details,
                    h.data,
                    u.firstname,
                    u.lastname,
                    u.email
                FROM historique_imports h
                LEFT JOIN users u ON u.id = h.user_id
                WHERE h.user_id=%s
                ORDER BY h.date_import DESC
                """,
                (current_user_id,),
            )

        grouped = {}

        for row in rows:
            user_name = (
                f"{row.get('firstname') or ''} {row.get('lastname') or ''}".strip()
                or row.get("email")
                or row.get("importe_par")
                or f"User {row.get('user_id')}"
            )

            if user_name not in grouped:
                grouped[user_name] = []

            details_value = []
            data_value = []

            if row.get("details"):
                try:
                    details_raw = json.loads(row["details"])
                    if isinstance(details_raw, dict) and details_raw.get("compressed"):
                        details_value = decompress_json_payload(details_raw.get("content")) or []
                    else:
                        details_value = details_raw
                except Exception:
                    details_value = []

            if row.get("data"):
                try:
                    data_raw = json.loads(row["data"])
                    if isinstance(data_raw, dict) and data_raw.get("compressed"):
                        data_value = {
                            "total_rows": data_raw.get("total_rows", 0),
                            "rows": decompress_json_payload(data_raw.get("content")) or []
                        }
                    else:
                        data_value = data_raw
                except Exception:
                    data_value = []

            grouped[user_name].append({
                "id": row.get("id"),
                "user_id": row.get("user_id"),
                "nom_fichier": row.get("nom_fichier"),
                "date_import": row.get("date_import").isoformat() if row.get("date_import") else None,
                "date_import_label": row.get("date_import").strftime("%Y-%m-%d %H:%M") if row.get("date_import") else "",
                "nb_lignes": row.get("nb_lignes", 0),
                "nb_erreurs": row.get("nb_erreurs", 0),
                "statut": row.get("statut", "succes"),
                "departement": row.get("departement"),
                "importe_par": row.get("importe_par"),
                "details": details_value,
                "data": data_value,
            })

        groups = [{"user_name": k, "items": v} for k, v in grouped.items()]
        return jsonify({"success": True, "groups": groups})

    except Exception as e:
        return jsonify({"error": str(e), "detail": traceback.format_exc()}), 500

@app.route("/api/etl/history/<int:import_id>", methods=["DELETE"])
def delete_etl_history(import_id):
    try:
        user_id = get_current_user()
        if not user_id:
            return jsonify({"error": "Auth requis"}), 401

        run_update("DELETE FROM historique_imports WHERE id=%s AND user_id=%s", (import_id, user_id))
        return jsonify({"success": True, "message": "Import supprimé de l'historique"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# -------------------------------------------------------------------
# PROFILE
# -------------------------------------------------------------------
@app.route("/api/profile", methods=["GET"])
def get_my_profile():
    user_id = get_current_user()
    if not user_id:
        return jsonify({"error": "Non autorisé"}), 401

    user = run_query("SELECT id, firstname, email, role FROM users WHERE id=%s", (user_id,), fetch_one=True)
    if not user:
        return jsonify({"error": "User non trouvé"}), 404

    return jsonify(
        {
            "status": "success",
            "data": {
                "id": user["id"],
                "username": user["firstname"],
                "email": user["email"],
                "role": user["role"],
            },
        }
    )


# -------------------------------------------------------------------
# GOOGLE OAUTH
# -------------------------------------------------------------------
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
    redirect_uri = url_for("google_callback", _external=True)
    return google.authorize_redirect(redirect_uri)


@app.route("/api/auth/google/callback")
def google_callback():
    try:
        token = google.authorize_access_token()
        user_info = token.get("userinfo") or {}
        if not user_info and token.get("access_token"):
            resp = google.get("userinfo", token=token)
            user_info = resp.json() if resp else {}

        email = (user_info.get("email") or "").strip().lower()
        firstname = user_info.get("given_name", "")
        lastname = user_info.get("family_name", "")

        if not email:
            raise ValueError("Google n'a pas fourni d'email")

        base_url = request.url_root.rstrip("/")
        login_url = f"{base_url}/index.html"

        try:
            user = run_query(
                """
                SELECT id, firstname, lastname, email,
                       COALESCE(login_type, 'email') as login_type,
                       COALESCE(role, 'user') as role
                FROM users WHERE LOWER(TRIM(email))=%s
                """,
                (email,),
                True,
            )
        except Exception:
            user = run_query(
                "SELECT id, firstname, lastname, email FROM users WHERE LOWER(TRIM(email))=%s",
                (email,),
                True,
            )
            if user:
                user["login_type"] = "email"
                user["role"] = "user"

        if user and (user.get("login_type") or "email") != "google":
            return redirect(login_url)

        if user and (user.get("login_type") or "") == "google":
            username = email.split("@")[0]
            role = user.get("role", "user")
            user_data = {
                "id": user["id"],
                "username": username,
                "firstname": user["firstname"],
                "lastname": user["lastname"],
                "email": user["email"],
                "role": role,
            }
            redirect_url = f"{base_url}/admin.html" if role == "admin" else f"{base_url}/dash.html"
            return _oauth_redirect_html(user_data, redirect_url)

        try:
            user_id = run_update(
                """
                INSERT INTO users (firstname, lastname, email, password, login_type, role, created_at)
                VALUES (%s, %s, %s, %s, 'google', 'user', NOW())
                """,
                (firstname, lastname, email, ""),
            )
        except Exception:
            user_id = run_update(
                """
                INSERT INTO users (firstname, lastname, email, password, login_type, created_at)
                VALUES (%s, %s, %s, %s, 'google', NOW())
                """,
                (firstname, lastname, email, ""),
            )

        username = email.split("@")[0]
        user_data = {
            "id": user_id,
            "username": username,
            "firstname": firstname,
            "lastname": lastname,
            "email": email,
            "role": "user",
        }
        return _oauth_redirect_html(user_data, f"{base_url}/dash.html")

    except Exception as e:
        base_url = request.url_root.rstrip("/")
        login_url = f"{base_url}/index.html"
        return f"""<!DOCTYPE html><html><head><title>Erreur Auth</title></head>
<body><div style="text-align:center;padding:50px;font-family:Arial,sans-serif;">
  <h2 style="color:red;">Erreur d'authentification</h2><p>{str(e)}</p>
</div>
<script>setTimeout(function(){{ window.location.href='{login_url}'; }}, 3000);</script>
</body></html>"""


# -------------------------------------------------------------------
# CONTACT
# -------------------------------------------------------------------
@app.route("/api/contact", methods=["POST"])
def contact():
    try:
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "Données manquantes"}), 400

        name = (data.get("name") or "").strip()
        email = (data.get("email") or "").strip()
        subject = (data.get("subject") or "").strip()
        message = (data.get("message") or "").strip()

        if not name or not email or not subject or not message:
            return jsonify({"success": False, "error": "Tous les champs sont requis"}), 400

        return jsonify({"success": True, "message": "Message envoyé avec succès"}), 201
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# -------------------------------------------------------------------
# AUTH
# -------------------------------------------------------------------
@app.route("/api/register", methods=["POST"])
def register():
    data = request.get_json()
    firstname = data.get("firstname", "").strip()
    lastname = data.get("lastname", "").strip()
    email = data.get("email", "").strip()
    password = data.get("password", "")

    if not firstname or not lastname or not email or not password:
        return jsonify({"error": "Tous les champs sont requis"}), 400
    if len(password) < 6:
        return jsonify({"error": "Mot de passe minimum 6 caractères"}), 400
    if run_query("SELECT id FROM users WHERE email=%s", (email,), True):
        return jsonify({"error": "Email déjà utilisé"}), 409

    hashed = generate_password_hash(password)
    try:
        user_id = run_update(
            """
            INSERT INTO users (firstname, lastname, email, password, role, created_at)
            VALUES (%s, %s, %s, %s, 'user', NOW())
            """,
            (firstname, lastname, email, hashed),
        )
    except Exception:
        user_id = run_update(
            """
            INSERT INTO users (firstname, lastname, email, password, created_at)
            VALUES (%s, %s, %s, %s, NOW())
            """,
            (firstname, lastname, email, hashed),
        )

    return jsonify(
        {
            "message": "Inscription réussie",
            "user": {"id": user_id, "firstname": firstname, "lastname": lastname, "email": email},
        }
    ), 201


@app.route("/api/login", methods=["POST"])
def login():
    data = request.get_json()
    email = data.get("email", "").strip()
    password = data.get("password", "")

    if not email or not password:
        return jsonify({"error": "Email et mot de passe requis"}), 400

    try:
        user = run_query(
            """
            SELECT id, firstname, lastname, email, password,
                   COALESCE(role, 'user') as role
            FROM users WHERE email=%s
            """,
            (email,),
            True,
        )
    except Exception:
        user = run_query(
            "SELECT id, firstname, lastname, email, password FROM users WHERE email=%s",
            (email,),
            True,
        )
        if user:
            user["role"] = "user"

    if not user or not check_password_hash(user["password"], password):
        return jsonify({"error": "Email ou mot de passe incorrect"}), 401

    username = email.split("@")[0]
    role = user.get("role", "user")

    return jsonify(
        {
            "message": "Connexion réussie",
            "user": {
                "id": user["id"],
                "username": username,
                "firstname": user["firstname"],
                "lastname": user["lastname"],
                "email": user["email"],
                "role": role,
            },
        }
    )


# -------------------------------------------------------------------
# PROFILE CRUD
# -------------------------------------------------------------------
@app.route("/api/profile/<int:user_id>", methods=["GET"])
def get_profile(user_id):
    try:
        user = run_query(
            """
            SELECT id, firstname, lastname, email, created_at,
                   COALESCE(role, 'user') as role,
                   COALESCE(login_type, 'email') as login_type
            FROM users WHERE id=%s
            """,
            (user_id,),
            True,
        )
    except Exception:
        user = run_query(
            "SELECT id, firstname, lastname, email, created_at FROM users WHERE id=%s",
            (user_id,),
            True,
        )
        if user:
            user["role"] = "user"
            user["login_type"] = "email"

    if not user:
        return jsonify({"error": "Utilisateur non trouvé"}), 404
    if user["created_at"]:
        user["created_at"] = user["created_at"].strftime("%Y-%m-%d %H:%M")
    return jsonify(user)


@app.route("/api/profile/<int:user_id>", methods=["PUT"])
def update_profile(user_id):
    data = request.get_json() or {}
    firstname = (data.get("firstname") or "").strip()
    lastname = (data.get("lastname") or "").strip()
    email = (data.get("email") or "").strip().lower()

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
            (firstname, lastname, email, user_id),
        )
        updated_user = run_query(
            """
            SELECT id, firstname, lastname, email, created_at,
                   COALESCE(role, 'user') as role,
                   COALESCE(login_type, 'email') as login_type
            FROM users WHERE id=%s
            """,
            (user_id,),
            True,
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


# -------------------------------------------------------------------
# DASHBOARD STATS
# -------------------------------------------------------------------
@app.route("/api/stats", methods=["GET"])
def stats():
    try:
        total = run_query("SELECT COUNT(*) as n FROM users", fetch_one=True)["n"]
        today = datetime.now().strftime("%Y-%m-%d")
        today_count = run_query("SELECT COUNT(*) as n FROM users WHERE DATE(created_at)=%s", (today,), True)["n"]
        week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        week_count = run_query("SELECT COUNT(*) as n FROM users WHERE created_at >= %s", (week_ago,), True)["n"]

        try:
            google_users = run_query("SELECT COUNT(*) as n FROM users WHERE login_type='google'", fetch_one=True)["n"]
            email_users = run_query(
                "SELECT COUNT(*) as n FROM users WHERE login_type='email' OR login_type IS NULL OR login_type=''",
                fetch_one=True,
            )["n"]
            admin_count = run_query("SELECT COUNT(*) as n FROM users WHERE role='admin'", fetch_one=True)["n"]
        except Exception:
            google_users = 0
            email_users = total
            admin_count = 0

        return jsonify(
            {
                "total_users": total,
                "new_today": today_count,
                "active_week": week_count,
                "google_users": google_users,
                "email_users": email_users,
                "admin_count": admin_count,
            }
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# -------------------------------------------------------------------
# USERS LIST / ADMIN
# -------------------------------------------------------------------
@app.route("/api/users", methods=["GET"])
def users_list():
    try:
        users = run_query(
            """
            SELECT id, firstname, lastname, email, created_at,
                   COALESCE(login_type, 'email') as login_type,
                   COALESCE(role, 'user') as role
            FROM users ORDER BY id DESC
            """
        )
    except Exception:
        users = run_query("SELECT id, firstname, lastname, email, created_at, role FROM users ORDER BY id DESC")
        for u in users:
            u["login_type"] = "email"
            if "role" not in u or not u["role"]:
                u["role"] = "user"

    for u in users:
        if u["created_at"]:
            u["created_at"] = u["created_at"].strftime("%Y-%m-%d %H:%M")
    return jsonify(users)


@app.route("/api/admin/users/<int:target_id>/role", methods=["PUT"])
def update_user_role(target_id):
    data = request.get_json() or {}
    new_role = (data.get("role") or "").strip()
    if new_role not in ("user", "admin"):
        return jsonify({"error": "Rôle invalide"}), 400

    target = run_query("SELECT id, firstname, lastname FROM users WHERE id=%s", (target_id,), True)
    if not target:
        return jsonify({"error": "Utilisateur non trouvé"}), 404

    try:
        run_update("UPDATE users SET role=%s WHERE id=%s", (new_role, target_id))
        return jsonify(
            {
                "message": f"Rôle de {target['firstname']} {target['lastname']} → {new_role}",
                "user_id": target_id,
                "role": new_role,
            }
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/admin/users", methods=["POST"])
def create_user_admin():
    data = request.get_json() or {}
    firstname = (data.get("firstname") or "").strip()
    lastname = (data.get("lastname") or "").strip()
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    role = (data.get("role") or "user").strip().lower()

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
            """
            INSERT INTO users (firstname, lastname, email, password, role, login_type, created_at)
            VALUES (%s, %s, %s, %s, %s, 'email', NOW())
            """,
            (firstname, lastname, email, hashed, role),
        )
    except Exception:
        user_id = run_update(
            """
            INSERT INTO users (firstname, lastname, email, password, created_at)
            VALUES (%s, %s, %s, %s, NOW())
            """,
            (firstname, lastname, email, hashed),
        )
        role = "user"

    return jsonify(
        {
            "message": "Utilisateur créé",
            "user": {
                "id": user_id,
                "firstname": firstname,
                "lastname": lastname,
                "email": email,
                "role": role,
                "login_type": "email",
            },
        }
    ), 201


@app.route("/api/users/export", methods=["GET"])
def export_users():
    try:
        users = run_query(
            """
            SELECT id, firstname, lastname, email, created_at,
                   COALESCE(role, 'user') as role,
                   COALESCE(login_type, 'email') as login_type
            FROM users ORDER BY id
            """
        )
    except Exception:
        users = run_query("SELECT id, firstname, lastname, email, created_at FROM users ORDER BY id")
        for u in users:
            u["role"] = "user"
            u["login_type"] = "email"

    lines = ["id,firstname,lastname,email,role,login_type,created_at"]
    for u in users:
        created = u["created_at"]
        if hasattr(created, "strftime"):
            created = created.strftime("%Y-%m-%d %H:%M")
        lines.append(
            f"{u['id']},{u['firstname']},{u['lastname']},{u['email']},"
            f"{u.get('role','user')},{u.get('login_type','email')},{created}"
        )

    return Response(
        "\n".join(lines),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment;filename=users.csv"},
    )


# -------------------------------------------------------------------
# ANALYTICS — star-schema queries for charts.html
# -------------------------------------------------------------------
@app.route("/api/analytics/data", methods=["GET"])
def analytics_data():
    """Return all transactions joined with dimension tables."""
    user_id = get_current_user()
    if not user_id:
        return jsonify({"error": "Auth requis"}), 401
    try:
        rows = run_query("""
            SELECT
                t.Transaction_ID,
                t.Montant,
                t.Montant_Signe,
                d.Date        AS date_val,
                d.Année       AS annee,
                d.Mois        AS mois,
                d.Trimestre   AS trimestre,
                d.YearMonth   AS year_month,
                dep.NomDepartement  AS departement,
                tt.TypeTransaction  AS type_transaction,
                td.TypeDepense      AS type_depense,
                r.NomResponsable    AS responsable,
                cf.NomClientFournisseur AS client_fournisseur,
                cf.Type         AS cf_type,
                p.NomProjet     AS projet
            FROM transactions t
            LEFT JOIN `date`             d   ON d.Date_ID             = t.Date_ID
            LEFT JOIN departement        dep ON dep.Departement_ID     = t.Departement_ID
            LEFT JOIN typetransaction    tt  ON tt.TypeTransaction_ID  = t.TypeTransaction_ID
            LEFT JOIN typedepense        td  ON td.TypeDepense_ID      = t.TypeDepense_ID
            LEFT JOIN responsable        r   ON r.Responsable_ID       = t.Responsable_ID
            LEFT JOIN clientfournisseur  cf  ON cf.ClientFournisseur_ID= t.ClientFournisseur_ID
            LEFT JOIN projet             p   ON p.Projet_ID            = t.Projet_ID
            ORDER BY d.Date DESC
            LIMIT 50000
        """)
        safe = make_json_safe(rows)
        return jsonify({"success": True, "total": len(safe), "data": safe})
    except Exception as e:
        return jsonify({"success": False, "error": str(e), "detail": traceback.format_exc()}), 500


@app.route("/api/analytics/kpi-refresh", methods=["POST"])
def kpi_refresh():
    """Compute KPIs from transactions and (re)save them to valeur_kpi."""
    user_id = get_current_user()
    if not user_id:
        return jsonify({"error": "Auth requis"}), 401
    try:
        rows = run_query("""
            SELECT
                t.Montant, t.Montant_Signe,
                d.Année AS annee, d.Trimestre AS trimestre, d.YearMonth AS year_month,
                dep.NomDepartement AS departement, dep.Departement_ID,
                tt.TypeTransaction AS type_transaction,
                td.TypeDepense AS type_depense
            FROM transactions t
            LEFT JOIN `date`          d   ON d.Date_ID           = t.Date_ID
            LEFT JOIN departement     dep ON dep.Departement_ID   = t.Departement_ID
            LEFT JOIN typetransaction tt  ON tt.TypeTransaction_ID= t.TypeTransaction_ID
            LEFT JOIN typedepense     td  ON td.TypeDepense_ID    = t.TypeDepense_ID
        """)
        if not rows:
            return jsonify({"success": True, "inserted": 0, "message": "Aucune transaction à agréger"})

        kpis_to_save = []
        total_montant = sum(float(r.get("Montant") or 0) for r in rows)
        total_signe   = sum(float(r.get("Montant_Signe") or 0) for r in rows)
        revenus  = sum(float(r.get("Montant_Signe") or 0) for r in rows if (r.get("Montant_Signe") or 0) > 0)
        depenses = abs(sum(float(r.get("Montant_Signe") or 0) for r in rows if (r.get("Montant_Signe") or 0) < 0))
        n = len(rows)

        kpis_to_save += [
            {"kpiNom": "CA_Total",       "periode": "global", "valeur": round(total_montant, 2), "stat_type": "sum"},
            {"kpiNom": "Solde_Net",      "periode": "global", "valeur": round(total_signe, 2),   "stat_type": "sum"},
            {"kpiNom": "Revenus",        "periode": "global", "valeur": round(revenus, 2),        "stat_type": "sum"},
            {"kpiNom": "Dépenses",       "periode": "global", "valeur": round(depenses, 2),       "stat_type": "sum"},
            {"kpiNom": "Nb_Transactions","periode": "global", "valeur": n,                        "stat_type": "count"},
            {"kpiNom": "Valeur_Moyenne", "periode": "global", "valeur": round(total_montant / n, 2) if n else 0, "stat_type": "avg"},
            {"kpiNom": "Ratio_Dep_Rev",  "periode": "global", "valeur": round(depenses / revenus * 100, 2) if revenus else 0, "stat_type": "ratio"},
        ]

        # By quarter
        from collections import defaultdict
        q_data = defaultdict(list)
        for r in rows:
            key = f"Q{r.get('trimestre') or 0}_{r.get('annee') or 0}"
            q_data[key].append(float(r.get("Montant") or 0))
        for k, vals in q_data.items():
            kpis_to_save.append({"kpiNom": f"CA_{k}", "periode": k, "valeur": round(sum(vals), 2), "stat_type": "sum"})

        # By department
        dep_data = defaultdict(lambda: {"montant": [], "id": None})
        for r in rows:
            dep = r.get("departement") or "Inconnu"
            dep_data[dep]["montant"].append(float(r.get("Montant") or 0))
            dep_data[dep]["id"] = r.get("Departement_ID")
        for dep, d in dep_data.items():
            kpis_to_save.append({
                "kpiNom": f"CA_{dep}", "periode": "global",
                "valeur": round(sum(d["montant"]), 2),
                "departementId": d["id"], "stat_type": "sum"
            })

        # By type transaction
        tt_data = defaultdict(list)
        for r in rows:
            tt = r.get("type_transaction") or "Autre"
            tt_data[tt].append(float(r.get("Montant") or 0))
        for tt, vals in tt_data.items():
            kpis_to_save.append({"kpiNom": f"Vol_{tt}", "periode": "global", "valeur": round(sum(vals), 2), "stat_type": "sum"})

        # By type depense
        td_data = defaultdict(list)
        for r in rows:
            td = r.get("type_depense") or "Autre"
            td_data[td].append(float(r.get("Montant") or 0))
        for td, vals in td_data.items():
            kpis_to_save.append({"kpiNom": f"Dep_{td}", "periode": "global", "valeur": round(sum(vals), 2), "stat_type": "sum"})

        # Compute evolution % vs previous period using YearMonth grouping
        ym_data = defaultdict(list)
        for r in rows:
            ym = r.get("year_month") or "0000-00"
            ym_data[ym].append(float(r.get("Montant") or 0))
        sorted_ym = sorted(ym_data.keys())
        for i, ym in enumerate(sorted_ym):
            val = round(sum(ym_data[ym]), 2)
            prev_val = round(sum(ym_data[sorted_ym[i-1]]), 2) if i > 0 else val
            evo = round((val - prev_val) / prev_val * 100, 2) if prev_val else 0
            kpis_to_save.append({"kpiNom": "CA_Mensuel", "periode": ym, "valeur": val, "evolution": evo, "stat_type": "sum"})

        # Save to valeur_kpi (replace=True per period+nom)
        conn = get_connection()
        cursor = conn.cursor()
        inserted = 0
        for kpi in kpis_to_save:
            nom    = kpi.get("kpiNom", "")
            per    = kpi.get("periode", "global")
            valeur = float(kpi.get("valeur", 0))
            evo    = float(kpi.get("evolution", 0))
            dept   = kpi.get("departementId")
            stype  = kpi.get("stat_type", "sum")
            cursor.execute("DELETE FROM valeur_kpi WHERE kpiNom=%s AND periode=%s", (nom, per))
            cursor.execute(
                "INSERT INTO valeur_kpi (kpiNom, periode, valeur, evolution, departementId, source, stat_type) VALUES (%s,%s,%s,%s,%s,'etl_auto',%s)",
                (nom, per, valeur, evo, dept, stype)
            )
            inserted += 1
        conn.commit()
        cursor.close()
        conn.close()
        return jsonify({"success": True, "inserted": inserted})
    except Exception as e:
        return jsonify({"success": False, "error": str(e), "detail": traceback.format_exc()}), 500


# -------------------------------------------------------------------
# ETL
# -------------------------------------------------------------------
UPLOAD_FOLDER = Path(__file__).resolve().parent / "uploads_etl"
UPLOAD_FOLDER.mkdir(exist_ok=True)

ETL_ENGINE_PATH = Path(__file__).resolve().parent / "etl_generic.py"
_spec = _ilu.spec_from_file_location("etl_generic", str(ETL_ENGINE_PATH))
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

    file = request.files["file"]
    fname = file.filename or ""

    if not any(fname.lower().endswith(e) for e in (".csv", ".xlsx", ".xls")):
        return jsonify({"error": "Format accepté : CSV, XLSX, XLS"}), 400

    safe_fname = re.sub(r"[^\w._-]", "_", fname)
    save_path = UPLOAD_FOLDER / safe_fname
    file.save(str(save_path))

    try:
        df = read_table_rows(save_path)
        raw_rows = df.to_dict(orient="records")
        imported_rows = make_json_safe(raw_rows)

        return jsonify(
            {
                "success": True,
                "filename": safe_fname,
                "imported_rows": imported_rows,
                "stats": {
                    "columns": make_json_safe(list(df.columns)),
                    "rows_preview": len(df),
                },
            }
        )
    except Exception as e:
        return jsonify({"success": False, "error": f"Erreur lecture fichier: {str(e)}"}), 500


@app.route("/api/etl/process", methods=["POST"])
def etl_process():
    try:
        user_id = get_current_user()
        if not user_id:
            return jsonify({"error": "Authentification requise"}), 401

        data = request.get_json() or {}
        filename = data.get("filename")
        if not filename:
            return jsonify({"error": "Nom de fichier manquant"}), 400

        file_path = UPLOAD_FOLDER / filename
        if not file_path.exists():
            return jsonify({"error": "Fichier non trouvé"}), 404

        print(f"[ETL] Processing file: {filename} for user: {user_id}")
        result = run_generic_etl(str(file_path), replace_existing=True)

        if not result.get("success"):
            error_log = make_json_safe(result.get("log", []))
            try:
                save_import_history(
                    user_id=user_id,
                    filename=filename,
                    stats={"lignes": 0, "nb_erreurs": 1},
                    log=error_log,
                    cleaned_data=[],
                    success=False
                )
                print("[ETL] Failed import saved in history.")
            except Exception as hist_error:
                print(f"[ETL] History insert failed after ETL error: {hist_error}")

            return jsonify(
                {
                    "success": False,
                    "error": result.get("error", "Erreur ETL"),
                    "detail": result.get("detail"),
                    "log": error_log,
                }
            ), 500

        stats = make_json_safe(result.get("stats", {}) or {})
        log = make_json_safe(result.get("log", []) or [])
        db_result = make_json_safe(result.get("db_result", {}) or {})
        before_rows = make_json_safe(result.get("before_rows", []) or [])
        after_rows = make_json_safe(result.get("after_rows", []) or [])
        changed_rows = make_json_safe(result.get("changed_rows", 0))

        print(f"[ETL] Cleaning success: {stats}")

        try:
            save_import_history(
                user_id=user_id,
                filename=filename,
                stats=stats,
                log=log,
                cleaned_data=after_rows,
                success=True
            )
            print("[ETL] Success import saved in history.")
        except Exception as hist_error:
            print(f"[ETL] History insert failed: {hist_error}")

        return jsonify(
            {
                "success": True,
                "log": log,
                "stats": stats,
                "before_rows": before_rows,
                "after_rows": after_rows,
                "changed_rows": changed_rows,
                "db_result": db_result,
            }
        )

    except Exception as e:
        print("[ETL] Fatal error:")
        print(traceback.format_exc())

        try:
            data = request.get_json(silent=True) or {}
            filename = data.get("filename", "import_inconnu")
            user_id = get_current_user()
            if user_id:
                save_import_history(
                    user_id=user_id,
                    filename=filename,
                    stats={"lignes": 0, "nb_erreurs": 1},
                    log=[str(e), traceback.format_exc()],
                    cleaned_data=[],
                    success=False
                )
        except Exception as hist_error:
            print(f"[ETL] Fatal history insert failed: {hist_error}")

        return jsonify({"success": False, "error": str(e), "detail": traceback.format_exc()}), 500


@app.route("/api/etl/download", methods=["GET"])
def etl_download():
    cleaned_path = UPLOAD_FOLDER / "donnees_nettoyees.csv"
    if not cleaned_path.exists():
        return jsonify({"error": "Aucun fichier nettoyé disponible"}), 404
    return send_from_directory(
        str(UPLOAD_FOLDER),
        "donnees_nettoyees.csv",
        as_attachment=True,
        download_name="donnees_nettoyees.csv",
        mimetype="text/csv",
    )


@app.route("/api/etl/table-data", methods=["GET"])
def etl_table_data():
    try:
        user_id = get_current_user()
        if not user_id:
            return jsonify({"error": "Authentification requise"}), 401

        filename = request.args.get("filename", "").strip()
        stage = request.args.get("stage", "before").strip().lower()

        if not filename:
            return jsonify({"error": "Nom de fichier manquant"}), 400

        source_path = UPLOAD_FOLDER / filename
        cleaned_path = UPLOAD_FOLDER / "donnees_nettoyees.csv"

        if stage == "before":
            if not source_path.exists():
                return jsonify({"error": "Fichier source introuvable"}), 404
            df = read_table_rows(source_path)
        elif stage == "after":
            if not cleaned_path.exists():
                return jsonify({"error": "Fichier nettoyé introuvable"}), 404
            df = pd.read_csv(str(cleaned_path), encoding="utf-8-sig", low_memory=False)
        else:
            return jsonify({"error": "Stage invalide. Utiliser before ou after"}), 400

        rows = make_json_safe(df.to_dict(orient="records"))
        return jsonify({"success": True, "stage": stage, "rows": rows, "total": len(rows)})
    except Exception as e:
        return jsonify({"success": False, "error": str(e), "detail": traceback.format_exc()}), 500


@app.route("/api/etl/schema", methods=["GET"])
def etl_schema():
    try:
        from sqlalchemy import create_engine, inspect as sa_inspect

        engine = create_engine("mysql+pymysql://root:@127.0.0.1:3306/pfe_bd", echo=False)
        insp = sa_inspect(engine)
        tables = insp.get_table_names()
        result = {t: [{"name": c["name"], "type": str(c["type"])} for c in insp.get_columns(t)] for t in tables}
        return jsonify({"success": True, "tables": result})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# -------------------------------------------------------------------
# KPI
# -------------------------------------------------------------------
@app.route("/api/kpi/save", methods=["POST"])
def save_kpis():
    data = request.get_json(force=True) or {}
    kpis = data.get("kpis", [])
    source = data.get("source", "etl")
    do_replace = data.get("replace", False)

    if not kpis:
        return jsonify({"success": False, "error": "Aucun KPI fourni"}), 400

    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        inserted = 0
        replaced = 0

        for kpi in kpis:
            nom = str(kpi.get("kpiNom", "")).strip()
            periode = str(kpi.get("periode", "global")).strip()
            valeur = float(kpi.get("valeur", 0))
            evolution = float(kpi.get("evolution", 0))
            dept_id = kpi.get("departementId", None)
            stat_type = str(kpi.get("stat_type", "sum")).strip()

            if not nom:
                continue

            if do_replace:
                cursor.execute("DELETE FROM valeur_kpi WHERE kpiNom=%s AND periode=%s", (nom, periode))
                replaced += cursor.rowcount

            cursor.execute(
                """
                INSERT INTO valeur_kpi
                (kpiNom, periode, valeur, evolution, departementId, source, stat_type)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (nom, periode, valeur, evolution, dept_id, source, stat_type),
            )
            inserted += 1

        conn.commit()
        cursor.close()
        return jsonify({"success": True, "inserted": inserted, "replaced": replaced, "message": f"{inserted} KPI(s) sauvegardés"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        if conn:
            conn.close()


@app.route("/api/kpi", methods=["GET"])
def get_kpis():
    kpi_nom = request.args.get("kpiNom")
    periode = request.args.get("periode")
    limit = int(request.args.get("limit", 100))

    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor(dictionary=True)
        q = "SELECT * FROM valeur_kpi WHERE 1=1"
        p = []

        if kpi_nom:
            q += " AND kpiNom=%s"
            p.append(kpi_nom)
        if periode:
            q += " AND periode=%s"
            p.append(periode)

        q += " ORDER BY created_at DESC LIMIT %s"
        p.append(limit)
        cursor.execute(q, p)
        kpis = cursor.fetchall()
        cursor.close()

        for k in kpis:
            for f in ["created_at", "updated_at"]:
                if k.get(f):
                    k[f] = k[f].isoformat()

        return jsonify({"success": True, "kpis": kpis, "total": len(kpis)})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        if conn:
            conn.close()


# -------------------------------------------------------------------
# PREVISIONS
# -------------------------------------------------------------------
@app.route("/api/previsions", methods=["POST"])
def create_prevision():
    data = request.get_json(force=True) or {}
    user_id = get_current_user()
    type_prev = (data.get("type") or "").strip()
    date_debut = data.get("dateDebut")
    date_fin = data.get("dateFin")
    resultats = data.get("resultats", {})
    dept_id = data.get("departementId", None)

    if not type_prev or not date_debut or not date_fin:
        return jsonify({"success": False, "error": "type, dateDebut et dateFin sont requis"}), 400

    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO previsions (type, dateDebut, dateFin, resultats, departementId, created_by)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (type_prev, date_debut, date_fin, json.dumps(resultats, ensure_ascii=False), dept_id, user_id),
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
    dept_id = request.args.get("departementId")
    type_p = request.args.get("type")
    limit = int(request.args.get("limit", 50))

    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor(dictionary=True)
        q = "SELECT * FROM previsions WHERE 1=1"
        p = []

        if dept_id:
            q += " AND departementId=%s"
            p.append(dept_id)
        if type_p:
            q += " AND type=%s"
            p.append(type_p)

        q += " ORDER BY created_at DESC LIMIT %s"
        p.append(limit)
        cursor.execute(q, p)
        rows = cursor.fetchall()
        cursor.close()

        for r in rows:
            for f in ["created_at", "updated_at"]:
                if r.get(f):
                    r[f] = r[f].isoformat()
            for f in ["dateDebut", "dateFin"]:
                if r.get(f):
                    r[f] = str(r[f])
            if isinstance(r.get("resultats"), str):
                try:
                    r["resultats"] = json.loads(r["resultats"])
                except Exception:
                    pass

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
        conn = get_connection()
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
    try:
        create_kpi_tables()
        return jsonify({"success": True, "message": "Tables créées ou déjà existantes"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# -------------------------------------------------------------------
# STATIC FILES
# -------------------------------------------------------------------
@app.route("/")
def home():
    return send_from_directory(FRONTEND, "index.html")


@app.route("/<path:filename>")
def serve_static(filename):
    return send_from_directory(FRONTEND, filename)


# -------------------------------------------------------------------
# RUN
# -------------------------------------------------------------------
if __name__ == "__main__":
    create_kpi_tables()
    app.run(host="0.0.0.0", port=5000, debug=True)