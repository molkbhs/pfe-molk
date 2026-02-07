"""
Flask backend complet - Auth + Profile + Dashboard
"""

from datetime import datetime, timedelta
from pathlib import Path
from flask import Flask, request, jsonify, send_from_directory, Response
from flask_cors import CORS
from werkzeug.security import check_password_hash, generate_password_hash
from db import get_connection

app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": "*"}})

FRONTEND = Path(__file__).resolve().parent.parent / "frontend"


# ==================== DATABASE HELPERS ====================

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


# ==================== AUTH ====================

@app.route("/api/register", methods=["POST"])
def register():
    data = request.get_json()

    username = data.get("username", "").strip()
    email = data.get("email", "").strip()
    password = data.get("password", "")

    if not username or not email or not password:
        return jsonify({"error": "Tous les champs sont requis"}), 400

    if len(password) < 6:
        return jsonify({"error": "Mot de passe minimum 6 caractères"}), 400

    # Vérifications
    if run_query("SELECT id FROM users WHERE email=%s", (email,), True):
        return jsonify({"error": "Email déjà utilisé"}), 409

    if run_query("SELECT id FROM users WHERE username=%s", (username,), True):
        return jsonify({"error": "Username déjà pris"}), 409

    hashed_password = generate_password_hash(password)

    try:
        user_id = run_update(
            """INSERT INTO users (username, email, password, created_at)
               VALUES (%s, %s, %s, NOW())""",
            (username, email, hashed_password)
        )

        return jsonify({
            "message": "Inscription réussie",
            "user": {
                "id": user_id,
                "username": username,
                "email": email
            }
        }), 201

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/login", methods=["POST"])
def login():
    data = request.get_json()

    email = data.get("email", "").strip()
    password = data.get("password", "")

    if not email or not password:
        return jsonify({"error": "Email et mot de passe requis"}), 400

    user = run_query(
        "SELECT id, username, email, password FROM users WHERE email=%s",
        (email,),
        True
    )

    if not user or not check_password_hash(user["password"], password):
        return jsonify({"error": "Email ou mot de passe incorrect"}), 401

    return jsonify({
        "message": "Connexion réussie",
        "user": {
            "id": user["id"],
            "username": user["username"],
            "email": user["email"]
        }
    })


# ==================== PROFILE ====================

@app.route("/api/profile/<int:user_id>", methods=["GET"])
def get_profile(user_id):
    user = run_query(
        "SELECT id, username, email, created_at FROM users WHERE id=%s",
        (user_id,),
        True
    )

    if not user:
        return jsonify({"error": "Utilisateur non trouvé"}), 404

    return jsonify(user)


@app.route("/api/profile/<int:user_id>", methods=["PUT"])
def update_profile(user_id):
    data = request.get_json()

    username = data.get("username", "").strip()
    email = data.get("email", "").strip()

    if not username or not email:
        return jsonify({"error": "Champs requis"}), 400

    try:
        run_update(
            "UPDATE users SET username=%s, email=%s WHERE id=%s",
            (username, email, user_id)
        )

        return jsonify({"message": "Profil mis à jour"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/profile/<int:user_id>", methods=["DELETE"])
def delete_account(user_id):
    try:
        run_update("DELETE FROM users WHERE id=%s", (user_id,))
        return jsonify({"message": "Compte supprimé"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ==================== DASHBOARD ====================

@app.route("/api/stats", methods=["GET"])
def stats():
    try:
        total = run_query("SELECT COUNT(*) as n FROM users", fetch_one=True)["n"]

        today = datetime.now().strftime("%Y-%m-%d")
        today_count = run_query(
            "SELECT COUNT(*) as n FROM users WHERE DATE(created_at)=%s",
            (today,),
            True
        )["n"]

        week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        week_count = run_query(
            "SELECT COUNT(*) as n FROM users WHERE created_at >= %s",
            (week_ago,),
            True
        )["n"]

        return jsonify({
            "total_users": total,
            "new_today": today_count,
            "active_week": week_count
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ==================== USERS LIST ====================

@app.route("/api/users", methods=["GET"])
def users_list():
    users = run_query(
        "SELECT id, username, email, created_at FROM users ORDER BY id DESC"
    )

    for u in users:
        if u["created_at"]:
            u["created_at"] = u["created_at"].strftime("%Y-%m-%d %H:%M")

    return jsonify(users)


# ==================== EXPORT CSV ====================

@app.route("/api/users/export", methods=["GET"])
def export_users():
    users = run_query(
        "SELECT id, username, email, created_at FROM users ORDER BY id"
    )

    lines = ["id,username,email,created_at"]

    for u in users:
        created = u["created_at"]
        if hasattr(created, "strftime"):
            created = created.strftime("%Y-%m-%d %H:%M")

        lines.append(f"{u['id']},{u['username']},{u['email']},{created}")

    csv = "\n".join(lines)

    return Response(
        csv,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment;filename=users.csv"}
    )


# ==================== STATIC ====================

@app.route("/")
def home():
    return send_from_directory(FRONTEND, "index.html")


@app.route("/<path:filename>")
def serve_static(filename):
    return send_from_directory(FRONTEND, filename)


# ==================== RUN ====================

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
