# Local Matcher V3

Version V3 : première connexion réelle à l'API Sirene 3.11 de l'Insee.

## 1. Déploiement Streamlit

Le projet utilise la clé stockée dans Streamlit Secrets :

```toml
SIRENE_API_KEY = "votre_cle"
```

Ne jamais mettre la clé dans GitHub ou dans `app.py`.

## 2. Utilisation

1. Saisir une **adresse complète**, idéalement avec code postal et ville.
2. Cliquer sur **Rechercher les établissements à cette adresse**.
3. Les établissements actifs et fermés trouvés dans SIRENE sont affichés.
4. Sélectionner un établissement fermé comme ancien occupant.
5. Consulter son historique de périodes et son APE.
6. Utiliser l'activité du matching pour obtenir les activités et enseignes compatibles.

## 3. Limites V3

- Une adresse SIRENE ne constitue pas une preuve cadastrale de propriété.
- L'APE est une classification d'activité et ne prouve pas l'existence d'une extraction, d'une chambre froide ou d'une puissance électrique donnée.
- Les données SIRENE sont mises à jour quotidiennement, mais l'historique et les données d'établissement ne remplacent pas un diagnostic technique ou foncier.
- Le quota de l'accès public est de 30 interrogations/minute.

## 4. Prochaine brique

Connecter l'adresse à la parcelle cadastrale puis aux données de propriété des personnes morales, avec SIREN lorsque disponible.
