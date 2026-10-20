# PiCap-Pi3-Wav-multitouch-multison V2

Interface graphique pour configurer et déclencher des sons en simultané via les mesures des 12 électrodes capacitives du **PiCap Bare Conductive (MPR121)** sur **Raspberry Pi 3**, en fonction de plages de valeurs (intensité du contact).

![PiCap](https://raw.githubusercontent.com/guillaumeapdnas/PiCap-Pi3-Wav-multitouch-multison/main/PiCap-Pi3-Wav-multitouch-multison.png)

## Nouveautés de la version V3 (`diff_sound_player_V3.py`)

**Stabilité**
- Boucle capteur exécutée dans le thread principal via `root.after()` : Tkinter thread-safe, plus de crashs aléatoires
- Play/Stop fiable (plus de threads dupliqués), raccourci **Espace**
- 64 voix pygame correctement allouées (`set_num_channels`)

**Interaction**
- Une plage est active uniquement si `max > min` ; `min == max` = désactivée (fini le son en boucle au repos)
- Rejeu immédiat à l'entrée dans une plage + délai de re-trigger réglable
- **Calibration automatique du silence au lancement** : le bruit capacitif de repos de chaque électrode est mesuré (il dépend de la matière et de la longueur de ce qui y est attaché) puis soustrait des mesures. Bouton **Calibrer silence** pour recalibrer à chaud.
- Synchronisation min/max par colonne en temps réel
- Mode démo : interface utilisable sans capteur ni sons (préparation des configs sur PC)

**Interface**
- Démarrage en **plein écran kiosk** (Échap = fenêtré, F11 = basculer)
- Barres de niveau capacitif en temps réel par électrode
- Code couleurs : vert = plage active, gris = désactivée, rouge = min > max
- Bouton **Répartir** : génère 12 plages graduées en un clic
- Bouton **Test ▶** par électrode pour écouter sans toucher
- Sauvegarde rapide `config.json` auto-chargée au démarrage (rétro-compatible avec les configs V1/V2) + barre d'état

## Matériel
- Raspberry Pi 3
- PiCap Bare Conductive (capteur MPR121, 12 électrodes)
- Haut-parleurs branchés au Pi

## Installation

1. Système Raspbian à jour :
```
sudo apt-get update
sudo apt-get dist-upgrade
sync && sudo reboot
```

2. Paquet PiCap :
```
sudo apt-get install picap
picap-setup
```

3. Dépendances Python :
```
pip install pygame
pip install mpr121
```
(ou conserver le module `MPR121.py` du dépôt V1 à côté du script)

4. Arborescence des fichiers sons (chaque dossier E0 à E11 contient jusqu'à 12 fichiers `.wav` nommés `000.wav` à `011.wav`) :
```
├── diff_sound_player_V3.py
├── MPR121.py
├── tracks/
│   ├── E0/
│   │   ├── 000.wav
│   │   ├── ...
│   ├── E1/
│   └── ... E11/
└── config.json (optionnel, auto-chargé au démarrage)
```

5. Lancer le programme :
```
python3 diff_sound_player_V3.py
```

## Configuration (`config.json`)
```json
{
  "touch_threshold": 40,
  "release_threshold": 20,
  "volume": 100,
  "retrigger_delay": 1.0,
  "spread_max": 60,
  "noise_floor": [2, 2, 3, 2, 2, 2, 4, 2, 2, 2, 2, 2],
  "plages": [
    [1, 5, 6, 10, 11, 15, 16, 20, 21, 25, 26, 30, 31, 35, 36, 40, 41, 45, 46, 50, 51, 55, 56, 60],
    ...
  ]
}
```
Le champ `noise_floor` (bruit de repos par électrode) est recalculé à chaque calibration ; il est aussi sauvegardé dans la config. Les fichiers de config des versions précédentes restent compatibles.

## Raccourcis
   Touche | Action |
 |---|---|
 | Espace | Play / Stop |
 | F11 | Basculer plein écran |
 | Échap | Sortir du plein écran |
