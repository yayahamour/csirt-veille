# Installer CSIRT sur Raspberry Pi 2 Model B v1.1 — 1 Go

Le paquet fournit l'interface FastAPI, SQLite, le collecteur RSS et les services de démarrage automatique. Il conserve uniquement le rassemblement des alertes, en mode production bridé. Les dépendances Python ARM sont incluses pour **Python 3.11 / Raspberry Pi OS Lite 32 bits Bookworm**.

## Installation automatique depuis GitHub

Le système Raspberry Pi OS doit déjà être installé, démarré et connecté à Internet. Sur le Pi, une fois connecté par SSH, lancer :

```bash
curl -fL https://raw.githubusercontent.com/yayahamour/csirt-veille/main/bootstrap.sh -o csirt-install.sh && sudo bash csirt-install.sh
```

Si `curl` n'est pas présent : `sudo apt-get update && sudo apt-get install -y curl ca-certificates`. La préparation de la carte SD reste à faire avec Raspberry Pi Imager ; le script prend en charge toute l'installation de l'application après démarrage du système.

Le bootstrap vérifie le modèle d'architecture et systemd, installe les prérequis sans questionnaire, télécharge la dernière [version publiée](https://github.com/yayahamour/csirt-veille/releases/latest) et son checksum, vérifie le SHA-256 avant extraction, contrôle le manifeste, puis lance l'installateur. Il affiche l'adresse IP et le port du site après contrôle de l'API et du collecteur. Aucun clone Git, compte GitHub, clé API ou transfert de fichiers n'est nécessaire.

La version publique démarre avec une base vide et collecte les flux à son premier passage. Elle ne contient jamais la base du poste de développement. L'export manuel avec snapshot reste disponible séparément.

Pour les mises à jour suivantes :

```bash
sudo csirt-update
```

Pour installer une version précise : `sudo csirt-update --version v0.2.0`. Une seule installation peut fonctionner à la fois. Les données et réglages sont conservés. La version et le checksum sont vérifiés avant l'arrêt des services. Le bootstrap sauvegarde la base, les configurations et les unités dans `/var/backups/csirt/DATE`, puis conserve l'ancien code avec son environnement dans `/opt/csirt-previous-DATE`. Si l'installateur échoue, il arrête la version en échec, rétablit le code, la base, les unités et les services antérieurs. Le code échoué reste dans `/opt/csirt-failed-DATE` pour diagnostic. Lors d'une première installation en échec, les fichiers de données créés sont conservés pour diagnostic et réessai.

Les sauvegardes ne sont pas supprimées automatiquement : les archiver régulièrement hors de la carte SD. Les prochaines versions sont construites automatiquement sur GitHub après les tests lors de la publication d'un tag correspondant à la version du projet.

## 1. Préparer le Pi

Installer Raspberry Pi OS Lite **32 bits**, version Bookworm avec Python 3.11, sur la carte microSD. Activer SSH, créer votre utilisateur et connecter le Pi au réseau. Prévoir environ 1 Go libre pour le système Python, les environnements et les sauvegardes, en plus du système installé. L'installation initiale utilise Internet pour les paquets système ; la collecte nécessite ensuite un accès HTTPS aux sources.

Le Pi 2 v1.1 utilise ARMv7. Une image 64 bits ne convient pas à ce modèle. Vérifier avant de copier :

```bash
uname -m
python3 --version
```

Résultat attendu pour le paquet fourni : `armv7l` et `Python 3.11.x`. Sur Raspberry Pi OS 32 bits Trixie / Python 3.13, l'installateur peut télécharger des roues compatibles ; elles ne sont pas incluses dans cet export. Il refuse toute compilation de remplacement. Pour une installation reproductible avec les roues jointes, utiliser Bookworm.

Références : [Raspberry Pi OS et versions Lite](https://www.raspberrypi.com/documentation/computers/os.html), [compatibilité des roues ARM piwheels](https://www.piwheels.org/faq.html).

## 2. Copier le paquet depuis Windows

Depuis PowerShell, à la racine du projet, remplacer `utilisateur` et `IP_DU_PI` :

```powershell
scp ".\dist\csirt-pi2.tar.gz" ".\dist\csirt-pi2.tar.gz.sha256" utilisateur@IP_DU_PI:~/
ssh utilisateur@IP_DU_PI
```

Le guide est également disponible dans `dist/README_RASPBERRY_PI.md` et à l'intérieur du paquet.

## 3. Première installation

Sur le Pi, dans la session SSH :

```bash
cd ~
sha256sum -c csirt-pi2.tar.gz.sha256
sudo install -d -m 0755 /opt
sudo tar -xzf csirt-pi2.tar.gz -C /opt
sudo bash /opt/csirt/install.sh
```

Continuer uniquement si le contrôle SHA-256 affiche `OK`. Pour une installation déjà existante, suivre la section mise à jour avant l'extraction.

L'installateur vérifie les fichiers, installe Python/venv et les certificats système, crée l'utilisateur de service `csirt`, puis installe les dépendances figées dans un environnement isolé. Il vérifie SQLite FTS5, les versions et la configuration avant de préparer la base. Les deux services sont activés au démarrage ; le contrôle final attend une API disponible et un collecteur actif.

Si aucune base n'existe sur le Pi, la copie des alertes de l'export est importée. Si une base existe, elle est sauvegardée avant migration et conservée : le paquet ne la remplace pas. Sans snapshot dans l'export, une base vide est créée. Les fichiers de configuration existants sont également conservés.

Ouvrir **`http://IP_DU_PI:8000/`** dans le navigateur. La documentation de l'API est à `/docs` et l'état à `/health`. Le port 16200 utilisé sur le PC devient 8000 sur le Pi. L'interface écoute sur le réseau local ; elle ne comporte pas d'authentification et ne doit pas être publiée directement sur Internet.

Pour installer sans accès aux dépôts, Python 3.11, `python3-venv`, `ca-certificates` et les fuseaux système `tzdata` doivent déjà être présents. Les roues correspondant au Python du Pi doivent être jointes :

```bash
sudo bash /opt/csirt/install.sh --offline
```

Cette option évite aussi `apt`. Elle ne rend pas la collecte RSS utilisable hors ligne.

## 4. Fichiers et réglages

| Chemin sur le Pi | Contenu |
| --- | --- |
| `/opt/csirt` | Code, guide et dépendances du paquet |
| `/opt/csirt/.venv` | Lien vers l'environnement Python actif |
| `/opt/csirt/.venvs/` | Environnements conservés après installation/mise à jour |
| `/etc/csirt/csirt.env` | Variables des deux services |
| `/etc/csirt/sources.toml` | URL des quatre flux, requêtes et correspondances XML |
| `/var/lib/csirt/csirt.sqlite3` | Base opérationnelle ; journaux SQLite voisins possibles |
| `/var/lib/csirt/backups/` | Sauvegardes avant installation et sauvegardes manuelles |

Modifier les flux avec `sudo nano /etc/csirt/sources.toml`. Garder leurs identifiants stables. La configuration est rechargée au prochain cycle ; changer une URL ne nécessite pas de modifier le code. Une erreur de configuration apparaît dans les journaux.

Les paramètres initiaux de `/etc/csirt/csirt.env` sont :

```ini
CSIRT_DB_PATH=/var/lib/csirt/csirt.sqlite3
CSIRT_POLL_SECONDS=1800
CSIRT_USER_AGENT=CSIRT-Veille/0.2
CSIRT_SOURCES_PATH=/etc/csirt/sources.toml
CSIRT_WEB_THREADS=4
```

Conserver la base sous `/var/lib/csirt`. Après modification des variables :

```bash
sudo systemctl restart csirt-web csirt-collector
```

Les sources sont interrogées séquentiellement toutes les **30 minutes**, avec requêtes conditionnelles et délais conservés dans SQLite. Une erreur réseau ou un `Retry-After` peut repousser la collecte. Le réveil interne du collecteur toutes les 30 secondes ne force pas de requêtes supplémentaires. Aucun mode débridé ni connecteur SIRENE/RNA n'est installé.

Le navigateur vérifie les changements chaque minute lorsqu'il est visible et au retour sur l'onglet. Le tableau évolue sans recharger la page. Le suivi « Nouveau / mis à jour » est propre au navigateur et à l'adresse du site : votre première visite sur le Pi crée un nouveau repère de lecture. Les regroupements entre sources conservent une plage totale de publication strictement inférieure à 48 heures.

Les compteurs et graphiques utilisent les dates de publication entre 17 h la veille (incluse) et 17 h le jour du bilan (exclue), en heure de Paris. Le bilan reste affiché jusqu'à 20 h, puis passe à la période suivante en incluant les publications reçues depuis 17 h. Cette bascule est automatique, y compris sans nouvelle collecte, et suit les changements d'heure. Le tableau garde l'historique complet. Les fuseaux horaires sont fournis par le paquet système `tzdata`, installé avec les prérequis.

## 5. Vérifier et surveiller

```bash
systemctl status csirt-web csirt-collector --no-pager
curl -fsS http://127.0.0.1:8000/health
sudo journalctl -u csirt-collector -n 50 --no-pager
sudo journalctl -u csirt-web -n 50 --no-pager
```

L'état attendu contient `"status":"ok"`, `"mode":"production"` et un collecteur `active`. Pour suivre les messages en continu : `sudo journalctl -u csirt-collector -f`.

L'API utilise un worker, quatre threads et 16 requêtes simultanées maximum. Les plafonds systemd sont de 256 Mio pour l'API et 192 Mio pour le collecteur, avec une priorité réduite et un quota CPU de 50 % pour ce dernier. Ce sont des limites configurées, pas une mesure de consommation sur votre Pi. Aucun notebook, Docker ou outil d'analyse n'est lancé par les services.

Les sauvegardes et anciens environnements sont conservés sans suppression automatique. Surveiller `df -h` et les archiver hors de la carte SD. Garder au moins le dernier ensemble code/configuration/base fonctionnel avant de retirer les plus anciens.

## 6. Sauvegarder les données

Cette commande copie SQLite de manière cohérente, même avec l'application active et un journal WAL :

```bash
sudo -u csirt env CSIRT_DB_PATH=/var/lib/csirt/csirt.sqlite3 \
  /opt/csirt/.venv/bin/python /opt/csirt/scripts/pi_admin.py backup \
  "/var/lib/csirt/backups/manual-$(date -u +%Y%m%dT%H%M%S).sqlite3"
```

Le nom de destination doit être nouveau. Adapter `CSIRT_DB_PATH` si vous l'avez modifié. Copier régulièrement ces sauvegardes sur un autre support. Une simple copie du fichier `.sqlite3` d'une base active peut omettre les données de son journal WAL.

## 7. Mettre à jour une installation

Copier le nouveau paquet et son checksum dans le répertoire personnel du Pi. Vérifier le checksum, puis arrêter les services **avant de remplacer le code** :

```bash
cd ~
sha256sum -c csirt-pi2.tar.gz.sha256
sudo systemctl stop csirt-collector csirt-web
sudo -u csirt env CSIRT_DB_PATH=/var/lib/csirt/csirt.sqlite3 \
  /opt/csirt/.venv/bin/python /opt/csirt/scripts/pi_admin.py backup \
  "/var/lib/csirt/backups/manual-$(date -u +%Y%m%dT%H%M%S).sqlite3"
sudo tar --exclude='csirt/.venv' --exclude='csirt/.venvs' --exclude='csirt/.venv-before-*' \
  --exclude='csirt/data' --exclude='csirt/__pycache__' \
  -czf "$HOME/csirt-code-before-$(date -u +%Y%m%dT%H%M%S).tar.gz" -C /opt csirt
sudo tar -czf "$HOME/csirt-config-before-$(date -u +%Y%m%dT%H%M%S).tar.gz" -C /etc csirt
sudo tar -xzf csirt-pi2.tar.gz -C /opt
sudo bash /opt/csirt/install.sh
```

Pour une très ancienne installation sans `scripts/pi_admin.py`, sauvegarder préalablement la base avec l'API `sqlite3.Connection.backup` ou la commande `.backup` de SQLite. Ne passer à l'extraction qu'après cette sauvegarde.

L'installateur garde l'ancien environnement Python, prépare le nouveau, sauvegarde à nouveau la base avant migration et préserve `/etc/csirt`. Si une ancienne configuration contient encore du matching, elle est refusée. Après sauvegarde de la configuration, installer celle à quatre flux puis relancer :

```bash
sudo install -m 0640 -o root -g csirt /opt/csirt/csirt/sources.toml /etc/csirt/sources.toml
sudo bash /opt/csirt/install.sh
```

L'installateur ne supprime pas les anciens stocks nationaux ou archives externes présents sur le Pi. Ils ne sont plus utilisés par l'application.

En cas d'échec, consulter les journaux avant de relancer. Pour revenir à une version précédente, arrêter les deux services, restaurer ensemble le code, la configuration et la sauvegarde SQLite correspondants, puis réinstaller cette version et redémarrer les services. Ne rouvrir une base migrée avec un ancien code qu'après restauration de sa sauvegarde. Une restauration de base doit passer par SQLite et conserver le propriétaire `csirt`, plutôt qu'écraser uniquement le fichier principal en laissant d'anciens journaux WAL.

## 8. Dépannage

| Symptôme | Vérification |
| --- | --- |
| `Python 3.11 minimum` | Installer Bookworm 32 bits avec Python 3.11, pas une ancienne image Bullseye/Python 3.9 |
| Architecture refusée | Vérifier `uname -m` = `armv7l` et un Python 32 bits |
| Roue introuvable ou téléchargement TLS impossible | Vérifier la version Python et le réseau ; les roues du paquet joint ciblent 3.11. Aucune compilation automatique ni désactivation TLS n'est tentée |
| `FTS5` absent | Utiliser le Python/SQLite fourni par Raspberry Pi OS, avec FTS5 |
| Page inaccessible | Vérifier l'IP avec `hostname -I`, le port 8000 et `systemctl status csirt-web` |
| Collecteur inactif | Consulter `journalctl`, les droits de `/var/lib/csirt` et `/etc/csirt`, puis redémarrer le service |
| Pas de nouvelle alerte immédiate | Lire l'échéance et l'erreur de la source dans l'interface ; les délais de collecte survivent aux redémarrages |
| Service tué par manque de mémoire | Consulter `journalctl -k`, fermer les autres programmes et vérifier la place disponible ; ne pas augmenter d'abord les workers |

## 9. Reconstruire un export depuis le PC

À la racine du projet, avec Python 3.11+ et les dépendances de développement installées :

```powershell
python -m pytest -q
python scripts/prepare_pi_wheels.py --python 3.11
python scripts/package_pi.py --with-database --wheelhouse dist/pi-wheels
```

La préparation utilise PyPI et piwheels sans clé API. Pour vérifier des roues déjà téléchargées sans réseau : ajouter `--offline` à `prepare_pi_wheels.py`. Une préparation pour Python 3.13 est possible avec `--python 3.13`, sous réserve de disponibilité des roues ARM auprès des dépôts ; elle n'est pas validée dans le paquet fourni.

Omettre `--with-database` pour démarrer avec une base vide. Omettre `--wheelhouse` pour télécharger les dépendances à l'installation. L'export exclut les environnements Windows, secrets `.env`, stocks nationaux, sauvegardes historiques et dossiers de recherche. Il inclut le code, les configurations d'exemple, les tests, le notebook et la documentation courante.

`EXPORT.json` indique la date de création, la version, les Python couverts par les roues et les nombres d'alertes/publications exportés. `MANIFEST.sha256` couvre chaque fichier du paquet ; `scripts/verify_export.py` le vérifie avant installation. Le fichier `.tar.gz.sha256` vérifie l'archive complète. Pour changer les dépendances, mettre à jour ensemble `requirements.lock`, les roues et les tests ; le paquet refuse un wheelhouse construit pour un autre verrou.

Les tests et contrôles d'archive s'exécutent sur le PC. L'installation complète et les performances doivent encore être vérifiées sur le Raspberry Pi réel.
