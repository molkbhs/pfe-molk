import mysql.connector
from config import DB_CONFIG

def migrate():
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor()
        print("Adding login_type column...")
        cursor.execute("ALTER TABLE users ADD COLUMN login_type VARCHAR(20) DEFAULT 'email' AFTER password")
        conn.commit()
        print("Success!")
    except mysql.connector.Error as err:
        if err.errno == 1060:
            print("Column already exists.")
        else:
            print(f"Error: {err}")
    finally:
        if 'conn' in locals() and conn.is_connected():
            cursor.close()
            conn.close()

if __name__ == "__main__":
    migrate()
