# Alertes Vinted

Scripts Python qui surveillent Vinted et des fiches produit selon mes critères (titre, prix) , et envoient une notification
[ntfy](https://ntfy.sh) sur le téléphone dès qu'une annonce intéressante apparaît. Ils n'achètent rien.

- `alerte_vinted.py` : jeux Nintendo Switch sur Vinted (liste dans `recherches.txt`)
- `alerte_dvd.py` : DVD et Blu-ray sur Vinted (liste dans `films.txt`)
- `alerte_pochette.py` : stock d'un produit en magasin (pages dans `produits.txt`)

## Utilisation

Python 3.8+, aucune dépendance. Installez l'appli ntfy et abonnez-vous à un canal au nom difficile à deviner.

```bash
python3 alerte_vinted.py --ntfy MON-CANAL --test-alerte   # tester la notification
python3 alerte_vinted.py --ntfy MON-CANAL                 # lancer la surveillance
```

`./alertes.sh start|stop|status` lance les scripts en arrière-plan dans tmux.

Les listes se modifient sans relancer : une ligne par jeu ou film, avec titres acceptés, prix max et mots exclus.

## Bon usage

Pour un usage personnel : les scripts restent lents (une requête toutes les 20 à 60 s), ne se connectent
à aucun compte et ne contournent pas les blocages. Vérifiez toujours l'annonce avant d'acheter.
