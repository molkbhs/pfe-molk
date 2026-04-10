import mysql.connector
from config import DB_CONFIG

def migrate():
    conn = mysql.connector.connect(**DB_CONFIG)
    cursor = conn.cursor()
    try:
        print("Vérification de la table historique_imports...")
        cursor.execute("SHOW COLUMNS FROM historique_imports LIKE 'nb_colonnes'")
        result = cursor.fetchone()
        
        if not result:
            print("Ajout de la colonne 'nb_colonnes'...")
            cursor.execute("ALTER TABLE historique_imports ADD COLUMN nb_colonnes INT DEFAULT 0 AFTER nb_lignes")
            conn.commit()
            print("Migration réussie !")
        else:
            print("La colonne existe déjà.")
            
    except Exception as e:
        print(f"Erreur lors de la migration : {e}")
    finally:
        cursor.close()
        conn.close()

if __name__ == "__main__":
    migrate()
