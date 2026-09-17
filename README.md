# Local Matcher V5.3.2

Version V5.3.2 : géolocalisation du local, analyse d'un rayon commercial, regroupement des établissements par grandes catégories, historique SIRENE à l'adresse exacte, chronologie historisée et liens de succession, puis matching activité / enseignes.

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


## V7 — Reconstitution des locaux
V7 ajoute une vue locale centrée sur l'adresse : les établissements actifs et fermés sont regroupés par adresse SIRENE afin de distinguer les locaux occupés, les signaux de vacance potentielle et les historiques anciens. Cette reconstruction est une approximation analytique : une adresse SIRENE n'est ni une parcelle cadastrale ni une unité locative et doit être vérifiée sur le terrain.

## V9 — Ancrage cadastral

V9 ajoute une première brique de croisement SIRENE + référentiel géographique/cadastral :
- l'adresse est géocodée par le service Géoplateforme ;
- le point géocodé est rapproché de la parcelle cadastrale la plus proche ;
- la parcelle, section et numéro sont affichés et ajoutés à l'export ;
- cette donnée sert d'ancrage physique du local, sans être assimilée à une preuve de propriété.

La prochaine étape pourra croiser cet identifiant cadastral avec les fichiers des personnes morales propriétaires afin de remonter vers une SCI/foncière lorsque la donnée est disponible.

## V10.3 — DVF+ open-data Cerema

La brique DVF utilise désormais l'API ouverte DVF+ du Cerema. Local Matcher interroge une petite emprise autour du point géocodé, puis filtre les mutations sur l'identifiant cadastral de la parcelle cible. Cette approche remplace la dépendance à la micro-API Cquest.

Le signal DVF+ reste un signal de transaction : il ne permet pas, à lui seul, d'identifier le propriétaire actuel ni le nom de l'acquéreur.
