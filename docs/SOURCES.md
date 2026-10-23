# Sources RSS

Modifier `csirt/sources.toml` pour changer URL, méthode GET/POST, paramètres, en-têtes, corps JSON éventuel et correspondances XML. Le fichier est rechargé à chaque cycle ; la validation complète précède les accès réseau. Seules les sources de type `rss` (RSS ou Atom) sont acceptées. Ne pas renommer un identifiant existant pour un simple changement d’URL.

| Identifiant | URL | Intervalle |
|---|---|---|
| bonjour-la-fuite | https://bonjourlafuite.eu.org/feed.xml | 30 min |
| cyberattaque | https://www.cyberattaque.org/feed/ | 30 min |
| frenchbreaches | https://frenchbreaches.com/feed.xml | 30 min |
| fuites-infos | https://fuitesinfos.fr/feed.xml | 30 min |

Un changement du contrat HTTP/XML invalide le cache sans effacer les publications, ni avancer l’échéance ou le délai Retry-After. Désactiver une source garde son historique. Les secrets d’en-tête peuvent être référencés par `headers_env`, sans enregistrer leur valeur en base.

La collecte est bornée à 4 Mio, 500 entrées et un budget de lecture HTTP de 45 secondes. Un lot contenant une entrée invalide n’est pas partiellement validé. Les redirections ne sont pas suivies automatiquement. En cas d’erreur, le recul exponentiel et les indications Retry-After priment sur les 30 minutes.
