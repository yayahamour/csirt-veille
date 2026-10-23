# CSIRT — rassemblement des alertes

Application Python/FastAPI + SQLite, avec interface web locale. La collecte et le regroupement des alertes sont les seules fonctions de traitement. Les connecteurs d’identification, registres d’entreprises et matching ont été retirés.

## Installation automatique sur Raspberry Pi

Sur un Pi 2 v1.1 avec **Raspberry Pi OS Lite 32 bits Bookworm**, Internet et `curl` :

```bash
curl -fL https://raw.githubusercontent.com/yayahamour/csirt-veille/main/bootstrap.sh -o csirt-install.sh && sudo bash csirt-install.sh
```

Le téléchargement, les contrôles d'intégrité, les prérequis, la base et le démarrage automatique sont pris en charge. L'adresse du site s'affiche en fin d'installation. La commande `sudo csirt-update` installe ensuite la dernière version publiée en sauvegardant les données et la configuration. Si `curl` manque : `sudo apt-get update && sudo apt-get install -y curl ca-certificates`.

Les [versions publiques](https://github.com/yayahamour/csirt-veille/releases/latest) sont sans base locale. Le guide [README_RASPBERRY_PI.md](README_RASPBERRY_PI.md) détaille le fonctionnement et la récupération en cas d'échec. Les tests s'exécutent à chaque push ; un tag `vX.Y.Z` correspondant à `pyproject.toml` déclenche aussi la construction et la publication de l'archive ARM sans données locales.

**Export Raspberry Pi 2 (1 Go)** : le guide [README_RASPBERRY_PI.md](README_RASPBERRY_PI.md) explique la copie du paquet, l'installation, les sauvegardes et les mises à jour. Le paquet est généré dans `dist/csirt-pi2.tar.gz`, avec son checksum et les dépendances ARM pour Python 3.11 lorsqu'elles sont jointes.

## Démarrer

Python 3.11 ou supérieur, SQLite avec FTS5 :

```powershell
python -m pip install -e '.[test]'
python -m csirt.collector --check-config
python -m csirt.collector
```

Dans un second terminal :

```powershell
python -m uvicorn csirt.api:app --host 127.0.0.1 --port 16200 --workers 1 --loop asyncio --http h11 --limit-concurrency 16 --no-access-log
```

Interface : http://127.0.0.1:16200/ ; documentation API : /docs.

## Collecte sobre

Quatre flux publics : Bonjour la fuite, Cyberattaque.org, FrenchBreaches, Fuites Infos. Configuration et correspondances des champs dans `csirt/sources.toml`, rechargée à chaque cycle. Les identifiants des sources doivent rester stables.

Seul le mode production bridé existe. Chaque flux est interrogé toutes les 30 minutes, avec une seule requête à la fois, taille et durée plafonnées, déduplication du contenu et requêtes conditionnelles ETag/Last-Modified. Les erreurs et Retry-After peuvent reporter un passage. Un redémarrage conserve ces délais. Aucun appel aux sources depuis le navigateur ou les routes de consultation.

`python -m csirt.collector --once` traite uniquement les sources arrivées à échéance. Aucune option de débridage ; une ancienne variable `CSIRT_COLLECTION_MODE` est ignorée.

## Nouveautés et tableau automatique

Les compteurs d'incidents, de publications et de recoupements, ainsi que les graphiques, portent sur les publications comprises entre **17 h la veille (incluse) et 17 h le jour du bilan (exclue)**, en heure de Paris. Le bilan reste affiché jusqu'à **20 h**, puis la période suivante prend le relais, en incluant les publications intervenues depuis 17 h. Le changement s'affiche lors de la vérification automatique chaque minute, même sans nouvelle collecte. Les changements d'heure été/hiver sont pris en compte. Une alerte regroupée compte une seule fois si au moins une de ses publications appartient à la période ; les recoupements et statuts du bilan utilisent uniquement les publications de cette période. Les publications sans date sont exclues des compteurs de période. Le tableau conserve tout l'historique, le repère de nouveautés reste propre au navigateur, et le compteur de sources indique toujours le nombre de flux activés.

Les en-têtes Organisme, Dernière publication, Statut et Sources sont cliquables. Un second clic inverse le sens du tri ; les noms sont classés sans distinguer accents et casse. Le tri porte sur tous les résultats filtrés avant pagination, reste dans l’URL et survit aux actualisations automatiques. Pour une fiche ayant plusieurs sources, le tri utilise la première source dans l’ordre alphabétique.


Le navigateur consulte `/updates` chaque minute lorsque la page est visible. Si les données changent, le tableau se met à jour sur place en conservant ses filtres et sa pagination. Un retour sur l’onglet déclenche aussi une vérification. « Actualiser le tableau » consulte les données locales sans forcer une collecte RSS.

À la première visite, l’historique existant sert de point de départ. Ensuite, un incident nouvellement collecté, une publication supplémentaire ou une modification de publication reçoit le repère « Nouveau / mis à jour ». Le filtre « Nouveautés uniquement » les isole. Le bouton « Tout marquer comme vu » avance explicitement le repère de lecture. Il est conservé dans le stockage local de ce navigateur, y compris après actualisation et fermeture de l’onglet ; les onglets du même site synchronisent ce repère. Il n’est pas partagé entre appareils ou origines. Si le stockage est interdit ou effacé, le suivi repart à la visite suivante. Une nouvelle base est détectée par son identifiant propre.

Les statuts confirmé/revendiqué et indices retranscrivent ce que publient les sources. Ils ne constituent pas une vérification indépendante. Les regroupements utilisent les variantes de nom, les sigles explicitement cités et le type d’événement entre sources distinctes. L’écart total des dates de publication doit être strictement inférieur à 48 heures, sans exception fondée sur une mise à jour ou la date de collecte. Un DDoS et une fuite de données restent séparés.

## Base et migration

`data/csirt.sqlite3` est la seule base opérationnelle. La migration v5 conserve incidents, publications, identifiants historiques et échéances HTTP, puis supprime les tables de matching. Avant la migration locale, une sauvegarde de récupération a été placée dans `data/backups/before-alerts-only.sqlite3` ; elle contient l’ancien état et n’est jamais chargée par l’application. Les anciens fichiers de recherche dans `.research/` sont des archives de travail hors application et hors paquet Raspberry Pi.

Les anciens stocks locaux SIRENE/RNA ne sont plus nécessaires. Sur une installation déjà déployée, sauvegarder la base avant mise à jour, remplacer `sources.toml` par la version à quatre flux, puis redémarrer les deux services. Une ancienne configuration contenant des sources de matching est refusée avant tout appel réseau.

## Vérifier et analyser

```powershell
python -m pytest -q
python scripts/prepare_pi_wheels.py --python 3.11
python scripts/package_pi.py --with-database --wheelhouse dist/pi-wheels
```

Le notebook `test.ipynb` analyse les sources, statuts, dates et regroupements en lecture seule, sans collecte réseau. Dépendances facultatives : `python -m pip install -e '.[analysis]'`.

Installation native Pi 2 : voir [README_RASPBERRY_PI.md](README_RASPBERRY_PI.md). Aucun registre national ou moteur de matching n’est chargé en mémoire.
