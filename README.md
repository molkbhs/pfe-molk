# auth-web-app

Petit projet d'authentification Flask + MySQL (phpMyAdmin/XAMPP).

Prerequis:
- Python 3.8+
- MySQL (XAMPP) ou équivalent

Installation:

```bash
cd backend
python -m venv .venv
.\.venv\Scripts\activate   # Windows
pip install -r requirements.txt
```

Base de données:

1. Importez `backend/schema.sql` via phpMyAdmin ou exécutez-le dans MySQL.

Lancer l'API:

```bash
cd backend
python app.py
```

Frontend:

Ouvrez `frontend/index.html` dans votre navigateur (ou servez les fichiers via un serveur statique).

Endpoints utiles:
- `POST /api/register` : body JSON `{ "username", "email", "password" }`
- `POST /api/login` : body JSON `{ "email", "password" }`

Améliorations suggérées:
- Utiliser HTTPS et tokens JWT
- Validation côté serveur et côté client
- Gestion des sessions et protections CSRF
