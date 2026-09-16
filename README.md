# Dés d'Aventure — application Android

Ce dépôt contient une appli Android complète qui embarque ton moteur de dés
(`dice_engine.py`), ton interface (`dice_web.py`) et ton image de fond
(`bg_animorph_data.py`) tels quels, grâce à **Chaquopy** (Python intégré
dans une appli Android).

## Ce que ça change par rapport à avant

Avant : il fallait lancer le script dans Pydroid 3, puis ouvrir manuellement
un navigateur sur `http://127.0.0.1:5001`.

Maintenant : un seul icône sur l'écran d'accueil. En le touchant,
l'application démarre elle-même le serveur en interne (invisible pour toi)
et affiche directement l'interface dans sa propre fenêtre. Aucun navigateur
externe ne s'ouvre — tout est géré par l'appli.

Concrètement :
- `MainActivity.kt` démarre Python au lancement, appelle
  `android_bridge.start_server()`, puis affiche une `WebView` (une fenêtre
  d'affichage web **intégrée à l'appli**, pas le navigateur du téléphone)
  pointée sur le serveur local.
- `android_bridge.py` (nouveau fichier) place la sauvegarde dans le
  stockage interne de l'appli (le seul endroit inscriptible sur Android) et
  démarre `dice_web.py` dans un thread.
- Ta sauvegarde `dice_state.json` fournie est copiée comme état de départ
  au tout premier lancement, pour ne pas repartir de zéro.
- Aucun fichier Python existant n'a été modifié dans sa logique : seule
  cette couche de démarrage a été ajoutée.
- L'icône fournie (`Icon.png`, deux dés) a été déclinée en toutes les
  résolutions nécessaires (`mipmap-mdpi` à `xxxhdpi`), y compris la version
  "icône adaptative" (Android 8+) qui sépare fond et premier plan pour
  s'adapter à la forme (rond, carré, arrondi...) de chaque téléphone. Le
  fond de l'icône reprend la couleur "papier" (`#FBF3E1`) de l'interface.

## Narration automatique (optionnelle)

Une carte "Narration automatique" est apparue sur la page principale. Elle
permet à l'appli d'écrire l'histoire toute seule à chaque lancer, sans
copier-coller manuel, en utilisant l'API gratuite de **Mistral AI**
(entreprise française) :

- Créer un compte gratuit sur [console.mistral.ai](https://console.mistral.ai/)
  (email + mot de passe, **sans carte bancaire**), puis générer une clé API.
- Coller cette clé dans la carte "Narration automatique" de l'appli.
- À partir de là, chaque lancer (et chaque utilisation de jauge
  totémique/allié) est automatiquement envoyé à l'IA, qui répond
  directement dans l'appli — l'histoire s'affiche au fil des lancers.

Détails techniques utiles à savoir :
- **Zéro dépendance ajoutée** : `mistral_client.py` (nouveau fichier)
  utilise uniquement `urllib` (bibliothèque standard Python), comme le
  reste du projet — aucun `pip install` supplémentaire n'était nécessaire
  côté Android, et le script reste utilisable tel quel dans Pydroid 3.
- La clé et toute la conversation avec l'IA sont sauvegardées comme le
  reste de la partie (`dice_state.json` en local sur l'appareil) — rien
  n'est envoyé ailleurs qu'à l'API Mistral officielle.
- Si l'appel à l'IA échoue (pas de réseau, clé invalide, quota du plan
  gratuit atteint...), le lancer de dés n'est **jamais perdu** : seul un
  petit message d'erreur s'affiche, et le bouton "copier le prompt
  complet" (mode manuel) reste disponible en secours.
- Un bouton "Réinitialiser la conversation IA" permet de repartir sur une
  conversation neuve avec le modèle sans toucher au reste de la partie
  (jauges, quêtes, historique des dés) — utile si la conversation devient
  très longue ou part dans une mauvaise direction. Seule une fenêtre
  récente de l'échange est de toute façon renvoyée au modèle à chaque
  appel (l'historique complet, lui, reste affiché et sauvegardé dans
  l'appli).
- Un bouton "Retirer la clé" repasse en mode manuel à tout moment.
- **Démarrer l'aventure sans attendre un lancer** : la carte "Continuer
  l'aventure ailleurs" devient "Démarrer ou relancer un chapitre" avec un
  bouton "Envoyer le prompt à l'IA" — il transmet mécaniques + histoire déjà
  vécue (s'il y en a) et demande explicitement au modèle de commencer le
  prochain chapitre. Pratique pour planter le décor avant le tout premier
  lancer, ou pour relancer le fil après un "Réinitialiser la conversation IA".
- **Message libre à l'IA** : un champ de texte dans la carte "Narration
  automatique" permet d'envoyer n'importe quel message au narrateur à tout
  moment (décrire une action de Gabin entre deux lancers, préciser un
  détail...), pas seulement les résultats de dés.
- **Univers Marvel** : le contexte envoyé à l'IA précise que l'aventure se
  déroule dans l'univers Marvel — le narrateur peut y faire intervenir
  d'autres personnages Marvel au fil de l'histoire (nouvelles rencontres,
  alliances ponctuelles), en plus des trois alliés déjà liés à un symbole
  (Araignée=Spider-Man, Bouclier=Captain America, Étoile=Shuri).
- **Les 6 totems animaux sont acquis dès le début** (Aigle, Loup, Renard,
  Jaguar, Grand Bond, Profondeurs) — ce ne sont pas des découvertes à venir,
  Gabin les a déjà tous au départ.
- **De nouveaux totems peuvent apparaître au fil de l'aventure**, et le jeu a
  déjà tout ce qu'il faut pour ça : quand un "❓" du dé du destin débloque une
  quête secondaire de type "objet", c'est le signal envoyé à l'IA d'inventer
  la rencontre d'un nouveau totem (nom, apparence, pouvoirs) et de la
  raconter dans le chapitre. Une fois le chapitre terminé, **c'est toi qui
  l'ajoutes dans l'application** via le formulaire de la carte "Jauges
  totémiques" — nom, pouvoirs, capacité spéciale (optionnelle), et soit un
  emoji tapé à la main, soit **une image que tu uploades toi-même** (stockée
  en local dans un dossier `totem_images/` à côté de `dice_state.json`,
  jamais envoyée ailleurs qu'au modèle sous forme de texte). Il obtient
  aussitôt sa propre jauge, apparaît dans le sélecteur de symboles, et est
  automatiquement inclus dans le contexte envoyé à l'IA à partir de ce
  moment — sans réécrire les chapitres précédents où il n'existait pas
  encore. La fréquence reste donc entièrement entre tes mains : l'IA
  invente la rencontre dans l'histoire, mais seule ta validation manuelle
  lui donne une existence mécanique (jauge, pouvoirs suivis par l'appli).
  Un bouton permet de
  retirer un totem ajouté par erreur (jauge et image supprimées avec).

## Port réseau interne différent de l'original

Chaque version de l'appli fait tourner un petit serveur local sur
`127.0.0.1` pour s'afficher elle-même — c'est invisible pour toi, mais
important à savoir : **`127.0.0.1` est partagé par tout le téléphone**, pas
cloisonné par application comme le reste (stockage, mémoire...). Si deux
applications différentes utilisent le même port et tournent toutes les deux
en arrière-plan en même temps, la première à avoir démarré garde le port et
la seconde reste bloquée — elle donne l'impression de ne plus s'ouvrir.

Cette version "Auto" utilise donc le port **5011**, différent de celui de la
version d'origine (**5001**), pour que les deux applications puissent
cohabiter sur le même téléphone sans jamais se gêner, même laissées
ouvertes toutes les deux en fond. Si tu crées encore d'autres variantes à
l'avenir (voir `android_bridge.py` et `MainActivity.kt`, tous deux à
modifier ensemble), pense à leur donner chacune un port différent.

## Nom de l'application et icône

Cette version s'appelle maintenant **"Histoires Multiples"** (elle a eu
plusieurs noms au fil des versions : "Dés d'Aventure", puis "Dés
d'Aventure Auto") et garde son identifiant d'application propre
(`com.aventure.desdice.auto`, inchangé pour ne pas perdre la partie en
cours). C'est cet identifiant qui compte vraiment pour Android : c'est
lui qui décide si une appli est "la même" (et donc mise à jour) ou une
appli différente (installée en plus) — tu gardes donc tes autres
versions installées à côté sans que l'une écrase l'autre.

L'icône a aussi été remplacée par l'image que tu as fournie (les deux
personnages, Animorph et Poudlard, côte à côte) — recadrée en carré et
déclinée dans toutes les résolutions Android nécessaires.

## Plusieurs histoires dans la même application

Un écran de sélection apparaît maintenant au lancement : **Animorph**
(l'histoire d'origine) et **Poudlard** (nouvelle histoire, univers Harry
Potter). Un lien "🔀 Changer d'histoire" en haut de l'écran de jeu permet
d'en changer à tout moment, sans rien perdre.

Points clés :
- **Sauvegardes totalement séparées** (`dice_state_animorph.json` et
  `dice_state_poudlard.json`) : les dés, jauges, totems et quêtes de l'une
  n'ont aucune influence sur l'autre. Testé explicitement : ajouter un
  totem, lancer des dés, ou faire un reset complet sur une histoire ne
  touche jamais l'autre.
- **Clé Mistral partagée** : une seule fois collée, elle fonctionne pour
  les deux histoires (stockée à part, dans `app_config.json`, au niveau
  de l'application plutôt que dans la sauvegarde d'une histoire).
- **Poudlard part presque de zéro** : un seul totem de départ, la
  **Baguette Magique** (récupérée dès l'arrivée à Poudlard), dont la
  jauge pleine permet de relancer le dernier lancer de réussite — comme
  la Patte d'Animorph, mais avec une règle en plus : quand cette jauge
  est pleine, l'IA doit explicitement demander au joueur s'il veut
  l'utiliser *avant* de continuer l'histoire, plutôt que d'enchaîner
  directement sur un mauvais résultat.
- **Tout le reste se construit en jouant** : amis, créatures fantastiques
  et sorts appris en cours (avec une vraie mécanique pédagogique : l'IA
  enseigne un fait scientifique réel, pose une question, et le sort
  débloqué en cas de bonne réponse en découle logiquement — par exemple,
  l'air chaud qui monte menant au sort de lévitation Wingardium Leviosa).
  Comme pour les totems Animorph, l'IA invente la rencontre dans
  l'histoire ; c'est toi qui l'ajoutes ensuite dans l'application via le
  formulaire existant.
- Ajouter une troisième histoire plus tard ne nécessite de toucher qu'un
  seul fichier : `stories.py`, qui centralise tout ce qui différencie une
  histoire d'une autre (image de fond, sauvegarde, roster de totems de
  départ, texte d'univers envoyé à l'IA).

## Étape 1 — Créer le dépôt GitHub

1. Va sur [github.com/new](https://github.com/new) et crée un nouveau
   dépôt (public ou privé, peu importe), par exemple `des-aventure-app`.
   Ne coche aucune case (pas de README, pas de licence) : le dépôt doit
   être vide.
2. Sur ton ordinateur, dans le dossier de ce projet, exécute :

   ```bash
   git init
   git add .
   git commit -m "Première version de l'appli Android"
   git branch -M main
   git remote add origin https://github.com/<ton-compte>/des-aventure-app.git
   git push -u origin main
   ```

## Étape 2 — Laisser GitHub construire l'APK

Dès que le code est poussé sur la branche `main`, l'onglet **Actions** du
dépôt GitHub lance automatiquement le workflow `Build APK`
(`.github/workflows/build-apk.yml`). Il installe le SDK Android et Gradle,
compile le projet, puis met l'APK à disposition.

- Va dans l'onglet **Actions** du dépôt.
- Clique sur l'exécution la plus récente de "Build APK".
- En bas de la page, dans **Artifacts**, télécharge `des-aventure-apk`
  (un fichier `.zip` contenant `app-debug.apk`).

Si tu modifies le code plus tard et veux relancer un build sans nouveau
`push`, utilise le bouton **Run workflow** (déclenchement manuel) dans
l'onglet Actions.

## Étape 3 — Installer l'APK sur ton téléphone

1. Transfère `app-debug.apk` sur ton téléphone (câble, Drive, etc.).
2. Ouvre le fichier depuis le téléphone. Android demandera d'autoriser
   "l'installation d'applications inconnues" pour l'application utilisée
   pour ouvrir le fichier (ex. Fichiers, Chrome) — accepte pour cette
   installation.
3. Installe. Une icône "Dés d'Aventure" apparaît sur l'écran d'accueil.

## En cas d'échec du build sur GitHub

C'est un projet Android avec Python embarqué (Chaquopy) : la compilation
est plus lourde qu'une appli classique, et les versions des outils
(Chaquopy, Android Gradle Plugin, Gradle) évoluent avec le temps. Si le
workflow échoue :

1. Ouvre le détail de l'étape qui a échoué dans l'onglet Actions pour lire
   le message d'erreur exact.
2. Le plus souvent, c'est une histoire de versions à ajuster :
   - version de Chaquopy dans `build.gradle` (racine) et `app/build.gradle`
   - version d'Android Gradle Plugin dans `build.gradle` (racine)
   - version de Gradle dans le workflow (`gradle-version`)
   - la page officielle [chaquo.com/chaquopy/doc/current/versions.html](https://chaquo.com/chaquopy/doc/current/versions.html)
     donne les combinaisons compatibles.
3. Tu peux aussi coller le message d'erreur ici dans la conversation, je
   pourrai ajuster les fichiers en conséquence.

## Structure du projet

```
des-aventure-app/
├── .github/workflows/build-apk.yml   # construit l'APK automatiquement
├── build.gradle                      # plugins Android/Kotlin/Chaquopy
├── settings.gradle
├── gradle.properties
├── gradle/wrapper/gradle-wrapper.properties
└── app/
    ├── build.gradle                  # config Android + dépendance "flask" via pip
    ├── proguard-rules.pro
    └── src/main/
        ├── AndroidManifest.xml
        ├── java/com/aventure/desdice/MainActivity.kt
        ├── res/                      # nom de l'appli, layout, icônes (mipmap-*)
        └── python/
            ├── dice_engine.py        # ton moteur, inchangé (+ narration auto)
            ├── dice_engine.py        # moteur de jeu (partage par toutes les histoires)
            ├── dice_web.py           # interface Flask (partagee par toutes les histoires)
            ├── stories.py            # registre des histoires (Animorph, Poudlard, ...)
            ├── mistral_client.py     # client API Mistral (stdlib pure)
            ├── bg_animorph_data.py   # image de fond -- histoire Animorph
            ├── bg_poudlard_data.py   # image de fond -- histoire Poudlard
            ├── android_bridge.py     # demarre le serveur en interne
            └── dice_state_seed.json  # sauvegarde de depart d'Animorph (1er lancement)
```

## Ouvrir le projet dans Android Studio (optionnel)

Si tu veux modifier le projet toi-même avant de le repousser sur GitHub :
ouvre le dossier avec Android Studio (menu *Open*). Au premier lancement,
Android Studio proposera de régénérer le fichier `gradle-wrapper.jar`
manquant (nécessaire seulement en local, pas pour le build GitHub) —
accepte, ou lance `gradle wrapper --gradle-version 8.7` une fois si tu as
Gradle installé sur ta machine.
