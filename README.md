# Convertisseur Facebook — Render V4.1

Pack prêt à déployer sur Render.

## Fichiers
- `app.py` : résolution automatique V4.1
- `index.html` : interface + mode secours navigateur
- `requirements.txt`
- `render.yaml`

## Déploiement Render
Build command:
`pip install -r requirements.txt`

Start command:
`gunicorn app:app`

La V4.1 réduit les requêtes inutiles vers Facebook et bascule vers un mode manuel lorsque Facebook demande une connexion.
