# Local Matcher V4.2

Version V4.2 : géolocalisation du local, analyse d'un rayon commercial, regroupement des établissements par grandes catégories, historique SIRENE à l'adresse exacte et matching activité / enseignes.

## Fonctionnement
1. Géocodage de l'adresse via le service de géocodage de la Géoplateforme.
2. Récupération des établissements actifs de la commune via l'API Sirene et pagination par curseur.
3. Conversion des coordonnées Lambert de Sirene en latitude/longitude et filtrage par distance autour du local.
4. Visualisation cartographique allégée et indicateur de couverture géographique.
5. Regroupement analytique des APE en grandes catégories commerciales et lecture des catégories peu représentées.
6. Recherche séparée des établissements actifs et fermés à l'adresse exacte.
7. Matching activité / enseignes.

## Important
Les catégories commerciales sont des regroupements analytiques à partir de l'APE/NAF. Elles ne constituent ni une vérification terrain, ni une mesure de demande, ni un diagnostic technique.

## Secrets Streamlit
```toml
SIRENE_API_KEY = "votre_clé"
```
Ne jamais mettre la clé dans GitHub ou dans `app.py`.
