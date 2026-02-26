"""
ETL Générique — Moteur de nettoyage universel + Data Marts Financiers
======================================================================
Fonctionne avec n'importe quel CSV/Excel contenant des données financières.
Détecte automatiquement les colonnes et construit des data marts en MySQL :

  dim_temps        → colonnes de type date
  dim_categorie    → colonnes catégorielles (type, catégorie, libellé court…)
  dim_compte       → colonnes de compte / banque / devise
  fact_transactions→ table de faits centrale (montants + clés étrangères)

Usage :
    from etl_generic import run_generic_etl
    result = run_generic_etl("chemin/vers/fichier.csv")
"""

import re
import gc
import warnings
from pathlib import Path
from datetime import datetime

import pandas as pd
import numpy as np

warnings.filterwarnings("ignore")

# ─────────────────────────────────────────────
# CONFIGURATION MYSQL
# ─────────────────────────────────────────────
DB_HOST = "127.0.0.1"
DB_PORT = 3306
DB_USER = "root"
DB_PASS = ""
DB_NAME = "etl_data"

# ─────────────────────────────────────────────
# SIGNATURES DE COLONNES FINANCIÈRES
# (mots-clés → rôle sémantique)
# ─────────────────────────────────────────────
COL_SIGNATURES = {
    # Dates
    "date":        ["date", "dat", "jour", "day", "period", "période", "mois", "month", "time", "timestamp"],
    # Montants
    "montant":     ["montant", "amount", "valeur", "value", "prix", "price", "solde", "balance",
                    "débit", "debit", "crédit", "credit", "somme", "sum", "total"],
    # Type de transaction
    "type_trans":  ["type", "nature", "sens", "direction", "operation", "opération", "mouvement"],
    # Catégorie / libellé
    "categorie":   ["categorie", "catégorie", "category", "rubrique", "poste", "classe", "famille"],
    # Sous-catégorie
    "sous_cat":    ["sous", "sub", "detail", "détail", "detail"],
    # Libellé / description
    "libelle":     ["libelle", "libellé", "label", "description", "desc", "motif", "objet",
                    "intitule", "intitulé", "memo", "note", "commentaire"],
    # Compte
    "compte":      ["compte", "account", "iban", "rib", "numero", "numéro", "num"],
    # Banque
    "banque":      ["banque", "bank", "établissement", "etablissement", "institution"],
    # Devise
    "devise":      ["devise", "currency", "monnaie", "cur", "unit"],
    # Tiers / bénéficiaire
    "tiers":       ["tiers", "beneficiaire", "bénéficiaire", "recipient", "payee",
                    "fournisseur", "client", "vendor", "supplier"],
    # Identifiant
    "id_col":      ["id", "ref", "reference", "réference", "numero", "num", "code"],
}


# ═══════════════════════════════════════════════════════════════════════
# 1. DÉTECTION AUTOMATIQUE DES COLONNES
# ═══════════════════════════════════════════════════════════════════════

def _normalize(s: str) -> str:
    """Normalise un nom de colonne pour la comparaison."""
    s = s.lower().strip()
    s = re.sub(r"[àáâãäå]", "a", s)
    s = re.sub(r"[èéêë]", "e", s)
    s = re.sub(r"[ìíîï]", "i", s)
    s = re.sub(r"[òóôõö]", "o", s)
    s = re.sub(r"[ùúûü]", "u", s)
    s = re.sub(r"[ç]", "c", s)
    s = re.sub(r"[^a-z0-9_]", "_", s)
    return s


def detect_column_roles(df: pd.DataFrame) -> dict:
    """
    Retourne un dict { role → [col1, col2, …] }
    en analysant les noms ET le contenu de chaque colonne.
    """
    roles = {k: [] for k in COL_SIGNATURES}
    roles["unknown"] = []

    for col in df.columns:
        norm = _normalize(col)
        assigned = False

        # 1. Correspondance par nom
        for role, keywords in COL_SIGNATURES.items():
            if any(kw in norm for kw in keywords):
                roles[role].append(col)
                assigned = True
                break

        if assigned:
            continue

        # 2. Correspondance par contenu si le nom ne suffit pas
        sample = df[col].dropna().head(100)

        # Essai date
        if _looks_like_date(sample):
            roles["date"].append(col)
            continue

        # Essai numérique → montant potentiel
        if pd.api.types.is_numeric_dtype(df[col]):
            roles["montant"].append(col)
            continue

        # Colonne catégorielle (peu de valeurs uniques)
        n_unique = df[col].nunique()
        if n_unique <= max(20, len(df) * 0.05):
            roles["categorie"].append(col)
            continue

        # Texte long → libellé
        if df[col].dtype == object:
            avg_len = sample.astype(str).str.len().mean()
            if avg_len > 15:
                roles["libelle"].append(col)
            else:
                roles["categorie"].append(col)
            continue

        roles["unknown"].append(col)

    return roles


def _looks_like_date(series: pd.Series) -> bool:
    """Teste si une série ressemble à des dates."""
    try:
        parsed = pd.to_datetime(series, infer_datetime_format=True, errors="coerce")
        return parsed.notna().mean() > 0.7
    except Exception:
        return False


# ═══════════════════════════════════════════════════════════════════════
# 2. NETTOYAGE UNIVERSEL
# ═══════════════════════════════════════════════════════════════════════

def clean_dataframe(df: pd.DataFrame, roles: dict) -> pd.DataFrame:
    """Nettoyage générique adapté au type de chaque colonne."""
    log = []

    # ── Encodage / strip noms de colonnes ────────────────────────────
    df.columns = [c.strip() for c in df.columns]

    # ── Doublons ─────────────────────────────────────────────────────
    before = len(df)
    df = df.drop_duplicates()
    removed = before - len(df)
    if removed:
        log.append(f"✓ {removed} doublon(s) supprimé(s)")

    # ── Traitement par rôle ──────────────────────────────────────────

    # Dates → conversion + colonnes dérivées
    for col in roles.get("date", []):
        try:
            df[col] = pd.to_datetime(df[col], infer_datetime_format=True, errors="coerce")
            log.append(f"✓ Colonne date convertie : {col}")
        except Exception:
            pass

    # Montants → nettoyage numérique
    for col in roles.get("montant", []):
        if df[col].dtype == object:
            df[col] = (
                df[col].astype(str)
                    .str.replace(r"[€$£\s]", "", regex=True)
                    .str.replace(",", ".", regex=False)
                    .str.replace(r"[^\d.\-]", "", regex=True)
            )
        df[col] = pd.to_numeric(df[col], errors="coerce")

    # Catégories → strip + majuscule initiale
    for col in roles.get("categorie", []) + roles.get("type_trans", []) + roles.get("sous_cat", []):
        if df[col].dtype == object:
            df[col] = df[col].astype(str).str.strip().str.title()
            df[col] = df[col].replace({"Nan": None, "None": None, "": None})

    # Libellés → strip
    for col in roles.get("libelle", []):
        if df[col].dtype == object:
            df[col] = df[col].astype(str).str.strip()
            df[col] = df[col].replace({"nan": None, "None": None, "": None})

    # Montant signé : si une colonne type_trans existe, on crée montant_signe
    montant_cols   = roles.get("montant", [])
    type_trans_cols = roles.get("type_trans", [])
    if montant_cols and type_trans_cols:
        mc = montant_cols[0]
        tc = type_trans_cols[0]
        debit_kw  = ["debit", "débit", "retrait", "sortie", "depense", "dépense", "charge", "-"]
        credit_kw = ["credit", "crédit", "virement", "entree", "entrée", "recette", "revenu", "+"]

        def _sign(row):
            t = str(row[tc]).lower()
            if any(k in t for k in debit_kw):
                return -abs(row[mc]) if pd.notna(row[mc]) else None
            elif any(k in t for k in credit_kw):
                return abs(row[mc]) if pd.notna(row[mc]) else None
            return row[mc]

        df["montant_signe"] = df.apply(_sign, axis=1)
        log.append("✓ Colonne montant_signe calculée")

    # Valeurs manquantes
    for col in df.columns:
        if pd.api.types.is_numeric_dtype(df[col]):
            pass  # on laisse NaN pour MySQL NULL
        elif df[col].dtype == object:
            df[col] = df[col].fillna("N/A")

    log.append(f"✓ Nettoyage terminé : {len(df)} lignes, {len(df.columns)} colonnes")
    return df, log


# ═══════════════════════════════════════════════════════════════════════
# 3. CONSTRUCTION DES DATA MARTS
# ═══════════════════════════════════════════════════════════════════════

def build_data_marts(df: pd.DataFrame, roles: dict) -> dict:
    """
    Construit les tables dimensionnelles et la table de faits.
    Retourne un dict { table_name → DataFrame }.
    """
    marts = {}

    # ── dim_temps ────────────────────────────────────────────────────
    date_cols = roles.get("date", [])
    if date_cols:
        dc = date_cols[0]  # colonne date principale
        dates_valides = df[dc].dropna()
        if len(dates_valides):
            dim_temps = pd.DataFrame({"date_complete": dates_valides.unique()})
            dim_temps["date_complete"] = pd.to_datetime(dim_temps["date_complete"])
            dim_temps = dim_temps.sort_values("date_complete").reset_index(drop=True)
            dim_temps.insert(0, "id_temps", range(1, len(dim_temps) + 1))
            dim_temps["annee"]         = dim_temps["date_complete"].dt.year
            dim_temps["trimestre"]     = dim_temps["date_complete"].dt.quarter
            dim_temps["mois"]          = dim_temps["date_complete"].dt.month
            dim_temps["nom_mois"]      = dim_temps["date_complete"].dt.strftime("%B")
            dim_temps["semaine"]       = dim_temps["date_complete"].dt.isocalendar().week.astype(int)
            dim_temps["jour"]          = dim_temps["date_complete"].dt.day
            dim_temps["jour_semaine"]  = dim_temps["date_complete"].dt.strftime("%A")
            dim_temps["est_weekend"]   = dim_temps["date_complete"].dt.dayofweek >= 5
            dim_temps["date_complete"] = dim_temps["date_complete"].dt.strftime("%Y-%m-%d")
            marts["dim_temps"] = dim_temps

    # ── dim_categorie ────────────────────────────────────────────────
    cat_cols  = roles.get("categorie", [])
    type_cols = roles.get("type_trans", [])
    sous_cols = roles.get("sous_cat", [])

    if cat_cols or type_cols:
        cat_data = {}
        if type_cols:
            cat_data["type_transaction"] = df[type_cols[0]].astype(str).str.strip()
        if cat_cols:
            cat_data["categorie"] = df[cat_cols[0]].astype(str).str.strip()
        if sous_cols:
            cat_data["sous_categorie"] = df[sous_cols[0]].astype(str).str.strip()

        dim_cat = pd.DataFrame(cat_data).drop_duplicates().reset_index(drop=True)
        dim_cat = dim_cat[dim_cat.apply(
            lambda r: not all(v in ["nan", "N/A", "None", ""] for v in r.astype(str)), axis=1
        )]
        dim_cat.insert(0, "id_categorie", range(1, len(dim_cat) + 1))
        marts["dim_categorie"] = dim_cat

    # ── dim_compte ───────────────────────────────────────────────────
    compte_cols = roles.get("compte", [])
    banque_cols = roles.get("banque", [])
    devise_cols = roles.get("devise", [])
    tiers_cols  = roles.get("tiers", [])

    if compte_cols or banque_cols or devise_cols or tiers_cols:
        acc_data = {}
        if compte_cols: acc_data["compte"]  = df[compte_cols[0]].astype(str).str.strip()
        if banque_cols: acc_data["banque"]  = df[banque_cols[0]].astype(str).str.strip()
        if devise_cols: acc_data["devise"]  = df[devise_cols[0]].astype(str).str.strip()
        if tiers_cols:  acc_data["tiers"]   = df[tiers_cols[0]].astype(str).str.strip()

        dim_compte = pd.DataFrame(acc_data).drop_duplicates().reset_index(drop=True)
        dim_compte.insert(0, "id_compte", range(1, len(dim_compte) + 1))
        marts["dim_compte"] = dim_compte

    # ── fact_transactions ────────────────────────────────────────────
    fact = pd.DataFrame()

    # Clé date
    if "dim_temps" in marts and date_cols:
        dc = date_cols[0]
        df["_date_str"] = pd.to_datetime(df[dc], errors="coerce").dt.strftime("%Y-%m-%d")
        date_map = dict(zip(marts["dim_temps"]["date_complete"], marts["dim_temps"]["id_temps"]))
        fact["id_temps"] = df["_date_str"].map(date_map)

    # Clé catégorie
    if "dim_categorie" in marts:
        merge_cols_cat = {}
        if type_cols: merge_cols_cat["type_transaction"] = df[type_cols[0]].astype(str).str.strip()
        if cat_cols:  merge_cols_cat["categorie"]        = df[cat_cols[0]].astype(str).str.strip()
        if sous_cols: merge_cols_cat["sous_categorie"]   = df[sous_cols[0]].astype(str).str.strip()

        tmp = pd.DataFrame(merge_cols_cat)
        cat_map = marts["dim_categorie"].set_index(
            list(merge_cols_cat.keys())
        )["id_categorie"]
        try:
            fact["id_categorie"] = tmp.set_index(list(merge_cols_cat.keys())).index.map(
                lambda x: cat_map.get(x)
            )
        except Exception:
            pass

    # Clé compte
    if "dim_compte" in marts:
        merge_cols_acc = {}
        if compte_cols: merge_cols_acc["compte"] = df[compte_cols[0]].astype(str).str.strip()
        if banque_cols: merge_cols_acc["banque"] = df[banque_cols[0]].astype(str).str.strip()
        if devise_cols: merge_cols_acc["devise"] = df[devise_cols[0]].astype(str).str.strip()
        if tiers_cols:  merge_cols_acc["tiers"]  = df[tiers_cols[0]].astype(str).str.strip()

        tmp_acc = pd.DataFrame(merge_cols_acc)
        acc_map = marts["dim_compte"].set_index(list(merge_cols_acc.keys()))["id_compte"]
        try:
            fact["id_compte"] = tmp_acc.set_index(list(merge_cols_acc.keys())).index.map(
                lambda x: acc_map.get(x)
            )
        except Exception:
            pass

    # Montants
    montant_cols = roles.get("montant", [])
    if montant_cols:
        fact["montant"] = df[montant_cols[0]]
    if "montant_signe" in df.columns:
        fact["montant_signe"] = df["montant_signe"]

    # Libellé
    lib_cols = roles.get("libelle", [])
    if lib_cols:
        fact["libelle"] = df[lib_cols[0]].astype(str).str.strip()

    # Colonnes restantes inconnues → on les ajoute telles quelles
    used_cols = (
        date_cols + montant_cols + type_cols + cat_cols +
        sous_cols + lib_cols + compte_cols + banque_cols +
        devise_cols + tiers_cols + roles.get("id_col", []) +
        roles.get("unknown", []) + ["_date_str"]
    )
    for col in df.columns:
        if col not in used_cols and col not in fact.columns:
            fact[col] = df[col]

    # ── Renommer les colonnes qui entreraient en conflit avec id_transaction ──
    rename_map = {}
    for col in list(fact.columns):
        safe = re.sub(r"[^\w]", "_", col).lower()
        if safe == "id_transaction":
            rename_map[col] = "src_" + col
    if rename_map:
        fact = fact.rename(columns=rename_map)

    fact.insert(0, "id_transaction", range(1, len(fact) + 1))
    marts["fact_transactions"] = fact

    return marts


# ═══════════════════════════════════════════════════════════════════════
# 4. CHARGEMENT MYSQL
# ═══════════════════════════════════════════════════════════════════════

def _get_mysql_type(series: pd.Series) -> str:
    if pd.api.types.is_integer_dtype(series):
        return "BIGINT"
    if pd.api.types.is_float_dtype(series):
        return "DECIMAL(18,4)"
    if pd.api.types.is_bool_dtype(series):
        return "TINYINT(1)"
    if pd.api.types.is_datetime64_any_dtype(series):
        return "DATE"
    max_len = series.dropna().astype(str).str.len().max() if len(series.dropna()) else 50
    if max_len <= 10:  return "VARCHAR(20)"
    if max_len <= 50:  return "VARCHAR(100)"
    if max_len <= 255: return "VARCHAR(500)"
    return "TEXT"


def load_marts_to_mysql(marts: dict, source_name: str) -> dict:
    """
    Crée (ou recrée) les tables data mart dans MySQL et insère les données.
    Retourne un rapport d'exécution.
    """
    try:
        import pymysql
    except ImportError:
        return {"error": "pymysql non installé — pip install pymysql"}

    report = {}

    try:
        # Connexion sans base pour créer la DB si besoin
        conn = pymysql.connect(host=DB_HOST, port=DB_PORT, user=DB_USER,
                               password=DB_PASS, charset="utf8mb4")
        cur = conn.cursor()
        cur.execute(f"CREATE DATABASE IF NOT EXISTS `{DB_NAME}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
        conn.select_db(DB_NAME)

        ORDER = ["dim_temps", "dim_categorie", "dim_compte", "fact_transactions"]
        tables_to_load = ORDER + [t for t in marts if t not in ORDER]

        for table in tables_to_load:
            if table not in marts:
                continue
            df_mart = marts[table].copy()

            # Nettoyer les NaN → None pour MySQL
            df_mart = df_mart.where(pd.notnull(df_mart), None)

            # ── Dédupliquer les noms de colonnes ────────────────────
            # (évite "Duplicate column name" si le CSV source a des colonnes
            #  dont le nom normalisé entre en conflit avec nos clés générées)
            seen = {}
            new_cols = []
            for col in df_mart.columns:
                safe = re.sub(r"[^\w]", "_", str(col))
                if safe in seen:
                    seen[safe] += 1
                    safe = f"{safe}_{seen[safe]}"
                else:
                    seen[safe] = 0
                new_cols.append(safe)
            df_mart.columns = new_cols

            # ── DROP + CREATE ────────────────────────────────────────
            cur.execute(f"DROP TABLE IF EXISTS `{table}`")

            col_defs = []
            first_col = df_mart.columns[0]
            for col in df_mart.columns:
                sql_type = _get_mysql_type(df_mart[col])
                if col == first_col and col.startswith("id_"):
                    col_defs.append(f"`{col}` {sql_type} PRIMARY KEY")
                else:
                    col_defs.append(f"`{col}` {sql_type}")

            ddl = f"CREATE TABLE `{table}` ({', '.join(col_defs)}) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
            cur.execute(ddl)

            # ── INSERT par batch ─────────────────────────────────────
            cols_escaped = [f"`{c}`" for c in df_mart.columns]
            placeholders  = ", ".join(["%s"] * len(df_mart.columns))
            insert_sql    = f"INSERT INTO `{table}` ({', '.join(cols_escaped)}) VALUES ({placeholders})"

            batch_size = 1000
            rows = [tuple(r) for r in df_mart.itertuples(index=False, name=None)]
            for i in range(0, len(rows), batch_size):
                cur.executemany(insert_sql, rows[i:i + batch_size])

            conn.commit()
            report[table] = {"rows": len(df_mart), "columns": list(df_mart.columns)}

        cur.close()
        conn.close()
        return {"success": True, "db": DB_NAME, "tables": report}

    except Exception as e:
        import traceback
        return {"success": False, "error": str(e), "detail": traceback.format_exc()}


# ═══════════════════════════════════════════════════════════════════════
# 5. POINT D'ENTRÉE PRINCIPAL
# ═══════════════════════════════════════════════════════════════════════

def run_generic_etl(file_path: str) -> dict:
    """
    Pipeline complet :
      1. Lecture du fichier (CSV ou Excel)
      2. Détection des rôles de colonnes
      3. Nettoyage
      4. Construction des data marts
      5. Chargement MySQL
      6. Export CSV nettoyé
      7. Retour du résultat pour Flask

    Returns dict avec :
      success, stats, preview, total_preview, schema,
      db_result { db_name, table_name, rows_inserted, marts_detail }
    """
    path = Path(file_path)
    log  = []

    # ── 1. Lecture ────────────────────────────────────────────────────
    try:
        ext = path.suffix.lower()
        if ext == ".csv":
            for enc in ["utf-8", "latin-1", "cp1252"]:
                try:
                    df_raw = pd.read_csv(str(path), encoding=enc, low_memory=False)
                    log.append(f"✓ Fichier lu (CSV, {enc}) : {len(df_raw)} lignes")
                    break
                except UnicodeDecodeError:
                    continue
        else:
            df_raw = pd.read_excel(str(path))
            log.append(f"✓ Fichier lu (Excel) : {len(df_raw)} lignes")
    except Exception as e:
        return {"success": False, "log": [f"✗ Lecture impossible : {e}"]}

    # ── 2. Détection ──────────────────────────────────────────────────
    roles = detect_column_roles(df_raw)
    log.append(f"✓ Rôles détectés : { {k: v for k, v in roles.items() if v} }")

    # ── 3. Nettoyage ──────────────────────────────────────────────────
    df_clean, clean_log = clean_dataframe(df_raw.copy(), roles)
    log.extend(clean_log)

    # ── 4. Data Marts ─────────────────────────────────────────────────
    try:
        marts = build_data_marts(df_clean, roles)
        mart_summary = {t: len(df) for t, df in marts.items()}
        log.append(f"✓ Data marts construits : {mart_summary}")
    except Exception as e:
        import traceback
        return {"success": False, "log": log + [f"✗ Data mart échoué : {e}", traceback.format_exc()]}

    # ── 5. MySQL ──────────────────────────────────────────────────────
    db_result = load_marts_to_mysql(marts, path.stem)
    if db_result.get("success"):
        log.append(f"✓ MySQL : {len(db_result['tables'])} tables chargées dans `{DB_NAME}`")
        for t, info in db_result["tables"].items():
            log.append(f"   └─ {t} : {info['rows']} lignes")
    else:
        log.append(f"⚠ MySQL : {db_result.get('error', 'erreur inconnue')}")

    # ── 6. Export CSV nettoyé ─────────────────────────────────────────
    out_path = path.parent / "donnees_nettoyees.csv"
    try:
        df_clean.to_csv(str(out_path), index=False, encoding="utf-8-sig")
        log.append(f"✓ CSV nettoyé exporté : {out_path.name}")
    except Exception as e:
        log.append(f"⚠ Export CSV : {e}")
    finally:
        gc.collect()  # libère les handles pour éviter PermissionError Windows

    # ── 7. Résultat pour Flask ────────────────────────────────────────
    preview_df   = df_clean.head(100)
    preview_rows = []
    for _, row in preview_df.iterrows():
        r = {}
        for col in preview_df.columns:
            v = row[col]
            if pd.isna(v) if not isinstance(v, str) else False:
                r[col] = None
            elif hasattr(v, "item"):
                r[col] = v.item()
            else:
                r[col] = str(v) if not isinstance(v, (int, float, bool, type(None))) else v
        preview_rows.append(r)

    # Statistiques globales
    n_rows, n_cols = df_clean.shape
    num_cols = df_clean.select_dtypes(include="number").columns.tolist()
    stats = {
        "lignes":   n_rows,
        "colonnes": n_cols,
        "roles":    {k: v for k, v in roles.items() if v},
        "marts":    mart_summary,
        "log":      log,
    }
    if num_cols:
        mc = roles.get("montant", [None])[0] or num_cols[0]
        if mc in df_clean.columns:
            col_data = df_clean[mc].dropna()
            stats["montant_col"]  = mc
            stats["total"]        = round(float(col_data.sum()), 2)
            stats["moyenne"]      = round(float(col_data.mean()), 2)
            stats["min"]          = round(float(col_data.min()), 2)
            stats["max"]          = round(float(col_data.max()), 2)

        if "montant_signe" in df_clean.columns:
            ms = df_clean["montant_signe"].dropna()
            stats["solde"]    = round(float(ms.sum()), 2)
            stats["revenus"]  = round(float(ms[ms > 0].sum()), 2)
            stats["depenses"] = round(float(ms[ms < 0].sum()), 2)

    schema = {
        col: str(df_clean[col].dtype)
        for col in df_clean.columns
    }

    rows_inserted = sum(info["rows"] for info in db_result.get("tables", {}).values())

    return {
        "success":       True,
        "stats":         stats,
        "preview":       preview_rows,
        "total_preview": len(preview_rows),
        "schema":        schema,
        "log":           log,
        "db_result": {
            "db_name":       DB_NAME,
            "table_name":    "fact_transactions",
            "rows_inserted": rows_inserted,
            "marts_detail":  db_result.get("tables", {}),
        }
    }


# ── Test standalone ──────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage : python etl_generic.py <chemin_fichier.csv>")
        sys.exit(1)

    res = run_generic_etl(sys.argv[1])
    print("\n=== RÉSULTAT ETL ===")
    for line in res.get("log", []):
        print(line)
    if res.get("db_result"):
        print(f"\nBase : {res['db_result']['db_name']}")
        for t, info in res["db_result"].get("marts_detail", {}).items():
            print(f"  {t:25s} → {info['rows']:>6} lignes | {len(info['columns'])} colonnes")
    print(f"\nStatistiques : {res.get('stats', {})}")