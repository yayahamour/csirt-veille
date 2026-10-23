# Collecte en production bridée

Un seul profil est disponible : production. Les flux configurés sont interrogés toutes les 1800 secondes au minimum ; le collecteur vérifie les échéances toutes les 30 secondes. Il n’y a plus de sélecteur de mode dans l’interface, d’API de changement de mode ou d’option CLI de débridage.

Le tableau consulte la base locale toutes les 60 secondes tant qu’il est visible, et lors du retour sur la page. Cette lecture ne provoque aucun accès RSS. Le bouton d’actualisation suit la même règle. Les délais des sources sont persistés en SQLite, y compris après redémarrage ou modification de leur URL.

Une ancienne variable `CSIRT_COLLECTION_MODE` est ignorée. Un appel explicite à `--mode` est refusé par le parseur CLI. Une ancienne configuration contenant des sources d’identification doit être remplacée par le fichier de sources RSS fourni.
