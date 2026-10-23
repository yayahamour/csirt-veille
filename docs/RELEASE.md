Installation automatique pour Raspberry Pi 2 Model B v1.1 / Raspberry Pi OS Lite 32 bits Bookworm / Python 3.11.

La version 0.2.1 corrige l'installation depuis un dossier extrait ou cloné : `sudo ./install.sh` place automatiquement le projet dans `/opt/csirt`. Les dossiers imbriqués et les noms contenant des espaces sont pris en charge. L'ancien dossier, les données et la configuration sont préservés ou sauvegardés.

```bash
curl -fL https://raw.githubusercontent.com/yayahamour/csirt-veille/main/bootstrap.sh -o csirt-install.sh && sudo bash csirt-install.sh
```

Le bootstrap télécharge la dernière version publiée, vérifie son checksum et son manifeste, installe les dépendances et active les deux services. L'adresse locale du site est affichée à la fin. Le système d'exploitation doit déjà être installé sur le Pi et connecté à Internet. Si `curl` est absent : `sudo apt-get update && sudo apt-get install -y curl ca-certificates`.

Mise à jour ultérieure : `sudo csirt-update`. Les configurations et la base existantes sont préservées. Avant remplacement, une sauvegarde est créée ; en cas d'échec de l'installateur, le code, la base et les services antérieurs sont restaurés lorsqu'ils existaient. Les sauvegardes restent disponibles pour récupération.

L'archive publique ne contient aucune base locale. Une installation neuve collecte ses alertes depuis les quatre flux publics. Les roues ARM Python 3.11 sont incluses ; Python 3.13 nécessite les roues disponibles auprès des dépôts. Aucun compilateur n'est lancé sur le Pi.

Les tests logiciels et les contrôles d'archive sont automatisés sur Linux. L'installation et les performances sur le matériel Pi réel restent à valider.
