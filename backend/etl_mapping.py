"""
ETL Script v2 — Mapping données nettoyées → Base de données pfe_bd
CORRIGÉ : Gestion des types et doublons
"""

import pandas as pd
import warnings
from sqlalchemy import create_engine, text

warnings.filterwarnings("ignore")

# ─────────────────────────────────────────────
# 1. CONNEXION À LA BASE DE DONNÉES
# ─────────────────────────────────────────────
DB_USER     = "root"
DB_PASSWORD = ""
DB_HOST     = "127.0.0.1"
DB_PORT     = 3306
DB_NAME     = "pfe_bd"

engine = create_engine(
    f"mysql+pymysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}",
    echo=False
)

# ─────────────────────────────────────────────
# 2. NETTOYAGE DE LA BASE (OPTIONNEL)
# ─────────────────────────────────────────────
CLEAN_DATABASE = True  # Mettre à False si tu veux garder les données existantes

if CLEAN_DATABASE:
    print("🗑️  Nettoyage de la base de données...")
    with engine.connect() as conn:
        conn.execute(text("SET FOREIGN_KEY_CHECKS = 0"))
        for table in ["fait_transactions", "dim_date", "dim_departement", "dim_responsable",
                      "dim_typetransaction", "dim_typedepense", "dim_clientfournisseur", "dim_projet"]:
            conn.execute(text(f"TRUNCATE TABLE {table}"))
        conn.execute(text("SET FOREIGN_KEY_CHECKS = 1"))
        conn.commit()
    print("✓ Tables vidées\n")

# ─────────────────────────────────────────────
# 3. CHARGEMENT DES DONNÉES NETTOYÉES
# ─────────────────────────────────────────────
try:
    data = pd.read_excel("donnees_nettoyees.xlsx", sheet_name="Données Nettoyées")
    print("✓ Fichier Excel chargé")
except Exception:
    data = pd.read_csv("donnees_nettoyees.csv", encoding="utf-8-sig")
    print("✓ Fichier CSV chargé")

print(f"  → {len(data):,} lignes chargées\n")

# ─────────────────────────────────────────────
# 4. MAPPING — dim_date
# ─────────────────────────────────────────────
print("── dim_date ──────────────────────────────")

data["Date"] = pd.to_datetime(data["Date"], errors="coerce")

dates_uniq = data["Date"].dropna().unique()
dates_df = pd.DataFrame({"Date": pd.to_datetime(dates_uniq)})
dates_df = dates_df.sort_values("Date").reset_index(drop=True)
dates_df["Date_ID"]       = range(1, len(dates_df) + 1)
dates_df["Année"]         = dates_df["Date"].dt.year
dates_df["Mois"]          = dates_df["Date"].dt.month
dates_df["Trimestre"]     = dates_df["Date"].dt.quarter
dates_df["AnnéeFiscale"]  = dates_df["Date"].dt.year
dates_df["JourSemaine"]   = dates_df["Date"].dt.day_name()
dates_df["Semaine"]       = dates_df["Date"].dt.isocalendar().week.astype(int)
dates_df["JourAnnée"]     = dates_df["Date"].dt.dayofyear
dates_df["YearMonth"]     = dates_df["Date"].dt.strftime("%Y-%m")

dates_sql = dates_df[[
    "Date_ID", "Date", "Année", "Mois", "Trimestre",
    "AnnéeFiscale", "JourSemaine", "Semaine", "JourAnnée", "YearMonth"
]].copy()
dates_sql["Date"] = dates_sql["Date"].dt.strftime("%Y-%m-%d")

dates_sql.to_sql("dim_date", con=engine, if_exists="append", index=False)
date_map = dict(zip(dates_df["Date"].dt.strftime("%Y-%m-%d"), dates_df["Date_ID"]))
print(f"  → {len(dates_sql):,} dates insérées")

# ─────────────────────────────────────────────
# 5. MAPPING — dim_departement
# ─────────────────────────────────────────────
print("── dim_departement ───────────────────────")

depts = (
    data[["DepartementID", "Département"]]
    .drop_duplicates()
    .dropna(subset=["Département"])
    .copy()
)

# CRITIQUE : Extraire l'ID numérique de "DEPT001" → 1
depts["Departement_ID"] = depts["DepartementID"].astype(str).str.extract(r'(\d+)')[0].astype(int)
depts["NomDepartement"] = depts["Département"]
depts["DepartementCode"] = depts["DepartementID"].astype(str)
depts = depts[["Departement_ID", "NomDepartement", "DepartementCode"]].sort_values("Departement_ID")

depts.to_sql("dim_departement", con=engine, if_exists="append", index=False)
dept_map = dict(zip(depts["NomDepartement"], depts["Departement_ID"]))
print(f"  → {len(depts):,} départements insérés")

# ─────────────────────────────────────────────
# 6. MAPPING — dim_responsable
# ─────────────────────────────────────────────
print("── dim_responsable ───────────────────────")

resps = (
    data[["Responsable", "Département"]]
    .drop_duplicates(subset=["Responsable"])
    .dropna(subset=["Responsable"])
    .reset_index(drop=True)
)
resps.insert(0, "Responsable_ID", range(1, len(resps) + 1))
resps.columns = ["Responsable_ID", "NomResponsable", "Departement"]

resps.to_sql("dim_responsable", con=engine, if_exists="append", index=False)
resp_map = dict(zip(resps["NomResponsable"], resps["Responsable_ID"]))
print(f"  → {len(resps):,} responsables insérés")

# ─────────────────────────────────────────────
# 7. MAPPING — dim_typetransaction
# ─────────────────────────────────────────────
print("── dim_typetransaction ───────────────────")

trans_types = (
    data[["TypeTransaction"]]
    .drop_duplicates()
    .dropna()
    .reset_index(drop=True)
)
trans_types.insert(0, "TypeTransaction_ID", range(1, len(trans_types) + 1))

trans_types.to_sql("dim_typetransaction", con=engine, if_exists="append", index=False)
trans_map = dict(zip(trans_types["TypeTransaction"], trans_types["TypeTransaction_ID"]))
print(f"  → {len(trans_types):,} types de transaction insérés")

# ─────────────────────────────────────────────
# 8. MAPPING — dim_typedepense
# ─────────────────────────────────────────────
print("── dim_typedepense ───────────────────────")

depenses = (
    data[["TypeDépense"]]
    .drop_duplicates()
    .dropna()
    .reset_index(drop=True)
)
depenses.insert(0, "TypeDepense_ID", range(1, len(depenses) + 1))
depenses.columns = ["TypeDepense_ID", "TypeDepense"]

def categorize(type_depense):
    t = str(type_depense).lower()
    if any(k in t for k in ["salaire", "rh", "recrutement", "formation"]):
        return "Ressources Humaines"
    elif any(k in t for k in ["achat", "stock", "fournisseur", "matière"]):
        return "Achats & Approvisionnement"
    elif any(k in t for k in ["loyer", "immobilier", "infrastructure"]):
        return "Infrastructure"
    elif any(k in t for k in ["marketing", "publicité", "communication"]):
        return "Marketing"
    elif any(k in t for k in ["it", "informatique", "logiciel", "tech"]):
        return "IT & Technologie"
    elif any(k in t for k in ["revenu", "vente", "client"]):
        return "Revenus"
    else:
        return "Autres"

depenses["Categorie"] = depenses["TypeDepense"].apply(categorize)

depenses.to_sql("dim_typedepense", con=engine, if_exists="append", index=False)
depense_map = dict(zip(depenses["TypeDepense"], depenses["TypeDepense_ID"]))
print(f"  → {len(depenses):,} types de dépense insérés")

# ─────────────────────────────────────────────
# 9. MAPPING — dim_clientfournisseur
# ─────────────────────────────────────────────
print("── dim_clientfournisseur ─────────────────")

cf_list = (
    data[["Client_Fournisseur", "TypeTransaction"]]
    .drop_duplicates(subset=["Client_Fournisseur"])
    .dropna(subset=["Client_Fournisseur"])
    .reset_index(drop=True)
)
cf_list.insert(0, "ClientFournisseur_ID", range(1, len(cf_list) + 1))
cf_list["Type"] = cf_list["TypeTransaction"].apply(
    lambda x: "Client" if x == "Revenu" else "Fournisseur"
)
cf_list = cf_list[["ClientFournisseur_ID", "Client_Fournisseur", "Type"]]
cf_list.columns = ["ClientFournisseur_ID", "NomClientFournisseur", "Type"]

cf_list.to_sql("dim_clientfournisseur", con=engine, if_exists="append", index=False)
cf_map = dict(zip(cf_list["NomClientFournisseur"], cf_list["ClientFournisseur_ID"]))
print(f"  → {len(cf_list):,} clients/fournisseurs insérés")

# ─────────────────────────────────────────────
# 10. MAPPING — dim_projet
# ─────────────────────────────────────────────
print("── dim_projet ────────────────────────────")

if "Projet" in data.columns:
    projets = (
        data[["Projet"]]
        .drop_duplicates()
        .dropna()
        .reset_index(drop=True)
    )
    projets.insert(0, "Projet_ID", range(1, len(projets) + 1))
    projets.columns = ["Projet_ID", "NomProjet"]

    proj_dates = (
        data.groupby("Projet")["Date"]
        .agg(DateDebut="min", DateFin="max")
        .reset_index()
        .rename(columns={"Projet": "NomProjet"})
    )
    proj_dates["DateDebut"] = pd.to_datetime(proj_dates["DateDebut"]).dt.strftime("%Y-%m-%d")
    proj_dates["DateFin"]   = pd.to_datetime(proj_dates["DateFin"]).dt.strftime("%Y-%m-%d")
    projets = projets.merge(proj_dates, on="NomProjet", how="left")

    projets.to_sql("dim_projet", con=engine, if_exists="append", index=False)
    proj_map = dict(zip(projets["NomProjet"], projets["Projet_ID"]))
    print(f"  → {len(projets):,} projets insérés")
else:
    proj_map = {}
    print("  ⚠ Colonne 'Projet' absente")

# ─────────────────────────────────────────────
# 11. MAPPING — fait_transactions
# ─────────────────────────────────────────────
print("── fait_transactions ─────────────────────")

facts = data.copy()
facts["Date_str"] = facts["Date"].dt.strftime("%Y-%m-%d")

facts["Date_ID"]             = facts["Date_str"].map(date_map)
facts["Departement_ID"]      = facts["Département"].map(dept_map)
facts["TypeTransaction_ID"]  = facts["TypeTransaction"].map(trans_map)
facts["TypeDepense_ID"]      = facts["TypeDépense"].map(depense_map)
facts["Responsable_ID"]      = facts["Responsable"].map(resp_map)
facts["ClientFournisseur_ID"]= facts["Client_Fournisseur"].map(cf_map)
facts["Projet_ID"]           = facts["Projet"].map(proj_map) if "Projet" in facts.columns else None

missing_dates = facts["Date_ID"].isna().sum()
if missing_dates:
    print(f"  ⚠ {missing_dates} lignes sans Date_ID — ignorées")
    facts = facts.dropna(subset=["Date_ID"])

facts_sql = facts[[
    "Date_ID", "Departement_ID", "TypeTransaction_ID", "TypeDepense_ID",
    "Responsable_ID", "ClientFournisseur_ID", "Projet_ID",
    "Montant", "Montant_Signe"
]].copy()

int_cols = ["Date_ID", "Departement_ID", "TypeTransaction_ID",
            "TypeDepense_ID", "Responsable_ID", "ClientFournisseur_ID", "Projet_ID"]
for col in int_cols:
    facts_sql[col] = pd.to_numeric(facts_sql[col], errors="coerce").astype("Int64")

BATCH = 1_000
total = len(facts_sql)
inserted = 0
for i in range(0, total, BATCH):
    batch = facts_sql.iloc[i:i + BATCH]
    batch.to_sql("fait_transactions", con=engine, if_exists="append", index=False)
    inserted += len(batch)
    print(f"  → {inserted:,}/{total:,} lignes insérées", end="\r")

print(f"\n  ✓ {inserted:,} transactions insérées")

# ─────────────────────────────────────────────
# 12. RÉSUMÉ FINAL
# ─────────────────────────────────────────────
print("\n══════════════════════════════════════════")
print("  CHARGEMENT TERMINÉ")
print(f"  dim_date             : {len(dates_sql):>6,} lignes")
print(f"  dim_departement      : {len(depts):>6,} lignes")
print(f"  dim_responsable      : {len(resps):>6,} lignes")
print(f"  dim_typetransaction  : {len(trans_types):>6,} lignes")
print(f"  dim_typedepense      : {len(depenses):>6,} lignes")
print(f"  dim_clientfournisseur: {len(cf_list):>6,} lignes")
print(f"  dim_projet           : {len(projets) if 'Projet' in data.columns else 0:>6,} lignes")
print(f"  fait_transactions    : {inserted:>6,} lignes")
solde = facts_sql["Montant_Signe"].sum()
print(f"\n  Solde total          : {solde:>12,.2f} TND")
print("══════════════════════════════════════════")