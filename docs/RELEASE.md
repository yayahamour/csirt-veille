Installation automatique pour Raspberry Pi 2 Model B v1.1 / Raspberry Pi OS Lite 32 bits Bookworm / Python 3.11.

La version 0.3.0 ajoute l’onglet **Alertes CVE**, alimenté par le CERT-FR et enrichi par NVD, CISA KEV, FIRST EPSS et CVE Program. L’onglet **Incidents** devient l’accueil. L’onglet **Gestion mémoire** affiche la RAM, le volume de la base SQLite et deux calendriers de conservation indépendants. Laisser une date vide conserve les données sans limite.

```bash
curl -fL https://raw.githubusercontent.com/yayahamour/csirt-veille/main/bootstrap.sh -o csirt-install.sh && sudo bash csirt-install.sh
```

Le bootstrap télécharge la dernière version publiée, vérifie son checksum et son manifeste, installe les dépendances et active les deux services. L'adresse locale du site est affichée à la fin. Le système d'exploitation doit déjà être installé sur le Pi et connecté à Internet. Si `curl` est absent : `sudo apt-get update && sudo apt-get install -y curl ca-certificates`.

Mise à jour ultérieure : `sudo csirt-update`. Les configurations et la base existantes sont préservées. Avant remplacement, une sauvegarde est créée ; en cas d'échec de l'installateur, le code, la base et les services antérieurs sont restaurés lorsqu'ils existaient. Les sauvegardes restent disponibles pour récupération.

L’archive publique contient le code et les roues ARM Python 3.11. Une installation neuve crée sa base et collecte les incidents depuis les quatre flux publics, ainsi que les publications du CERT-FR. Python 3.13 utilise les roues disponibles auprès des dépôts. Aucun compilateur n’est lancé sur le Pi.

Les tests logiciels et les contrôles d'archive sont automatisés sur Linux. L'installation et les performances sur le matériel Pi réel restent à valider.
