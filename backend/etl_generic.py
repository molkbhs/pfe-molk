# -*- coding: utf-8 -*-
"""
ETL aligné sur pfe_bd (5).sql
- Ne modifie pas le schéma métier existant
- Charge dans : date, departement, responsable, typetransaction,
  typedepense, clientfournisseur, projet, transactions
- Préserve la structure pfe_bd (5) de transactions
- Retourne before_rows / after_rows pour affichage front
"""

from pathlib import Path
import gc
import warnings
import traceback
import math

import pandas as pd
import numpy as np
import pymysql

warnings.filterwarnings("ignore")

DB_HOST = "127.0.0.1"
DB_PORT = 3306
DB_USER = "root"
DB_PASS = ""
DB_NAME = "pfe_bd"

REQUIRED_COLUMNS = [
    "Date",
    "DepartementID",
    "Département",
    "TypeTransaction",
    "TypeDépense",
    "Montant",
    "Responsable",
    "Client_Fournisseur",
    "Projet",
]


def _connect():
    return pymysql.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASS,
        database=DB_NAME,
        charset="utf8mb4",
        autocommit=False,
        cursorclass=pymysql.cursors.DictCursor,
    )


def _read_file(path: Path, log: list) -> pd.DataFrame:
    ext = path.suffix.lower()
    if ext == ".csv":
        for enc in ["utf-8", "utf-8-sig", "latin-1", "cp1252"]:
            try:
                df = pd.read_csv(path, encoding=enc, low_memory=False)
                log.append(f"✓ Fichier lu (CSV, {enc}) : {len(df)} lignes")
                return df
            except UnicodeDecodeError:
                continue
        raise ValueError("Impossible de lire le CSV")
    if ext in [".xlsx", ".xls"]:
        df = pd.read_excel(path)
        log.append(f"✓ Fichier lu (Excel) : {len(df)} lignes")
        return df
    raise ValueError("Format non supporté")


def _norm(v):
    if pd.isna(v):
        return None
    s = " ".join(str(v).strip().split())
    return s or None


def _json_safe_records(df: pd.DataFrame):
    out = []
    for _, row in df.iterrows():
        record = {}
        for col, val in row.items():
            try:
                if pd.isna(val):
                    record[col] = None
                    continue
            except Exception:
                pass

            if isinstance(val, np.integer):
                record[col] = int(val)
            elif isinstance(val, np.floating):
                f = float(val)
                record[col] = None if math.isnan(f) or math.isinf(f) else f
            elif isinstance(val, np.bool_):
                record[col] = bool(val)
            elif isinstance(val, float):
                record[col] = None if math.isnan(val) or math.isinf(val) else val
            elif hasattr(val, "isoformat") and not isinstance(val, str):
                try:
                    record[col] = val.isoformat()
                except Exception:
                    record[col] = str(val)
            elif hasattr(val, "item"):
                try:
                    item = val.item()
                    if isinstance(item, float) and (math.isnan(item) or math.isinf(item)):
                        record[col] = None
                    else:
                        record[col] = item
                except Exception:
                    record[col] = str(val)
            elif isinstance(val, (int, str, bool)) or val is None:
                record[col] = val
            else:
                record[col] = str(val)

        out.append(record)
    return out


def _clean_dataframe(df: pd.DataFrame):
    log = []
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]

    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError("Colonnes manquantes: " + ", ".join(missing))

    before = len(df)
    df = df.drop_duplicates()
    removed = before - len(df)
    if removed:
        log.append(f"✓ {removed} doublon(s) supprimé(s)")

    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    df["Montant"] = (
        df["Montant"].astype(str)
        .str.replace(r"[€$£\s]", "", regex=True)
        .str.replace(",", ".", regex=False)
        .str.replace(r"[^\d.\-]", "", regex=True)
    )
    df["Montant"] = pd.to_numeric(df["Montant"], errors="coerce")

    for col in ["DepartementID", "Département", "TypeTransaction", "TypeDépense", "Responsable", "Client_Fournisseur", "Projet"]:
        df[col] = df[col].apply(_norm)

    df["TypeDépense"] = df["TypeDépense"].fillna("N/A")

    def signed_amount(row):
        montant = row["Montant"]
        ttype = (row["TypeTransaction"] or "").strip().lower()
        if pd.isna(montant):
            return None
        if ttype in ["depense", "dépense"]:
            return -abs(float(montant))
        return abs(float(montant))

    df["Montant_Signe"] = df.apply(signed_amount, axis=1)
    df["Année"] = df["Date"].dt.year
    df["Mois"] = df["Date"].dt.month
    df["Trimestre"] = df["Date"].dt.quarter
    df["Semaine"] = df["Date"].dt.isocalendar().week.astype("Int64")
    df["JourSemaine"] = df["Date"].dt.day_name()
    df["JourAnnée"] = df["Date"].dt.dayofyear
    df["YearMonth"] = df["Date"].dt.strftime("%Y-%m")
    df["AnnéeFiscale"] = df["Date"].dt.year

    invalid = df[
        df["Date"].isna()
        | df["Montant"].isna()
        | df["Département"].isna()
        | df["TypeTransaction"].isna()
        | df["Responsable"].isna()
        | df["Client_Fournisseur"].isna()
        | df["Projet"].isna()
    ]
    if len(invalid):
        log.append(f"⚠ {len(invalid)} ligne(s) invalide(s) ignorée(s)")
        df = df.drop(invalid.index)

    log.append(f"✓ Nettoyage terminé : {len(df)} lignes, {len(df.columns)} colonnes")
    return df.reset_index(drop=True), log


def _fetch_lookup(cursor, table, key_col, id_col):
    cursor.execute(f"SELECT `{id_col}`, `{key_col}` FROM `{table}`")
    rows = cursor.fetchall()
    return {row[key_col]: row[id_col] for row in rows if row[key_col] is not None}


def _fetch_combo_lookup(cursor, table, cols, id_col):
    cols_sql = ", ".join(f"`{c}`" for c in [id_col] + cols)
    cursor.execute(f"SELECT {cols_sql} FROM `{table}`")
    rows = cursor.fetchall()
    out = {}
    for row in rows:
        out[tuple(row[c] for c in cols)] = row[id_col]
    return out


def _next_id(cursor, table, id_col):
    cursor.execute(f"SELECT COALESCE(MAX(`{id_col}`), 0) + 1 AS next_id FROM `{table}`")
    return int(cursor.fetchone()["next_id"])


def _ensure_date_dimension(cursor, df, log):
    existing = _fetch_lookup(cursor, "date", "Date", "Date_ID")
    next_id = _next_id(cursor, "date", "Date_ID")
    inserted = 0

    for value in sorted(df["Date"].dropna().dt.strftime("%Y-%m-%d").unique()):
        if value in existing:
            continue
        d = pd.to_datetime(value)
        cursor.execute(
            """
            INSERT INTO `date`
            (`Date_ID`, `Date`, `Année`, `Mois`, `Trimestre`, `AnnéeFiscale`, `JourSemaine`, `Semaine`, `JourAnnée`, `YearMonth`)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                next_id,
                value,
                int(d.year),
                int(d.month),
                int(d.quarter),
                int(d.year),
                d.day_name(),
                int(d.isocalendar().week),
                int(d.dayofyear),
                d.strftime("%Y-%m"),
            ),
        )
        existing[value] = next_id
        next_id += 1
        inserted += 1

    if inserted:
        log.append(f"✓ {inserted} date(s) ajoutée(s)")
    return existing


def _ensure_responsables(cursor, df, log):
    lookup = _fetch_combo_lookup(cursor, "responsable", ["NomResponsable", "Departement"], "Responsable_ID")
    next_id = _next_id(cursor, "responsable", "Responsable_ID")
    inserted = 0

    for nom, dept in df[["Responsable", "Département"]].drop_duplicates().dropna().itertuples(index=False, name=None):
        key = (nom, dept)
        if key in lookup:
            continue
        cursor.execute(
            "INSERT INTO `responsable` (`Responsable_ID`, `NomResponsable`, `Departement`) VALUES (%s, %s, %s)",
            (next_id, nom, dept),
        )
        lookup[key] = next_id
        next_id += 1
        inserted += 1

    if inserted:
        log.append(f"✓ {inserted} responsable(s) ajouté(s)")
    return lookup


def _ensure_clientfournisseur(cursor, df, log):
    lookup = _fetch_lookup(cursor, "clientfournisseur", "NomClientFournisseur", "ClientFournisseur_ID")
    next_id = _next_id(cursor, "clientfournisseur", "ClientFournisseur_ID")
    inserted = 0

    for nom, ttype in df[["Client_Fournisseur", "TypeTransaction"]].drop_duplicates().dropna().itertuples(index=False, name=None):
        if nom in lookup:
            continue
        cf_type = "Client" if str(ttype).strip().lower() == "revenu" else "Fournisseur"
        cursor.execute(
            "INSERT INTO `clientfournisseur` (`ClientFournisseur_ID`, `NomClientFournisseur`, `Type`) VALUES (%s, %s, %s)",
            (next_id, nom, cf_type),
        )
        lookup[nom] = next_id
        next_id += 1
        inserted += 1

    if inserted:
        log.append(f"✓ {inserted} client(s)/fournisseur(s) ajouté(s)")
    return lookup


def _ensure_projets(cursor, df, log):
    lookup = _fetch_lookup(cursor, "projet", "NomProjet", "Projet_ID")
    next_id = _next_id(cursor, "projet", "Projet_ID")
    inserted = 0

    grouped = df.groupby("Projet")["Date"].agg(DateDebut="min", DateFin="max").reset_index()
    for row in grouped.itertuples(index=False):
        nom = row.Projet
        if nom in lookup or nom is None:
            continue
        date_debut = pd.to_datetime(row.DateDebut).strftime("%Y-%m-%d") if pd.notna(row.DateDebut) else None
        date_fin = pd.to_datetime(row.DateFin).strftime("%Y-%m-%d") if pd.notna(row.DateFin) else None
        cursor.execute(
            "INSERT INTO `projet` (`Projet_ID`, `NomProjet`, `DateDebut`, `DateFin`) VALUES (%s, %s, %s, %s)",
            (next_id, nom, date_debut, date_fin),
        )
        lookup[nom] = next_id
        next_id += 1
        inserted += 1

    if inserted:
        log.append(f"✓ {inserted} projet(s) ajouté(s)")
    return lookup


def _load_transactions(cursor, df, maps, replace_existing, log):
    if replace_existing:
        cursor.execute("DELETE FROM `transactions`")
        next_id = 1
        log.append("✓ Table transactions vidée avant rechargement")
    else:
        next_id = _next_id(cursor, "transactions", "Transaction_ID")

    rows = []
    skipped = 0

    for _, row in df.iterrows():
        date_key = pd.to_datetime(row["Date"]).strftime("%Y-%m-%d")
        dept_value = row["Département"]
        type_trans_value = row["TypeTransaction"]
        type_dep_value = row["TypeDépense"]
        client_value = row["Client_Fournisseur"]
        projet_value = row["Projet"]
        montant_value = row["Montant"]
        montant_signe_value = row["Montant_Signe"]
        resp_value = row["Responsable"]

        date_id = maps["date"].get(date_key)
        dept_id = maps["departement"].get(dept_value)
        ttid = maps["typetransaction"].get(type_trans_value)
        tdid = maps["typedepense"].get(type_dep_value)
        rid = maps["responsable"].get((resp_value, dept_value))
        cfid = maps["clientfournisseur"].get(client_value)
        pid = maps["projet"].get(projet_value)

        if None in [date_id, dept_id, ttid, tdid, rid, cfid, pid]:
            skipped += 1
            continue

        rows.append(
            (
                next_id,
                int(date_id),
                int(dept_id),
                int(ttid),
                int(tdid),
                int(rid),
                int(cfid),
                int(pid),
                round(float(montant_value), 2),
                str(round(float(montant_signe_value), 3)),
            )
        )
        next_id += 1

    sql = """
    INSERT INTO `transactions`
    (`Transaction_ID`, `Date_ID`, `Departement_ID`, `TypeTransaction_ID`,
     `TypeDepense_ID`, `Responsable_ID`, `ClientFournisseur_ID`,
     `Projet_ID`, `Montant`, `Montant_Signe`)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """
    if rows:
        cursor.executemany(sql, rows)

    if skipped:
        log.append(f"⚠ {skipped} transaction(s) ignorée(s) faute de correspondance")
    log.append(f"✓ {len(rows)} transaction(s) insérée(s)")
    return len(rows)


def run_generic_etl(file_path: str, replace_existing: bool = True) -> dict:
    path = Path(file_path)
    log = []

    try:
        df_raw = _read_file(path, log)
        df_before = df_raw.copy()

        df_clean, clean_log = _clean_dataframe(df_raw)
        log.extend(clean_log)

        out_path = path.parent / "donnees_nettoyees.csv"
        df_clean.to_csv(out_path, index=False, encoding="utf-8-sig")
        log.append(f"✓ CSV nettoyé exporté : {out_path.name}")

        conn = _connect()
        try:
            with conn.cursor() as cursor:
                maps = {}
                maps["date"] = _ensure_date_dimension(cursor, df_clean, log)
                maps["departement"] = _fetch_lookup(cursor, "departement", "NomDepartement", "Departement_ID")
                maps["typetransaction"] = _fetch_lookup(cursor, "typetransaction", "TypeTransaction", "TypeTransaction_ID")
                maps["typedepense"] = _fetch_lookup(cursor, "typedepense", "TypeDepense", "TypeDepense_ID")
                maps["responsable"] = _ensure_responsables(cursor, df_clean, log)
                maps["clientfournisseur"] = _ensure_clientfournisseur(cursor, df_clean, log)
                maps["projet"] = _ensure_projets(cursor, df_clean, log)
                inserted = _load_transactions(cursor, df_clean, maps, replace_existing, log)
            conn.commit()
        finally:
            conn.close()

        before_rows = _json_safe_records(df_before)
        after_rows = _json_safe_records(df_clean)
        changed_rows = min(len(before_rows), len(after_rows))

        stats = {
            "lignes": int(len(df_clean)),
            "colonnes": int(len(df_clean.columns)),
            "taux_correction": "100.0%",
            "total": round(float(df_clean["Montant"].sum()), 2),
            "solde": round(float(df_clean["Montant_Signe"].sum()), 2),
            "revenus": round(float(df_clean.loc[df_clean["Montant_Signe"] > 0, "Montant_Signe"].sum()), 2),
            "depenses": round(float(df_clean.loc[df_clean["Montant_Signe"] < 0, "Montant_Signe"].sum()), 2),
        }

        return {
            "success": True,
            "stats": stats,
            "before_rows": before_rows,
            "after_rows": after_rows,
            "changed_rows": changed_rows,
            "log": log,
            "db_result": {
                "db_name": DB_NAME,
                "table_name": "transactions",
                "rows_inserted": inserted,
                "mode": "pfe_bd_5_schema",
            },
        }

    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "detail": traceback.format_exc(),
            "log": log + [f"✗ Erreur ETL : {e}"],
        }
    finally:
        gc.collect()


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python etl_generic.py <fichier>")
        raise SystemExit(1)

    print(run_generic_etl(sys.argv[1], replace_existing=True))