# Regroupement des publications d’une même alerte

Le regroupement concerne les alertes, sans registre d’entreprises ni source externe. Les noms et publications originaux sont conservés.

## Indices reconnus

- Espaces, tirets, accents, ligatures (« Cœur » / « coeur ») et articles initiaux (« La Compagnie des Sens » / « Compagnie des Sens »).
- Nom accompagné d’une annotation : « PRREL Ambition (Région Hauts-de-France) » / « PRREL Ambition ».
- Sigle cité explicitement entre parenthèses : « Institut national d’études démographiques (Ined) » / « INED ». Aucun sigle n’est inventé à partir des initiales seules.
- Préfixes descriptifs limités : « Société Centrale Canine » / « Centrale Canine ». Le nom restant doit être assez distinctif.

Un nom de propriétaire ou une région entre parenthèses ne devient pas un alias de la victime. Des lieux explicitement différents empêchent la fusion. Deux noms développés différents ne sont pas assimilés parce qu’ils partagent un sigle. Si une proposition a plusieurs alternatives incompatibles, aucune n’est choisie automatiquement.

Le début du résumé peut aussi attester une variante orthographique : si la source nomme explicitement l’organisme dans son accroche et qu’une seule insertion, suppression ou substitution sépare cette variante du nom du titre, elle est indexée. Les noms doivent comporter au moins six caractères. Une simple ressemblance entre deux noms, ou une mention ailleurs dans le texte, ne suffit pas. Par exemple, un titre « Icantec (retraites) » dont le résumé commence par « Ircantec : ... » peut rejoindre une publication nommant Ircantec. Les citations originales restent intactes.

Un service et son organisme de rattachement peuvent être rapprochés lorsque le rattachement est explicitement fourni entre parenthèses par une publication et que l’autre nomme ce service comme sujet des données exposées. Une mention incidente du service, un hébergement ou une région commune ne suffisent pas. Ainsi, une publication « Région Sud » décrivant une base attribuée à ZOU peut rejoindre « Réseau ZOU (Région Sud) ». La fiche affiche alors le nom précis du service, tout en conservant les noms cités par les sources.

## Dates et événement

Les publications doivent provenir de sources distinctes et avoir des dates de publication espacées de strictement moins de 48 heures. À exactement 48 heures, elles restent séparées. Les fuseaux horaires sont convertis en UTC avant comparaison. La date de mise à jour ou de collecte ne remplace jamais la date de publication pour contourner cette limite.

La contrainte porte sur toute la fiche : la dernière publication moins la première doit rester strictement inférieure à 48 heures. Une chaîne de publications rapprochées ne peut pas élargir la fenêtre. Les anciens regroupements qui dépassent ce délai sont séparés, sans supprimer leurs publications. Les anciens liens sont réaffectés à la fiche contenant leur publication quand l’archive le permet.

Un DDoS, une fuite/intrusion et une simple alerte de vulnérabilité sont distingués. Une faille corrigée après une fuite déjà rapportée reste dans l’incident de fuite. Les anciens mélanges manifestes de ces catégories sont séparés et archivés avant nouveau regroupement.

## Traitement et traçabilité

Les variantes sont indexées dans `incident_grouping_aliases`. Une file traite cent incidents au maximum par cycle avec trente-deux fiches candidates au maximum par recherche. Un changement de version recalcule les variantes progressivement ; une consultation web ne déclenche aucun calcul ou accès externe.

Les publications restent intactes. `incident_merges` conserve les identifiants historiques et l’instantané avant fusion ; `incident_splits` conserve les corrections d’anciens mélanges. Les anciens liens pointent vers la fiche regroupée. Une sauvegarde SQLite précède l’application locale des corrections.

Une fusion conserve l’activité la plus récente des fiches : une publication nouvelle reste dans les nouveautés même si elle rejoint une alerte ancienne. Le regroupement reste indicatif ; des événements que les textes ne permettent pas de relier restent séparés.
