# Déploiement Raspberry Pi 2 Model B v1.1 — 1 Go

Le guide complet est maintenu dans [README_RASPBERRY_PI.md](../README_RASPBERRY_PI.md).

Il décrit Raspberry Pi OS Lite 32 bits / Python 3.11, le transfert de `dist/csirt-pi2.tar.gz`, le contrôle SHA-256, l’installateur `sudo bash /opt/csirt/install.sh`, les services systemd, les réglages, les sauvegardes et les mises à jour.

L’API utilise un worker et le collecteur traite les quatre flux RSS en mode production bridé, toutes les 30 minutes. L’interface est accessible sur `http://IP_DU_PI:8000/`.
