# Informations des flux fichiers et API du projet CSIRT

Documentation des données et de leur circulation dans la version alertes uniquement

22 octobre 2026 · Application 0.2.0 · Mode production bridé

Ce document permet de savoir quelles informations nous recevons, où elles sont conservées et comment les consulter. Il décrit les quatre flux RSS, les fichiers utiles du projet, les données SQLite et les sept routes métier de l’API locale.

L’unité de collecte est la publication d’une source. L’incident est une fiche qui rassemble des publications compatibles sur une même alerte. Cette distinction permet de consulter une synthèse sans perdre les textes, dates et liens de chaque source.

## Périmètre actuel

La collecte utilise Bonjour la fuite, Cyberattaque.org, FrenchBreaches et Fuites Infos. Les connecteurs de registres d’entreprises et le matching SIRENE ou RNA ont été retirés. Le regroupement des alertes n’identifie donc ni une entreprise légale, ni son SIREN, ni un siège social.

Le collecteur interroge chaque flux toutes les 30 minutes en fonctionnement normal. Le tableau vérifie la base locale chaque minute lorsqu’il est visible, ainsi qu’au retour sur l’onglet. Une consultation de l’API ne déclenche aucun appel aux sites sources.

## État relevé lors de la rédaction

La base contient 136 incidents et 176 publications après application de la règle stricte de regroupement à moins de 48 heures. Les valeurs suivantes constituent un instantané au 22 octobre 2026 ; la collecte peut ensuite les faire évoluer.

| Source | Publications conservées | Intervalle configuré |
|---|---:|---|
| Bonjour la fuite | 12 | 1 800 secondes |
| Cyberattaque.org | 26 | 1 800 secondes |
| FrenchBreaches | 115 | 1 800 secondes |
| Fuites Infos | 23 | 1 800 secondes |

## Parcours des données

Flux RSS ou Atom → lecture et normalisation → publications en SQLite → regroupement des alertes → API JSON locale → tableau web.

Les pages suivantes distinguent les données reçues de la source, les champs calculés par le projet et les informations purement techniques. Un champ absent reste absent ou prend la valeur null ; nous ne le remplaçons pas par une information supposée.

<!-- page -->

# Les informations reçues dans les flux

## Adresses et particularités des sources

Bonjour la fuite
https://bonjourlafuite.eu.org/feed.xml
Le titre porte généralement le nom cité et un marqueur de statut. Le descriptif peut énumérer les données concernées. L’ancre du lien identifie une alerte précise et doit être conservée.

Cyberattaque org
https://www.cyberattaque.org/feed/
Le titre est un titre d’article. Le nom de l’organisme est extrait seulement lorsque la formulation du titre est explicite. L’extrait RSS peut être court et ne représente pas nécessairement l’article complet.

FrenchBreaches
https://frenchbreaches.com/feed.xml
Le titre est utilisé comme nom cité. Les catégories peuvent qualifier la revendication ou la confirmation. Le flux peut fournir une date de mise à jour en plus de la date de publication.

Fuites Infos
https://fuitesinfos.fr/feed.xml
Le flux fournit des titres d’articles et leurs résumés. Le nom est extrait d’un titre explicite, par exemple avant les deux-points ou dans une formulation de fuite visant un organisme.

## Correspondance des champs principaux

| Élément reçu | Champ du projet | Utilisation |
|---|---|---|
| title | title | Titre de la publication, limité à 500 caractères. |
| link ou lien Atom alternate | url | Lien HTTP ou HTTPS vers la publication. |
| guid ou identifiant Atom | external_id | Identifiant propre à la source ; à défaut, URL. |
| description ou résumé Atom | summary | Texte nettoyé du HTML, limité à 2 000 caractères. |
| pubDate ou published Atom | published_at | Date de publication convertie en UTC. |
| updated Atom | updated_at | Mise à jour déclarée par la source, si présente. |
| category ou catégories Atom | categories | Au plus 30 catégories, 100 caractères chacune. |
| ttl du canal RSS | feed_ttl_seconds | Indication technique du flux, stockée en secondes. |

## Autres informations et limites

Les copies XML locales du 20 octobre 2026 montrent aussi des auteurs, images, pièces jointes, contenus enrichis, mentions de copyright, langues ou dates de construction de flux selon la source. Ces éléments ne sont pas des champs normalisés exploités par l’application actuelle. Leur présence dans un ancien échantillon ne garantit pas leur présence dans chaque publication future.

Le projet ne télécharge pas automatiquement le texte intégral des articles ou les fichiers divulgués. Les volumes de victimes, auteurs d’attaque et lieux peuvent apparaître dans le texte, mais ne disposent pas de champs structurés garantis.

<!-- page -->

# Le dictionnaire des publications

La table observations conserve une ligne par publication de source. Deux publications regroupées dans le même incident restent deux lignes distinctes. Les valeurs de source ne sont pas remplacées par le résumé d’une autre source.

| Champ | Format | Signification |
|---|---|---|
| id | entier | Identifiant local de la publication. |
| incident_id | entier | Fiche d’incident à laquelle la publication est rattachée. |
| source_id | texte | Identifiant stable du flux d’origine. |
| external_id | texte | GUID, identifiant Atom ou URL de repli ; 2 048 caractères au maximum. |
| url | texte | URL normalisée de la publication ; ancre conservée pour Bonjour la fuite. |
| title | texte | Titre nettoyé, au plus 500 caractères. |
| summary | texte | Résumé nettoyé, au plus 2 000 caractères. Il peut déjà être tronqué par la source. |
| published_at | date UTC ou null | Date de publication fournie par le flux. |
| updated_at | date UTC ou null | Date de mise à jour fournie par le flux. |
| organization | texte ou null | Nom cité extrait du titre ; aucune identité légale certifiée. |
| kind | code texte | Type d’événement classé à partir du contenu et des catégories. |
| status | code texte | Qualification éditoriale indiquée par la source. |
| categories | tableau JSON | En SQLite, texte contenant le JSON ; dans l’API, tableau de chaînes. |
| content_hash | texte | Empreinte SHA-256 de la publication normalisée ; non renvoyée par l’API. |
| first_seen_at | date UTC | Première collecte locale de cette publication. |
| last_changed_at | date UTC | Dernière modification de son contenu constatée localement. |

## Types et qualité des informations

kind prend l’une des valeurs data_breach, cyberattack, ransomware, vulnerability ou other. Il s’agit d’une classification automatique du texte, qui peut être imparfaite. Un terme « faille » peut par exemple décrire la cause d’une fuite plutôt qu’une simple alerte de vulnérabilité ; le regroupement tient compte de ce contexte.

status vaut confirmed, claimed, unreliable_claim ou unknown. Les catégories explicites et les marqueurs de titre de la source commandent ce statut. Un simple mot « confirme » dans un résumé ne suffit pas à confirmer l’incident.

Les dates d’un article, de sa mise à jour et de sa collecte sont trois informations différentes. La date de collecte ne décrit pas la date de l’attaque. Sans date valide de publication, le rapprochement automatique par proximité de dates est refusé.

<!-- page -->

# Les incidents et les règles de regroupement

Une ligne de la table incidents correspond à une fiche de synthèse. Les informations propres à chaque source restent dans observations. La fiche conserve les variantes du nom dans ses publications et les expose dans son détail.

| Champ | Format | Signification |
|---|---|---|
| id | entier | Identifiant de la fiche locale. |
| organization | texte ou null | Nom principal conservé pour la fiche. |
| title | texte | Titre conservé pour la fiche. |
| organization_key | texte ou null | Nom normalisé à usage interne. |
| grouping_key | texte ou null | Clé utilisée pour chercher des variantes de nom. |
| grouping_version | texte ou null | Version des règles utilisées pour indexer la fiche. |
| reference_at | date UTC ou null | Plus récente date de publication parmi les publications rattachées. |
| first_seen_at | date UTC | Première observation locale de la fiche. |
| last_changed_at | date UTC | Dernière évolution enregistrée de la fiche ou de son regroupement. |
| activity_seq | entier | Curseur d’activité utilisé pour les nouveautés. |

## Conditions de regroupement

Les variantes reconnues comprennent les espaces, tirets, accents et ligatures, les articles initiaux, certains préfixes descriptifs et les sigles explicitement cités entre parenthèses. Un sigle n’est pas inventé à partir d’initiales. Une région ou une maison mère entre parenthèses ne devient pas automatiquement un autre nom de la victime.

Les publications doivent provenir de sources distinctes, désigner un organisme compatible et décrire un même type d’événement. Les ambiguïtés entre plusieurs organismes restent séparées. Un DDoS et une fuite de données ne sont pas assimilés.

## Limite temporelle stricte

L’écart entre la première et la dernière date de publication de toute la fiche doit être strictement inférieur à 48 heures. À 47 h 59 min 59 s, la condition temporelle est satisfaite ; à 48 h exactement, elle ne l’est plus. Les fuseaux horaires sont normalisés en UTC avant comparaison.

Une date de mise à jour récente ou un contexte commun ne permet aucune exception. Une chaîne de publications espacées chacune de moins de deux jours ne doit pas étendre l’écart total au-delà de cette limite. Les anciennes fiches non conformes sont séparées sans supprimer leurs publications.

## Traçabilité

incident_merges conserve les identifiants retirés, le motif et l’instantané avant fusion. incident_splits conserve les séparations correctives. Les anciens liens sont réaffectés à la fiche de leur publication lorsque l’archive permet de l’identifier. Une sauvegarde précède la correction locale de l’historique.

<!-- page -->

# Les fichiers de configuration et de travail

## Configuration maintenable des sources

Le fichier csirt/sources.toml contient les URL et contrats des flux. Le collecteur le relit à chaque cycle. Un changement d’URL ne nécessite donc pas une modification du code Python. Conserver le même identifiant de source permet de garder son historique.

| Paramètre | Contenu |
|---|---|
| version | Version du format de configuration ; actuellement 1. |
| id et name | Identifiant stable et nom affiché. |
| kind et enabled | Type rss uniquement ; activation de la source. |
| poll_seconds | Intervalle par source ; minimum autorisé de 1 800 secondes. |
| fixed_poll_interval | Intervalle fixe malgré le cache indicatif ou le TTL, hors Retry-After et erreurs. |
| organization_mode | title pour un titre de type nom ; article pour un titre éditorial. |
| request | URL HTTPS, méthode GET ou POST, paramètres, en-têtes et corps JSON éventuel. |
| request.headers_env | Nom de variable d’environnement pour un en-tête secret ; sa valeur n’est pas stockée en configuration. |
| response | Correspondances XML facultatives pour title, url, id, summary, published_at, updated_at et categories. |

La commande python -m csirt.collector --check-config valide le fichier de sources sans lancer de collecte.

## Environnement de fonctionnement

.env.example sert de modèle ; il n’est pas chargé automatiquement par Python. Les variables doivent être exportées ou transmises au processus. Les services du Pi utilisent leur fichier EnvironmentFile.

| Variable | Usage et valeur par défaut |
|---|---|
| CSIRT_DB_PATH | Chemin SQLite ; data/csirt.sqlite3. |
| CSIRT_SOURCES_PATH | Configuration des sources ; csirt/sources.toml. |
| CSIRT_POLL_SECONDS | Intervalle de repli ; 1 800 secondes. Les intervalles explicites du TOML priment. |
| CSIRT_USER_AGENT | Identité HTTP du collecteur ; CSIRT-Veille/0.2. |
| CSIRT_WEB_THREADS | Nombre de threads de travail de l’API ; 4. |

Seul le mode production bridé existe. Les anciennes variables de matching et de débridage sont ignorées ; l’option CLI --mode n’est plus disponible.

<!-- page -->

# Les fichiers de données et les tables techniques

| Fichier ou emplacement | Information contenue et rôle |
|---|---|
| data/csirt.sqlite3 | Base opérationnelle des sources, publications, incidents et traces de regroupement. |
| csirt.sqlite3-wal et csirt.sqlite3-shm | Fichiers SQLite auxiliaires éventuels ; ils participent au fonctionnement WAL. |
| data/backups/ | Sauvegardes de récupération avant changements ; aucune n’est lue comme base active. |
| test.ipynb | Analyse en lecture seule des sources, dates, statuts et regroupements. |
| docs/ | Documentation ; certains anciens fichiers JSON sont des résultats historiques de recherche. |
| .research/ | Échantillons XML et essais locaux historiques, hors fonctionnement courant et hors paquet Pi. |
| dist/csirt-pi2.tar.gz | Paquet de déploiement ; peut contenir un snapshot cohérent de la base. |
| deploy/raspberry/ | Exemples de configuration, installation et services systemd du Pi. |

## État des sources dans SQLite

sources conserve id, name, feed_url, enabled, kind, poll_seconds et la copie normalisée de configuration config_json avec son empreinte config_hash. Les champs last_attempt_at, last_success_at, next_poll_at et retry_not_before décrivent les échéances. failures, last_http_status et last_error décrivent les échecs.

etag, last_modified, body_hash et feed_ttl_seconds servent au cache HTTP. lease_token et lease_until réservent temporairement une collecte pour empêcher deux processus de prendre simultanément la même source. Ces informations ne sont pas des données sur l’incident.

## Autres tables utiles

| Table | Champs et rôle |
|---|---|
| incident_grouping_aliases | incident_id et alias_key ; variantes indexées du nom. |
| incident_grouping_queue | incident_id ; fiches en attente de vérification locale. |
| incident_merges | old_id, incident_id, merged_at, reason, snapshot. |
| incident_splits | id, original_id, new_id, split_at, reason, snapshot. |
| alert_state | id, generation, revision, sequence ; état global des nouveautés. |
| collector_runtime | id, mode, heartbeat_at ; dernier signe d’activité du collecteur. |
| schema_version | version ; historique du schéma principal, actuellement v5. |
| observations_fts | Index FTS5 du titre, du résumé et du nom cité ; recherche textuelle locale. |

## Formats et sauvegardes

Les dates métier sont des textes ISO 8601 en UTC, par exemple 2026-10-22T00:00:00+00:00. Les échéances techniques sont des nombres de secondes Unix. Les catégories et instantanés sont du JSON stocké en texte en SQLite puis décodé dans les réponses concernées.

Pour sauvegarder une base active, utiliser l’API backup de SQLite, comme le script de paquet, plutôt que copier seulement le fichier principal en ignorant le WAL. Une ancienne sauvegarde peut encore contenir l’état antérieur au retrait du matching ; elle ne représente pas les sources actives.

<!-- page -->

# Les routes de consultation API

Base de test locale
http://127.0.0.1:16200

Les sept routes métier sont en GET. Elles consultent les données locales sans forcer une collecte RSS, un regroupement ou une écriture de choix utilisateur.

| Route | Informations renvoyées |
|---|---|
| /health | status, mode et collector avec active, mode et heartbeat_at. |
| /sources | Identifiant, nom, URL, activation, intervalle, dates de tentative et de succès, prochaine échéance, état HTTP, erreur et compteur d’échecs. |
| /dashboard | Bilan des publications entre 17 h la veille et 17 h le jour du bilan, heure de Paris, début inclus et fin exclue. Bascule à 20 h. Renvoie `period` (bornes UTC et locales, fuseau, prochaine bascule), incidents distincts, publications, recoupements et statuts de la période, volumes par source, sources activées, `total_incidents` pour l’historique, état du collecteur et snapshot. |
| POST /reading/updates | Compteur personnel d'alertes non vues, calculé à partir de `generation`, `sequence` et `seen` envoyés par le navigateur. Aucune écriture ou création d'utilisateur côté serveur. |
| POST /reading/incidents | Même état local en corps JSON ; filtres, tri et pagination de `/incidents` appliqués aux alertes non vues. Les acquittements sont pris en compte avant la pagination. |
| /updates | generation, revision, sequence, new_count et collector ; accepte le curseur since. |
| /incidents | total, limit, offset, items et snapshot ; chaque item est une synthèse d’incident. |
| /incidents/{incident_id} | Fiche complète, variantes du nom, publications originales et appréciation agrégée. Résout les anciens identifiants fusionnés. |
| /observations | total, limit, offset et items ; publications individuelles normalisées. |

## Champs ajoutés aux réponses

La liste des incidents ajoute observation_count, sources, grouping et alert_assessment aux champs SQLite de la fiche. Le détail ajoute organization_aliases et observations. Une publication du détail inclut source_name ; ce nom lisible n’est pas ajouté à la liste simple /observations.

content_hash reste interne et n’est pas renvoyé. categories est converti en tableau JSON. Les clés internes de regroupement, grouping_version et activity_seq sont actuellement renvoyées dans les fiches ; elles servent au fonctionnement du projet, pas à certifier une identité.

Le champ grouping vaut single_source ou possible_match. possible_match désigne ici un regroupement de publications ; il ne réactive aucun matching d’entreprise.

## Contrat et erreurs

/docs présente la documentation interactive, /redoc une seconde vue et /openapi.json le contrat OpenAPI. / sert l’interface ; /assets/ ses fichiers statiques.

Un identifiant d’incident inexistant renvoie 404. Un paramètre invalide, une pagination hors limites ou une recherche sans mot exploitable renvoie 422. Le détail JSON d’erreur précise le problème. Les routes de matching, les régions SIREN et le changement de mode n’existent plus.

<!-- page -->

# Les filtres et la pagination API

## Liste des incidents

| Paramètre | Valeur autorisée et effet |
|---|---|
| limit | Entier de 1 à 100 ; 25 par défaut. |
| offset | Entier de 0 à 10 000 ; 0 par défaut. |
| q | Recherche de 1 à 128 caractères dans les textes indexés ou la clé de nom. |
| source | Identifiant d’une source, au plus 80 caractères. |
| alert_status | confirmed, claimed, unreliable_claim, unknown ou mixed. |
| since_sequence | Entier positif ou nul ; garde les fiches dont activity_seq est supérieur. |
| sort | reference_at par défaut ; last_changed_at, activity_seq, organization, alert_status ou source_name. |
| order | desc par défaut ou asc. |

Le tri intervient avant la pagination. organization trie le nom affiché, ou le titre en absence de nom. alert_status trie le libellé français ; source_name trie la première source alphabétique de la fiche. Les tris textuels ignorent la casse et les accents ; les dates absentes restent à la fin dans les deux sens. Les en-têtes du tableau inversent le sens à chaque clic, avec une flèche et un état accessible aux lecteurs d’écran.

Exemple de consultation

```text
GET /incidents?source=frenchbreaches&limit=25&offset=0
GET /incidents?alert_status=claimed&sort=activity_seq
GET /incidents?since_sequence=150
```

## Liste des publications

| Paramètre | Valeur autorisée et effet |
|---|---|
| limit et offset | 1 à 100 et 0 à 10 000 ; limit vaut 50 par défaut. |
| source | Identifiant d’une source, au plus 80 caractères. |
| q | Recherche textuelle de 1 à 128 caractères ; jusqu’à 20 termes reliés par ET. |
| kind | data_breach, cyberattack, ransomware, vulnerability ou other. |
| status | confirmed, claimed, unreliable_claim ou unknown. |
| since et until | Dates bornant published_at, inclusives ; since doit précéder until. |
| sort et order | published_at par défaut ou last_changed_at ; desc ou asc. |

Une date sans fuseau est interprétée en UTC. Les résultats de /observations ne reçoivent pas le statut agrégé mixed : celui-ci appartient à l’incident quand les sources divergent.

```text
GET /observations?source=bonjour-la-fuite&status=confirmed
GET /observations?since=2026-10-22T00:00:00Z&limit=50
```

La réponse fournit total pour calculer le nombre de pages. La page suivante utilise offset + limit. Une URL contenant des espaces, accents ou signes spéciaux doit être encodée par le client HTTP.

<!-- page -->

# Les statuts les indices et les nouveautés

## Appréciation des publications et des incidents

alert_assessment contient status et status_label, un claim_index, les marqueurs disponibles et une attribution à la source. claim_index contient value, label et numeric_score ; numeric_score vaut toujours null. Aucun pourcentage de certitude n’est calculé.

| Code de statut | Lecture attendue |
|---|---|
| confirmed | Confirmé selon la source. |
| claimed | Revendiqué. |
| unreliable_claim | Revendication peu fiable selon la source. |
| unknown | Non renseigné. |
| mixed | Positions divergentes entre sources ; incident uniquement. |

claim_index.value vaut credible, unreliable, mixed, unspecified ou not_applicable. L’absence d’indice publié ne devient pas un indice favorable. independent_verification reste false : le projet retranscrit les qualifications éditoriales, sans vérifier indépendamment l’attaque.

L’appréciation de l’incident ajoute publication_count, counts par statut, divergent et sources avec leurs preuves d’attribution. Des statuts confirmé et revendiqué peuvent coexister ; la fiche retient alors confirmé tout en gardant chaque position. Une revendication peu fiable opposée à une confirmation ou à une revendication produit mixed.

## Mise à jour du tableau sans rechargement

Le navigateur interroge /updates?since=CURSEUR toutes les 60 secondes lorsqu’il est visible. Si revision change, il recharge les données utiles via l’API, conserve les filtres et la pagination, puis met le tableau à jour sur place. Un retour sur l’onglet provoque aussi une vérification.

sequence est le dernier curseur d’activité global. new_count compte les incidents dont activity_seq dépasse le repère de lecture. Une nouvelle publication sur une ancienne fiche ou une modification de contenu peut donc signaler « Nouveau / mis à jour », sans créer nécessairement un nouvel incident.

Le repère est conservé sous la clé locale csirt.alerts.seen.v1 avec generation et sequence. « Tout marquer comme vu » l’avance explicitement. Le repère appartient à ce navigateur et à cette origine, pas à tous les appareils. Une nouvelle base change generation. Sans stockage local disponible, le repère ne survit pas à la fermeture de la page.

## Collecte et charge du serveur

Le collecteur vérifie ses échéances locales toutes les 30 secondes. Il traite les flux séquentiellement, avec 4 Mio par réponse, au plus 500 entrées et un budget de lecture HTTP de 45 secondes. Les erreurs et Retry-After peuvent repousser un passage au-delà de 30 minutes. Les requêtes conditionnelles et empreintes évitent de retraiter un contenu inchangé.

Le regroupement traite au plus 100 fiches par cycle, avec des recherches d’alias indexées et au plus 32 fiches candidates par recherche. Ces contrôles restent locaux et n’ajoutent aucun appel aux sources.

<!-- page -->

# Exemple de réponse et références du projet

## Exemple réduit de réponse incident

L’exemple suivant illustre la forme du JSON et omet certains champs techniques ou détaillés pour rester lisible. Les clés ne représentent pas une identité légale et les dates sont des dates de publication.

```json
{
  "id": 25,
  "organization": "Fédération Française de Tir (FFTIR)",
  "reference_at": "2026-10-17T20:25:00+00:00",
  "grouping": "single_source",
  "organization_aliases": [],
  "observations": [
    {
      "source_id": "frenchbreaches",
      "published_at": "2026-10-17T20:25:00+00:00",
      "updated_at": "2026-10-22T12:03:46+00:00"
    }
  ]
}
```

La publication Bonjour la fuite du 22 octobre est désormais dans une fiche distincte, car l’écart de publication dépasse 48 heures. La mise à jour FrenchBreaches du 22 octobre ne permet pas de contourner cette règle. Les deux publications restent consultables par leur source et leur URL.

## Informations disponibles et informations absentes

Les flux permettent de connaître un titre, un résumé, un nom cité lorsqu’il peut être extrait, des dates, une source, une URL, des catégories et un statut attribué par la source. Le projet ajoute des identifiants, des regroupements, des historiques de collecte et un suivi des nouveautés.

Les données actuelles ne garantissent pas une date d’attaque, un nombre de victimes, un acteur, un SIREN, une adresse ou une région de siège. Ces informations peuvent être citées en texte libre ; elles ne sont pas des colonnes fiables et systématiques de cette version.

## Références locales vérifiées

- csirt/sources.toml et csirt/config.py pour les contrats et limites des sources.
- csirt/feeds.py et csirt/http.py pour la lecture, le nettoyage et les métadonnées HTTP.
- csirt/db.py pour les tables et index SQLite.
- csirt/grouping.py et docs/GROUPING.md pour les règles de regroupement à moins de 48 heures.
- csirt/alert_status.py pour les statuts et indices.
- csirt/api.py et http://127.0.0.1:16200/openapi.json pour le contrat réel de l’API.
- csirt/web/app.js pour le repère de lecture et l’actualisation automatique.

Les volumes ont été vérifiés en lecture seule dans la base locale et les routes contrôlées sur le serveur local. Les particularités XML supplémentaires proviennent des copies d’échantillons datées du 20 octobre 2026 ; aucune collecte supplémentaire des sites n’a été déclenchée pour rédiger ce document.
