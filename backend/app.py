"""
Flask backend - API d'authentification
Route POST /api/login compatible avec le frontend auth.js
"""
from flask import Flask, request, jsonify
from flask_cors import CORS
from werkzeug.security import check_password_hash, generate_password_hash
from db import get_connection

app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": "*"}})


@app.route("/api/login", methods=["POST"])
def login():
    """Authentification par email + mot de passe."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "Données JSON requises"}), 400

    email = data.get("email", "").strip()
    password = data.get("password", "")

    if not email or not password:
        return jsonify({"error": "Email et mot de passe requis"}), 400

    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor(dictionary=True, buffered=True)
        cursor.execute("SELECT id, username, email, password FROM users WHERE email = %s", (email,))
        user = cursor.fetchone()
        cursor.close()
    except Exception as e:
        return jsonify({"error": "Erreur serveur"}), 500
    finally:
        if conn:
            conn.close()

    if not user:
        return jsonify({"error": "Email ou mot de passe incorrect"}), 401

    if not check_password_hash(user["password"], password):
        return jsonify({"error": "Email ou mot de passe incorrect"}), 401

    return jsonify({
        "message": "Connexion réussie",
        "user": {
            "id": user["id"],
            "username": user["username"]
        }
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
