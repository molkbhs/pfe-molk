# -*- coding: utf-8 -*-
"""ETL upload route — saves history on success for the authenticated user."""

from flask import Blueprint, request, jsonify
from datetime import datetime
import traceback
import sys
import os

# Résolution du chemin vers backend/ pour importer db.py directement
# routes/etl.py  →  backend/routes/etl.py  →  parent = backend/
_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from db import get_connection           # ← db.py à la racine de backend/
from etl_generic import run_generic_etl

etl_bp = Blueprint("etl", __name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_user_id():
    """Extrait l'id utilisateur depuis le header Authorization: Bearer <id>."""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        token = auth[7:].strip()
        if token:
            try:
                return int(token)
            except ValueError:
                return None
    return None


def _get_importe_par(conn, user_id):
    """Récupère le prénom (ou email) de l'utilisateur pour le champ importe_par."""
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT COALESCE(firstname, email) FROM users WHERE id = %s LIMIT 1",
            (user_id,)
        )
        row = cur.fetchone()
        cur.close()
        return row[0] if row else None
    except Exception:
        return None


def _save_history(user_id, filename, nb_lignes, nb_erreurs, statut,
                  departement=None, details=None, data=None):
    """Insère une ligne dans historique_imports pour l'utilisateur donné."""
    if not user_id:
        print("[_save_history] Aucun user_id — historique non enregistré.")
        return

    import json
    details_json = json.dumps(details, ensure_ascii=False) if details is not None else None
    data_json    = json.dumps(data,    ensure_ascii=False) if data    is not None else None

    # ✅ Pas de sous-requête dans VALUES — mysql.connector ne supporte pas
    # les sous-requêtes paramétrées embarquées dans VALUES().
    # On résout importe_par en Python avant l'INSERT.
    sql = """
        INSERT INTO historique_imports
            (user_id, nom_fichier, date_import, nb_lignes, nb_erreurs,
             statut, departement, importe_par, details, data)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """

    try:
        conn = get_connection()
        importe_par = _get_importe_par(conn, user_id)   # résolu avant l'INSERT
        cur  = conn.cursor()
        cur.execute(sql, (
            user_id,
            filename,
            datetime.now(),
            nb_lignes,
            nb_erreurs,
            statut,
            departement,
            importe_par,
            details_json,
            data_json,
        ))
        conn.commit()
        cur.close()
        conn.close()
        print(f"[_save_history] ✅ Sauvegardé — user={user_id}, fichier={filename}, statut={statut}")
    except Exception as e:
        traceback.print_exc()
        print(f"[_save_history] ❌ ÉCHEC INSERT — user_id={user_id}, fichier={filename}, erreur={e}")


# ---------------------------------------------------------------------------
# ETL upload endpoint
# ---------------------------------------------------------------------------

@etl_bp.route("/api/etl/upload", methods=["POST"])
def etl_upload():
    file = request.files.get("file")
    if not file:
        return jsonify({"success": False, "error": "No file provided"}), 400

    user_id  = _get_user_id()
    filename = file.filename or "upload"

    upload_dir = os.path.join(_BACKEND_DIR, "uploads_etl")
    os.makedirs(upload_dir, exist_ok=True)
    safe_name = os.path.basename(filename)
    tmp_path  = os.path.join(upload_dir, safe_name)

    try:
        file.save(tmp_path)
        result = run_generic_etl(tmp_path)
    except Exception as exc:
        traceback.print_exc()
        _save_history(user_id, filename, 0, 1, "echec")
        return jsonify({"success": False, "error": str(exc)}), 500
    finally:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass

    if not result.get("success"):
        _save_history(user_id, filename, 0, 1, "echec")
        return jsonify(result), 422

    stats       = result.get("stats", {})
    nb_lignes   = int(stats.get("lignes", len(result.get("preview", []))))
    nb_erreurs  = int(stats.get("erreurs", 0))
    statut      = "partiel" if nb_erreurs > 0 else "succes"
    departement = stats.get("departement") or stats.get("department")
    details_payload = stats.get("log") or result.get("log") or []
    data_payload    = result.get("cleaned_rows") or result.get("preview") or []

    _save_history(
        user_id, filename, nb_lignes, nb_erreurs, statut,
        departement=departement,
        details=details_payload,
        data=data_payload,
    )

    return jsonify(result)


# ---------------------------------------------------------------------------
# Download cleaned CSV
# ---------------------------------------------------------------------------

@etl_bp.route("/api/etl/download", methods=["GET"])
def etl_download():
    return jsonify({"error": "Not implemented"}), 501


# ---------------------------------------------------------------------------
# Historique — lecture liste
# ---------------------------------------------------------------------------

@etl_bp.route("/api/historique-imports", methods=["GET"])
def get_historique():
    user_id = _get_user_id()
    if not user_id:
        return jsonify({"imports": []})

    sql = """
        SELECT id, nom_fichier, date_import, nb_lignes, nb_erreurs,
               statut, departement, importe_par
        FROM historique_imports
        WHERE user_id = %s
        ORDER BY date_import DESC
        LIMIT 200
    """
    try:
        conn = get_connection()
        cur  = conn.cursor()
        cur.execute(sql, (user_id,))
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        cur.close()
        conn.close()
        for r in rows:
            if isinstance(r.get("date_import"), datetime):
                r["date_import"] = r["date_import"].strftime("%Y-%m-%d %H:%M")
        return jsonify({"imports": rows})
    except Exception as exc:
        traceback.print_exc()
        return jsonify({"imports": [], "error": str(exc)}), 500


# ---------------------------------------------------------------------------
# Historique — détail d'un import (preview depuis le champ `data`)
# ---------------------------------------------------------------------------

@etl_bp.route("/api/historique-imports/<int:import_id>", methods=["GET"])
def get_historique_detail(import_id):
    user_id = _get_user_id()
    if not user_id:
        return jsonify({"error": "Unauthorized"}), 401

    try:
        conn = get_connection()
        cur  = conn.cursor()

        # Récupère les métadonnées + le champ data en une seule requête
        cur.execute(
            """SELECT id, nom_fichier, date_import, nb_lignes, nb_erreurs,
                      statut, departement, importe_par, data
               FROM historique_imports
               WHERE id = %s AND user_id = %s""",
            (import_id, user_id),
        )
        cols = [d[0] for d in cur.description]
        row  = cur.fetchone()
        cur.close()
        conn.close()

        if not row:
            return jsonify({"error": "Not found"}), 404

        record = dict(zip(cols, row))
        if isinstance(record.get("date_import"), datetime):
            record["date_import"] = record["date_import"].strftime("%Y-%m-%d %H:%M")

        # Extraire preview depuis le champ JSON `data`
        import json
        preview, columns, rows_count = [], [], 0
        raw_data = record.pop("data", None)   # retire `data` du dict detail
        if raw_data:
            try:
                all_rows = json.loads(raw_data)
                if isinstance(all_rows, list) and all_rows:
                    columns    = list(all_rows[0].keys())
                    preview    = all_rows[:5]
                    rows_count = len(all_rows)
            except Exception:
                pass

        return jsonify({
            "import":     record,
            "preview":    preview,
            "columns":    columns,
            "rows_count": rows_count,
        })
    except Exception as exc:
        traceback.print_exc()
        return jsonify({"error": str(exc)}), 500


# ---------------------------------------------------------------------------
# Historique — suppression
# ---------------------------------------------------------------------------

@etl_bp.route("/api/historique-imports/<int:import_id>", methods=["DELETE"])
def delete_historique(import_id):
    user_id = _get_user_id()
    if not user_id:
        return jsonify({"error": "Unauthorized"}), 401

    try:
        conn = get_connection()
        cur  = conn.cursor()

        cur.execute(
            "SELECT id FROM historique_imports WHERE id = %s AND user_id = %s",
            (import_id, user_id),
        )
        if not cur.fetchone():
            cur.close(); conn.close()
            return jsonify({"error": "Import introuvable ou accès refusé"}), 404

        cur.execute(
            "DELETE FROM historique_imports WHERE id = %s AND user_id = %s",
            (import_id, user_id),
        )
        conn.commit()
        cur.close()
        conn.close()
        return jsonify({"success": True, "deleted_id": import_id})

    except Exception as exc:
        traceback.print_exc()
        return jsonify({"error": str(exc)}), 500