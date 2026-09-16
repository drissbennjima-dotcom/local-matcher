# Local Matcher V4

Version V4 : géolocalisation du local, analyse d'un rayon commercial et historique SIRENE à l'adresse exacte.

## Fonctionnement
1. Géocodage de l'adresse via le service de géocodage de la Géoplateforme.
2. Récupération des établissements actifs de la commune via l'API Sirene et pagination par curseur.
3. Conversion des coordonnées Lambert de Sirene en latitude/longitude et filtrage par distance autour du local.
4. Recherche séparée des établissements actifs et fermés à l'adresse exacte.
5. Matching activité / enseignes comme dans les versions précédentes.

## Secrets Streamlit
```toml
SIRENE_API_KEY = "votre_clé"
```
Ne jamais mettre la clé dans GitHub ou dans `app.py`.
