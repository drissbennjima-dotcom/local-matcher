# Local Matcher V5.3.1

Version V5 : géolocalisation du local, analyse d'un rayon commercial, regroupement des établissements par grandes catégories, historique SIRENE à l'adresse exacte, chronologie historisée et liens de succession, puis matching activité / enseignes.

## Nouveautés V5
1. Recherche séparée des établissements actifs et fermés à l'adresse exacte.
2. Reconstitution limitée de l'historique détaillé des établissements trouvés, avec respect du quota de l'API publique.
3. Affichage d'une chronologie SIRENE : dates de début/fin, statut, enseigne, entreprise et APE.
4. Recherche des liens de succession entre établissements lorsque l'API les expose.
5. Détection des coordonnées SIRENE en WGS84 ou Lambert-93 selon leur plage numérique, pour éviter une conversion incorrecte.
6. Les liens de succession et périodes sont présentés comme des données administratives : ils ne constituent pas, à eux seuls, une preuve juridique d'occupation physique d'un local.

## Fonctionnement
1. Géocodage de l'adresse via le service de géocodage de la Géoplateforme.
2. Récupération des établissements actifs de la commune via l'API Sirene et pagination par curseur.
3. Filtrage par distance autour du local à partir des coordonnées géographiques Sirene.
4. Regroupement analytique des APE en catégories commerciales.
5. Recherche des établissements actifs et fermés à l'adresse exacte.
6. Reconstitution de l'historique détaillé et des liens de succession sur un nombre limité de SIRET.
7. Matching activité / enseignes.

## Important
Les catégories commerciales sont des regroupements analytiques à partir de l'APE/NAF. Elles ne constituent ni une vérification terrain, ni une mesure de demande, ni un diagnostic technique.

## Secrets Streamlit
```toml
SIRENE_API_KEY = "votre_clé"
```
Ne jamais mettre la clé dans GitHub ou dans `app.py`.

## Sources
Les données SIRENE sont fournies par l'Insee et mises à jour quotidiennement. L'API publique permet notamment l'accès aux établissements actifs/fermés, aux variables historisées et aux liens de succession.


## V5.3
Reconstruction de la chronologie à partir des périodes historisées déjà retournées par SIRENE, avec appels API supplémentaires limités aux cas nécessaires.
