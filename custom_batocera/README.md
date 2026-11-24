# Images Batocera personnalisées

Générateur hors ligne pour images officielles **v44 x86_64 et Raspberry Pi 3**.
Profil commun, deux images de sortie. Exécution sous Linux ; aucun binaire ARM
n'est exécuté sur le poste de génération.

## Préparation

Installer outils, par exemple sur Debian/Ubuntu récent :

```bash
sudo apt install python3 squashfs-tools util-linux fdisk gdisk e2fsprogs rsync
mkdir -p base roms bios splash
```

Utiliser e2fsprogs 1.47 ou ultérieur pour les fonctionnalités ext4 récentes.
Prévoir plusieurs dizaines de Go libres pour image brute, système extrait et
compression. Les architectures sont traitées successivement.

Télécharger les images **v44** depuis <https://batocera.org/download>.
Les déposer sous les noms déclarés dans `profile.json`, ou adapter les chemins.
Éviter une URL `stable/last` non figée : elle peut changer de version.

```bash
sha256sum base/*.img.gz
```

Reporter chaque empreinte dans `images.<architecture>.sha256`. Cela verrouille
les entrées ; comparer aussi aux empreintes officielles lorsqu'elles sont fournies.

Dans `profile.json`, renseigner les chemins des dossiers ROMs/BIOS et médias.
Chemins relatifs résolus par rapport au profil ; chemins absolus acceptés.

```text
roms/snes/jeu.sfc
roms/megadrive/jeu.md
bios/...
splash/boot-logo.png
splash/intro.mp4
splash/es-loading.png
```

Conserver structure des dossiers BIOS attendue par chaque émulateur.
Les métadonnées `gamelist.xml` et médias dans ROMs sont copiés aussi ; utiliser
chemins relatifs ou chemins `/userdata/roms/...`. Les liens externes aux dossiers
sources sont refusés ; liens internes sont copiés comme fichiers.

PNG pour les deux images. Pour vidéo commune PC/Pi 3 : MP4 H.264, yuv420p,
720p à 30 images/s conseillé. Générateur vérifie conteneur, pas codec ni lecture.
Il place un seul MP4 dans `splash` pour éviter sélection aléatoire.

## Génération

```bash
python3 build_image.py --check
sudo python3 build_image.py --arch all
```

Une seule architecture :

```bash
sudo python3 build_image.py --arch rpi3
```

Sorties :

```text
output/batocera-x86_64-44-custom.img.gz
output/batocera-rpi3-44-custom.img.gz
```

Chaque sortie possède un manifeste JSON avec profil, version, empreinte base,
empreintes médias et empreinte image finale. Pour régénérer après modification,
déplacer anciennes sorties et manifestes, puis relancer. Images sources conservées.
Génération reproductible depuis mêmes entrées, mais résultat non garanti identique
octet pour octet : timestamps et outils système peuvent varier.

## Taille et premier démarrage

Taille brute configurée à **14 000 000 000 octets**, inférieure à 15 Go décimaux.
Le script agrandit SHARE ext4 avant injection. Si contenus ne tiennent pas,
génération échoue au lieu de publier une image incomplète.

La partition BOOT du Pi 3 v44 est configurée à **8 Gio** dans sources officielles.
La taille d'une ancienne installation de 8 Go ne suffit donc pas à garantir place
disponible sur v44. Documentation Batocera annonce supports de 16 Go minimum ;
14 Go est une contrainte de ce générateur, à valider avec tes contenus.

**Agrandissement automatique désactivé volontairement (`autoresize=false`).**
Dans `S02resize` du tag v44, les SHARE initiales de 256/512 Mio peuvent être
reformatées. Pour une SHARE déjà agrandie, script agrandit partition mais ne lance
pas `resize2fs`. Générateur agrandit donc partition et système de fichiers hors
ligne, puis désactive ce mécanisme. Sur support plus grand, espace au-delà de
l'image reste non alloué. Agrandir ensuite avec GParted, hors ligne, si nécessaire.

## Personnalisations appliquées

- Français : `system.language=fr_FR` dans `system/batocera.conf`.
- Orange : `ThemeColorSet=orange` dans
  `system/configs/emulationstation/es_settings.cfg` (XML malgré extension `.cfg`).
- Premier logo : `/usr/share/batocera/splash/boot-logo.png` dans SquashFS.
- Vidéo : `/userdata/splash/intro.mp4`.
- Chargement EmulationStation : `/userdata/system/splash/es-loading.png`,
  avec `es.customsargs=--splash-image /userdata/system/splash/es-loading.png`.

`AlternateSplashScreen` n'est pas conservé par sauvegarde des réglages ES ;
argument de lancement assure persistance. Les réglages/médias sont également
injectés dans squelette `datainit`, pour initialisation compatible au premier boot.
Les autres réglages par défaut sont conservés. Pas de `machine-id`, clé SSH,
réglage écran ou manette provenant d'une machine déjà installée.

SquashFS reconstruit en conservant type de compression, taille de bloc,
permissions et attributs étendus ; options fines du compresseur peuvent différer.
Une mise à jour officielle remplace système personnalisé, donc premier logo et
squelette. Régénérer depuis nouvelle image officielle pour déploiement actualisé.

## Validation et déploiement

Flasher image correspondant au matériel avec Raspberry Pi Imager, option image
personnalisée. Le flash efface le contenu du support sélectionné.

Tester un support vierge pour chaque architecture :

1. Premier démarrage : français, thème orange, logo, vidéo, écran de chargement.
2. Présence ROMs/BIOS et lancement d'un jeu représentatif.
3. Modifier un réglage d'interface, redémarrer, vérifier persistance du splash.
4. Vérifier capacité SHARE et espace libre avec `df -h /userdata`.

Validation locale sans montage :

```bash
python3 -m unittest discover -s tests -v
```

Génération réelle requiert images, contenus et privilèges root. Validation du
démarrage PC/Pi 3 reste nécessaire avant déploiement multiple.

## Références v44

- [Initialisation SHARE](https://github.com/batocera-linux/batocera.linux/blob/batocera-44/board/batocera/fsoverlay/etc/init.d/S12populateshare)
- [Agrandissement initial](https://github.com/batocera-linux/batocera.linux/blob/batocera-44/board/batocera/fsoverlay/etc/init.d/S02resize)
- [Partitions Pi 3](https://github.com/batocera-linux/batocera.linux/blob/batocera-44/board/batocera/broadcom/bcm2837/genimage.cfg)
- [Splashes Batocera](https://wiki.batocera.org/splash_boot)
