# -*- coding: utf-8 -*-
from flask import Blueprint, request, jsonify
from models.database import run_query, run_update, safe_float, resolve_table
import traceback

kpi_bp = Blueprint('kpi', __name__)

# ─────────────────────────────────────────────────────────────
#  HELPER : détecte la table de transactions disponible
# ─────────────────────────────────────────────────────────────
def get_transaction_table():
    """Retourne le nom de la table de transactions existante."""
    for name in ("transaction", "transactions", "etl_transactions",
                 "dim_transaction", "fact_transaction"):
        from models.database import table_exists
        if table_exists(name):
            return name
    return None


# ─────────────────────────────────────────────────────────────
#  GET /api/kpi/computed  — calcule les KPIs depuis la base
# ─────────────────────────────────────────────────────────────
@kpi_bp.route("/api/kpi/computed", methods=["GET"])
def get_computed_kpis():
    """
    Calcule en temps réel les KPIs financiers depuis la table de transactions.
    Query params optionnels:
      - departementId : filtre par département
      - periode       : filtre YYYY-MM ou YYYY
    """
    dept_id = request.args.get("departementId")
    periode = request.args.get("periode")

    table = get_transaction_table()
    if not table:
        return jsonify({"success": False, "error": "Aucune table de transactions trouvée"}), 404

    # Construire les clauses WHERE dynamiques
    where_clauses = []
    params = []

    if dept_id:
        where_clauses.append("departementId = %s")
        params.append(dept_id)

    if periode:
        if len(periode) == 7:  # YYYY-MM
            where_clauses.append("DATE_FORMAT(date, '%%Y-%%m') = %s")
        else:  # YYYY
            where_clauses.append("YEAR(date) = %s")
        params.append(periode)

    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    try:
        # ── 1. KPIs globaux ──────────────────────────────────
        global_row = run_query(f"""
            SELECT
                COUNT(*)                                             AS nb_transactions,
                COALESCE(SUM(CASE WHEN montantSigne > 0 THEN  montantSigne ELSE 0 END), 0) AS total_revenus,
                COALESCE(SUM(CASE WHEN montantSigne < 0 THEN -montantSigne ELSE 0 END), 0) AS total_depenses,
                COALESCE(SUM(montantSigne), 0)                       AS solde_net,
                COALESCE(AVG(ABS(montantSigne)), 0)                  AS moyenne_transaction
            FROM `{table}`
            {where_sql}
        """, params or None, fetch_one=True)

        if not global_row:
            global_row = {}

        total_rev  = safe_float(global_row.get("total_revenus", 0))
        total_dep  = safe_float(global_row.get("total_depenses", 0))
        solde      = safe_float(global_row.get("solde_net", 0))
        nb_tx      = int(global_row.get("nb_transactions", 0) or 0)
        avg_tx     = safe_float(global_row.get("moyenne_transaction", 0))
        marge      = round((solde / total_rev * 100), 2) if total_rev > 0 else 0.0

        # ── 2. KPIs par département ──────────────────────────
        dept_rows = run_query(f"""
            SELECT
                departementId,
                COUNT(*)                                                      AS nb_transactions,
                COALESCE(SUM(CASE WHEN montantSigne > 0 THEN  montantSigne ELSE 0 END), 0) AS revenus,
                COALESCE(SUM(CASE WHEN montantSigne < 0 THEN -montantSigne ELSE 0 END), 0) AS depenses,
                COALESCE(SUM(montantSigne), 0)                                 AS solde
            FROM `{table}`
            {where_sql}
            GROUP BY departementId
            ORDER BY depenses DESC
        """, params or None)

        # ── 3. Évolution mensuelle ───────────────────────────
        monthly_rows = run_query(f"""
            SELECT
                DATE_FORMAT(date, '%%Y-%%m')                                   AS mois,
                COALESCE(SUM(CASE WHEN montantSigne > 0 THEN  montantSigne ELSE 0 END), 0) AS revenus,
                COALESCE(SUM(CASE WHEN montantSigne < 0 THEN -montantSigne ELSE 0 END), 0) AS depenses
            FROM `{table}`
            {where_sql}
            GROUP BY mois
            ORDER BY mois ASC
        """, params or None)

        # Calcul solde cumulé
        cumul = 0.0
        for row in (monthly_rows or []):
            row["revenus"]  = safe_float(row.get("revenus", 0))
            row["depenses"] = safe_float(row.get("depenses", 0))
            cumul += row["revenus"] - row["depenses"]
            row["solde_cumule"] = round(cumul, 2)

        # ── 4. Top types de dépenses ─────────────────────────
        type_dep_rows = run_query(f"""
            SELECT
                COALESCE(typeDepense, 'Non classé')   AS type_depense,
                COUNT(*)                               AS nb_transactions,
                SUM(-montantSigne)                     AS montant_total,
                AVG(-montantSigne)                     AS montant_moyen
            FROM `{table}`
            {(where_sql + " AND montantSigne < 0") if where_sql else "WHERE montantSigne < 0"}
            GROUP BY typeDepense
            ORDER BY montant_total DESC
            LIMIT 10
        """, params or None)

        for r in (type_dep_rows or []):
            r["montant_total"] = safe_float(r.get("montant_total", 0))
            r["montant_moyen"] = safe_float(r.get("montant_moyen", 0))

        # ── 5. Top projets ───────────────────────────────────
        projet_rows = run_query(f"""
            SELECT
                COALESCE(nomProjet, '—')  AS projet,
                COUNT(*)                  AS nb_transactions,
                SUM(ABS(montantSigne))    AS montant_total,
                AVG(ABS(montantSigne))    AS montant_moyen,
                SUM(CASE WHEN montantSigne > 0 THEN  montantSigne ELSE 0 END) AS revenus,
                SUM(CASE WHEN montantSigne < 0 THEN -montantSigne ELSE 0 END) AS depenses
            FROM `{table}`
            {where_sql}
            GROUP BY nomProjet
            ORDER BY montant_total DESC
            LIMIT 10
        """, params or None)

        for r in (projet_rows or []):
            for col in ("montant_total", "montant_moyen", "revenus", "depenses"):
                r[col] = safe_float(r.get(col, 0))

        # ── 6. Top clients / fournisseurs ────────────────────
        cf_rows = run_query(f"""
            SELECT
                COALESCE(nomClientFournisseur, '—') AS client_fournisseur,
                COUNT(*)                             AS nb_transactions,
                SUM(ABS(montantSigne))               AS montant_total
            FROM `{table}`
            {where_sql}
            GROUP BY nomClientFournisseur
            ORDER BY montant_total DESC
            LIMIT 10
        """, params or None)

        for r in (cf_rows or []):
            r["montant_total"] = safe_float(r.get("montant_total", 0))

        # ── Réponse finale ───────────────────────────────────
        return jsonify({
            "success": True,
            "kpis": {
                "total_revenus":      round(total_rev, 2),
                "total_depenses":     round(total_dep, 2),
                "solde_net":          round(solde, 2),
                "marge_nette":        marge,
                "nb_transactions":    nb_tx,
                "moyenne_transaction": round(avg_tx, 2),
            },
            "par_departement":  [_serialize(r) for r in (dept_rows or [])],
            "evolution_mensuelle": [_serialize(r) for r in (monthly_rows or [])],
            "top_types_depenses":  [_serialize(r) for r in (type_dep_rows or [])],
            "top_projets":         [_serialize(r) for r in (projet_rows or [])],
            "top_clients_fournisseurs": [_serialize(r) for r in (cf_rows or [])],
        })

    except Exception as e:
        print(f"[get_computed_kpis] ERROR:\n{traceback.format_exc()}")
        return jsonify({"success": False, "error": str(e)}), 500


def _serialize(row: dict) -> dict:
    """Convertit les types non-JSON-sérialisables (Decimal, datetime…)."""
    from decimal import Decimal
    from datetime import date, datetime
    out = {}
    for k, v in row.items():
        if isinstance(v, Decimal):
            out[k] = float(v)
        elif isinstance(v, (datetime, date)):
            out[k] = v.isoformat()
        else:
            out[k] = v
    return out


# ─────────────────────────────────────────────────────────────
#  POST /api/kpi/save  — sauvegarde des KPIs pré-calculés
# ─────────────────────────────────────────────────────────────
@kpi_bp.route("/api/kpi/save", methods=["POST"])
def save_kpis():
    data = request.get_json(force=True) or {}
    kpis = data.get("kpis", [])
    source = data.get("source", "etl")
    do_replace = data.get("replace", False)

    if not kpis:
        return jsonify({"success": False, "error": "Aucun KPI fourni"}), 400

    conn = None
    try:
        from db import get_connection
        conn = get_connection()
        cursor = conn.cursor()
        inserted = replaced = 0

        for kpi in kpis:
            nom       = str(kpi.get("kpiNom", "")).strip()
            periode   = str(kpi.get("periode", "global")).strip()
            valeur    = float(kpi.get("valeur", 0))
            evolution = float(kpi.get("evolution", 0))
            dept_id   = kpi.get("departementId", None)
            stat_type = str(kpi.get("stat_type", "sum")).strip()

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
                   VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                (nom, periode, valeur, evolution, dept_id, source, stat_type),
            )
            inserted += 1

        conn.commit()
        cursor.close()
        return jsonify({
            "success": True,
            "inserted": inserted,
            "replaced": replaced,
            "message": f"{inserted} KPI(s) sauvegardés"
        })

    except Exception as e:
        print(f"[save_kpis] ERROR:\n{traceback.format_exc()}")
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        if conn:
            try: conn.close()
            except Exception: pass


# ─────────────────────────────────────────────────────────────
#  GET /api/kpi  — lecture brute de valeur_kpi
# ─────────────────────────────────────────────────────────────
@kpi_bp.route("/api/kpi", methods=["GET"])
def get_kpis():
    kpi_nom = request.args.get("kpiNom")
    periode = request.args.get("periode")
    limit   = int(request.args.get("limit", 100))

    conn = None
    try:
        from db import get_connection
        conn = get_connection()
        cursor = conn.cursor(dictionary=True)

        q, p = "SELECT * FROM valeur_kpi WHERE 1=1", []
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
            for f in ("created_at", "updated_at"):
                if k.get(f):
                    k[f] = k[f].isoformat()
            k["valeur"]    = safe_float(k.get("valeur", 0))
            k["evolution"] = safe_float(k.get("evolution", 0))

        return jsonify({"success": True, "kpis": kpis, "total": len(kpis)})

    except Exception as e:
        print(f"[get_kpis] ERROR:\n{traceback.format_exc()}")
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        if conn:
            try: conn.close()
            except Exception: pass