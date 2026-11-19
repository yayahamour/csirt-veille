# Alertes CVE

L’onglet **Alertes CVE** s’ouvre depuis la navigation de l’Observatoire. Démarrer le serveur web et le collecteur avec la même valeur de `CSIRT_DB_PATH`. La première collecte démarre au passage du collecteur. Le bouton **Synchroniser maintenant** dépose une demande persistante prise en compte lors d’un passage du collecteur, y compris après redémarrage. Une nouvelle demande est possible cinq minutes après la fin d’une synchronisation.

## Sources et couverture

- Les alertes et avis utilisent les index et les fiches JSON de l’[API publique du CERT-FR](https://cert.cyber.gouv.fr/openapi.json). Le collecteur traite jusqu’à six fiches par catégorie et par passage. Il remonte progressivement les références récentes et contrôle les révisions des références déjà stockées. Les fiches déjà collectées sont aussi revérifiées au bout de sept jours, selon le quota disponible.
- Les [rapports de menace](https://cert.cyber.gouv.fr/cti/feed/) et les [publications IOC](https://cert.cyber.gouv.fr/ioc/feed/) utilisent les flux RSS par catégorie. Leur couverture et leur détail dépendent des entrées et des champs exposés par le flux. La source officielle reste accessible depuis la fiche pour consulter le contenu complet.
- Les données [NVD](https://nvd.nist.gov/developers/vulnerabilities), [CISA KEV](https://www.cisa.gov/known-exploited-vulnerabilities-catalog), [FIRST EPSS](https://www.first.org/epss/) et [CVE Program](https://www.cve.org/) apparaissent séparément avec la date de récupération. Le catalogue KEV et les scores EPSS sont rafraîchis au plus une fois par jour. NVD et CVE Program sont interrogés par lots d’au plus cinq CVE par source et par passage, avec un cache de sept jours pour les réponses valides.

Une criticité officielle explicite a priorité sur une évaluation interne motivée, suivie du score de base CVSS maximal issu de NVD. Les seuils CVSS sont 9,0 pour « Critique », 7,0 pour « Élevée », 4,0 pour « Moyenne » et un score strictement positif inférieur à 4,0 pour « Faible ». Les champs indéterminés restent vides. L’API CERT-FR documente des risques textuels et ne garantit aucun champ de score ni de criticité.

Les confirmations de correctif, de PoC public et d’exploitation du CERT-FR reposent sur un ensemble conservateur de formulations explicites. Le filtre « Non confirmé » inclut les données indisponibles. Si une confirmation ne peut être reliée de façon univoque à une CVE, elle apparaît au niveau du bulletin. CISA KEV atteste d’une exploitation connue. La présence dans le catalogue ne précise pas si l’exploitation se poursuit. Les versions corrigées et les contournements sont affichés à partir du texte officiel disponible.

## Configuration

| Variable du collecteur | Valeur par défaut | Rôle |
| --- | ---: | --- |
| `CSIRT_ANSSI_POLL_SECONDS` | 3600 | Délai après la fin d’une synchronisation automatique, de 1800 à 604800 secondes. |
| `CSIRT_ANSSI_INDEX_LIMIT` | 250 | Nombre de références récentes candidates au rattrapage par index, de 1 à 5000. Les références déjà en base restent surveillées si elles figurent dans l’index. |

Pour Docker Compose, définir ces valeurs dans l’environnement de Compose ou son fichier `.env`. Pour l’installation native Raspberry Pi, les définir dans `/etc/csirt/csirt.env` et redémarrer le service collecteur. En lancement direct, les exporter avant de lancer le collecteur.

## Stockage, suivi et API

Les tables préfixées `anssi_` sont créées automatiquement dans la base existante. La référence officielle est la clé de la publication. Les produits et les textes structurés sont stockés dans la fiche, les CVE dans une table de liaison. À chaque changement, le collecteur conserve la liste des champs modifiés et la fiche précédente. Il garde les publications sorties des flux ou devenues momentanément inaccessibles. Le suivi CSIRT est stocké à part. Une table de liaison permet de futurs rattachements à une organisation, un secteur ou un actif.

L’API publique locale expose `GET /anssi/summary`, `GET /anssi/publications` et `GET /anssi/publications/{reference}`. Les mutations `POST /anssi/sync` et `PATCH /anssi/publications/{reference}/tracking` requièrent l’en-tête `X-CSIRT-Client: observatoire` pour éviter les requêtes simples interorigines dans un navigateur. L’Observatoire utilise le modèle d’accès partagé de l’instance. Protéger le service par un contrôle d’accès en amont si l’instance est accessible au-delà de ses utilisateurs de confiance.

L’historique des synchronisations conserve leurs dates, le nombre de bulletins ajoutés ou mis à jour et les erreurs. Les requêtes sortantes utilisent des destinations HTTPS fixes et des réponses plafonnées. Un bail SQLite empêche les synchronisations simultanées et la reprise après interruption conserve les fiches et enrichissements déjà enregistrés. Une collecte partielle apparaît dans l’onglet, avec le détail des erreurs.

Pour vérifier le module hors réseau, exécuter `python -m pytest -q tests/test_anssi.py`.
