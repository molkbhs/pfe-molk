import pandas as pd
import warnings
warnings.filterwarnings('ignore')

data = pd.read_csv("données_financières_20k.csv")

# Encodage
colonnes_texte = data.select_dtypes(include=['object']).columns
for col in colonnes_texte:
    try:
        data[col] = data[col].astype(str).apply(
            lambda x: x.encode('latin1').decode('utf-8') if x != 'nan' else x
        )
    except:
        pass

# Conversion types
data['Date'] = pd.to_datetime(data['Date'], format='%Y-%m-%d', errors='coerce')

# Valeurs manquantes
data['TypeDépense'] = data['TypeDépense'].fillna('N/A')

# Doublons
data = data.drop_duplicates()

# Nettoyage espaces
for col in data.select_dtypes(include=['object']).columns:
    data[col] = data[col].apply(
        lambda x: ' '.join(str(x).split()).strip() if pd.notna(x) else x
    )

# Colonnes calculées
data['Montant_Signe'] = data.apply(
    lambda r: r['Montant'] if r['TypeTransaction']=='Revenu' else -r['Montant'], 
    axis=1
)
data['Signe'] = data['TypeTransaction'].apply(
    lambda x: 'Positif' if x == 'Revenu' else 'Négatif'
)
data['Semaine'] = data['Date'].dt.isocalendar().week
data['JourAnnée'] = data['Date'].dt.dayofyear

# Réorganisation
ordre = ['Entreprise', 'DepartementID', 'Département', 'Responsable', 'Date', 'Année', 'Mois', 
         'Trimestre', 'Semaine', 'JourSemaine', 'JourAnnée', 'YearMonth', 'AnnéeFiscale',
         'TypeTransaction', 'TypeDépense', 'Montant', 'Montant_Signe', 'Client_Fournisseur', 'Projet']
data = data[[col for col in ordre if col in data.columns]]

# Export
data.to_csv('donnees_nettoyees.csv', index=False, encoding='utf-8-sig')
try:
    data.to_excel('donnees_nettoyees.xlsx', index=False, sheet_name='Données Nettoyées', engine='openpyxl')
    print(f"✓ Fichiers créés | {len(data):,} lignes | Solde: {data['Montant_Signe'].sum():,.2f} TND")
except:
    print(f"✓ CSV créé | {len(data):,} lignes | Solde: {data['Montant_Signe'].sum():,.2f} TND")