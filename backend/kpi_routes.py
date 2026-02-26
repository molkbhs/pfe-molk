# -*- coding: utf-8 -*-
"""
Routes KPI + Prévisions — à intégrer dans app.py
Tables : valeur_kpi, previsions (schéma image)
"""

# ══════════════════════════════════════════════════════
# IMPORTS (ajouter à ceux existants dans app.py)
# ══════════════════════════════════════════════════════
# from flask import request, jsonify
# from datetime import datetime
# import json

# ══════════════════════════════════════════════════════
# HELPER DB — réutilise get_db() existant dans app.py
# ══════════════════════════════════════════════════════

def create_kpi_tables(conn):
    """
    Crée les tables valeur_kpi et previsions si elles n'existent pas.
    Appeler une fois au démarrage ou via /api/init-tables.
    """
    cursor = conn.cursor()

    # ── Table VALEUR KPI ──────────────────────────────
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS valeur_kpi (
            id            INT AUTO_INCREMENT PRIMARY KEY,
            kpiNom        VARCHAR(255)   NOT NULL,
            periode       VARCHAR(50)    NOT NULL,
            valeur        FLOAT          NOT NULL,
            evolution     FLOAT          DEFAULT 0,
            departementId INT            DEFAULT NULL,
            source        VARCHAR(100)   DEFAULT 'etl',
            stat_type     VARCHAR(20)    DEFAULT 'sum',
            created_at    DATETIME       DEFAULT CURRENT_TIMESTAMP,
            updated_at    DATETIME       DEFAULT CURRENT_TIMESTAMP
                                         ON UPDATE CURRENT_TIMESTAMP,
            FOREIGN KEY (departementId)
                REFERENCES departement(id)
                ON DELETE SET NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """)

    # ── Table PREVISIONS ─────────────────────────────
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
            FOREIGN KEY (departementId)
                REFERENCES departement(id)
                ON DELETE SET NULL,
            FOREIGN KEY (created_by)
                REFERENCES utilisateur(id)
                ON DELETE SET NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """)

    conn.commit()
    cursor.close()


# ══════════════════════════════════════════════════════
# ROUTE : Initialiser les tables
# ══════════════════════════════════════════════════════

@app.route('/api/init-tables', methods=['POST'])
@jwt_required()
def init_tables():
    """Crée valeur_kpi et previsions si elles n'existent pas."""
    try:
        conn = get_db()
        create_kpi_tables(conn)
        return jsonify({'success': True, 'message': 'Tables créées ou déjà existantes'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ══════════════════════════════════════════════════════
# ROUTE : Sauvegarder KPIs depuis charts.js
# ══════════════════════════════════════════════════════

@app.route('/api/kpi/save', methods=['POST'])
@jwt_required()
def save_kpis():
    """
    Body JSON attendu :
    {
        "kpis": [
            {
                "kpiNom":        "montant",
                "periode":       "2024-01",   // ou "global" si pas de date
                "valeur":        125000.50,
                "evolution":     12.3,         // delta % calculé côté front
                "departementId": null,
                "stat_type":     "sum"
            },
            ...
        ],
        "source": "etl",       // optionnel
        "replace": true        // si true : supprime les anciens KPIs de même nom+période
    }
    """
    data      = request.get_json(force=True)
    kpis      = data.get('kpis', [])
    source    = data.get('source', 'etl')
    do_replace = data.get('replace', False)

    if not kpis:
        return jsonify({'success': False, 'error': 'Aucun KPI fourni'}), 400

    try:
        conn   = get_db()
        cursor = conn.cursor()

        inserted = 0
        replaced = 0

        for kpi in kpis:
            nom       = str(kpi.get('kpiNom', '')).strip()
            periode   = str(kpi.get('periode', 'global')).strip()
            valeur    = float(kpi.get('valeur', 0))
            evolution = float(kpi.get('evolution', 0))
            dept_id   = kpi.get('departementId', None)
            stat_type = str(kpi.get('stat_type', 'sum')).strip()

            if not nom:
                continue

            if do_replace:
                # Supprimer l'ancien enregistrement de même nom + période
                cursor.execute(
                    "DELETE FROM valeur_kpi WHERE kpiNom = %s AND periode = %s",
                    (nom, periode)
                )
                replaced += cursor.rowcount

            cursor.execute("""
                INSERT INTO valeur_kpi
                    (kpiNom, periode, valeur, evolution, departementId, source, stat_type)
                VALUES
                    (%s, %s, %s, %s, %s, %s, %s)
            """, (nom, periode, valeur, evolution, dept_id, source, stat_type))
            inserted += 1

        conn.commit()
        cursor.close()

        return jsonify({
            'success':  True,
            'inserted': inserted,
            'replaced': replaced,
            'message':  f'{inserted} KPI(s) sauvegardés'
        })

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ══════════════════════════════════════════════════════
# ROUTE : Lire les KPIs (pour historique / dashboard)
# ══════════════════════════════════════════════════════

@app.route('/api/kpi', methods=['GET'])
@jwt_required()
def get_kpis():
    """
    Query params optionnels :
      - kpiNom  : filtrer par nom
      - periode : filtrer par période
      - limit   : nombre max de résultats (défaut 100)
    """
    kpi_nom = request.args.get('kpiNom')
    periode = request.args.get('periode')
    limit   = int(request.args.get('limit', 100))

    try:
        conn   = get_db()
        cursor = conn.cursor(dictionary=True)

        query  = "SELECT * FROM valeur_kpi WHERE 1=1"
        params = []

        if kpi_nom:
            query += " AND kpiNom = %s"
            params.append(kpi_nom)
        if periode:
            query += " AND periode = %s"
            params.append(periode)

        query += " ORDER BY created_at DESC LIMIT %s"
        params.append(limit)

        cursor.execute(query, params)
        kpis = cursor.fetchall()
        cursor.close()

        # Sérialiser les datetimes
        for k in kpis:
            for field in ['created_at', 'updated_at']:
                if k.get(field):
                    k[field] = k[field].isoformat()

        return jsonify({'success': True, 'kpis': kpis, 'total': len(kpis)})

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ══════════════════════════════════════════════════════
# ROUTE : Créer une prévision
# ══════════════════════════════════════════════════════

@app.route('/api/previsions', methods=['POST'])
@jwt_required()
def create_prevision():
    """
    Body JSON :
    {
        "type":          "budget_annuel",
        "dateDebut":     "2024-01-01",
        "dateFin":       "2024-12-31",
        "resultats":     { "total": 500000, "mensuel": [...] },
        "departementId": 1
    }
    """
    data      = request.get_json(force=True)
    current_user_id = get_jwt_identity()

    type_prev  = data.get('type', '').strip()
    date_debut = data.get('dateDebut')
    date_fin   = data.get('dateFin')
    resultats  = data.get('resultats', {})
    dept_id    = data.get('departementId', None)

    if not type_prev or not date_debut or not date_fin:
        return jsonify({'success': False, 'error': 'type, dateDebut et dateFin sont requis'}), 400

    try:
        conn   = get_db()
        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO previsions (type, dateDebut, dateFin, resultats, departementId, created_by)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (
            type_prev,
            date_debut,
            date_fin,
            json.dumps(resultats, ensure_ascii=False),
            dept_id,
            current_user_id
        ))

        conn.commit()
        new_id = cursor.lastrowid
        cursor.close()

        return jsonify({
            'success': True,
            'id':      new_id,
            'message': f'Prévision #{new_id} créée'
        })

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ══════════════════════════════════════════════════════
# ROUTE : Lire les prévisions
# ══════════════════════════════════════════════════════

@app.route('/api/previsions', methods=['GET'])
@jwt_required()
def get_previsions():
    dept_id = request.args.get('departementId')
    type_p  = request.args.get('type')
    limit   = int(request.args.get('limit', 50))

    try:
        conn   = get_db()
        cursor = conn.cursor(dictionary=True)

        query  = "SELECT * FROM previsions WHERE 1=1"
        params = []

        if dept_id:
            query += " AND departementId = %s"
            params.append(dept_id)
        if type_p:
            query += " AND type = %s"
            params.append(type_p)

        query += " ORDER BY created_at DESC LIMIT %s"
        params.append(limit)

        cursor.execute(query, params)
        previsions = cursor.fetchall()
        cursor.close()

        for p in previsions:
            for field in ['created_at', 'updated_at']:
                if p.get(field):
                    p[field] = p[field].isoformat()
            for field in ['dateDebut', 'dateFin']:
                if p.get(field):
                    p[field] = str(p[field])
            # Décoder JSON resultats si stocké en string
            if isinstance(p.get('resultats'), str):
                try:
                    p['resultats'] = json.loads(p['resultats'])
                except Exception:
                    pass

        return jsonify({'success': True, 'previsions': previsions, 'total': len(previsions)})

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ══════════════════════════════════════════════════════
# ROUTE : Supprimer une prévision
# ══════════════════════════════════════════════════════

@app.route('/api/previsions/<int:prev_id>', methods=['DELETE'])
@jwt_required()
def delete_prevision(prev_id):
    try:
        conn   = get_db()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM previsions WHERE id = %s", (prev_id,))
        conn.commit()
        deleted = cursor.rowcount
        cursor.close()
        if deleted:
            return jsonify({'success': True, 'message': f'Prévision #{prev_id} supprimée'})
        return jsonify({'success': False, 'error': 'Prévision non trouvée'}), 404
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ══════════════════════════════════════════════════════
# APPEL AU DÉMARRAGE — à ajouter dans if __name__ == '__main__':
# ══════════════════════════════════════════════════════
#
# with app.app_context():
#     conn = get_db()
#     create_kpi_tables(conn)
#
