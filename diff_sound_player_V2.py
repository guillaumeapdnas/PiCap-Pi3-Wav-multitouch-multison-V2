#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CapTouch Sound Mapper — PiCap Bare Conductive / Raspberry Pi 3
================================================================
Version améliorée (interaction + ergonomie) :

  INTERACTION
  - Boucle capteur exécutée via root.after() dans le thread principal
    (Tkinter n'est pas thread-safe : l'ancienne version plantait aléatoirement).
  - Un seul bouton Play/Stop fiable (plus de threads dupliqués).
  - Une plage est ACTIVE uniquement si max > min : min == max => désactivée,
    ce qui supprime le bug « un son joue en boucle quand on ne touche pas ».
  - Rejeu immédiat quand on quitte puis re-rentre dans une plage
    (plus besoin d'attendre le cooldown après un nouveau contact).
  - Cooldown de rejeu réglable (délai de re-trigger).
  - 64 voix pygame correctement allouées via set_num_channels().
  - Synchronisation min/max par colonne propagée EN TEMPS RÉEL
    (trace_add au lieu d'un simple copié au clic sur la case).
  - Mode démo si le capteur ou les fichiers audio sont absents
    (l'interface reste utilisable pour préparer les configs).

  ERGONOMIE
  - Barre de niveau capacitif en temps réel par électrode.
  - Couleurs : vert = plage active, gris = désactivée, rouge = min > max.
  - Bouton « Répartir » qui génère 12 plages graduées en 1 clic.
  - Bouton Test (▶) par électrode pour écouter un son sans toucher.
  - Sauvegarde rapide (config.json à côté du script), chargée au démarrage.
  - Barre d'état, auto-chargement, raccourci Espace = Play/Stop.
  - Thème sombre avec zones visuellement séparées et colorées :
    électrodes (cyan), seuils & config (orange), lecture (vert),
    volume (bleu), fichiers (violet).
  - Config sauvegardée rétro-compatible avec l'ancien format.
  - Démarrage en PLEIN ÉCRAN kiosk (Échap = sortir, F11 = basculer).
  - CALIBRATION AUTOMATIQUE DU SILENCE au lancement : le niveau capacitif de
    repos de CHAQUE électrode est mesuré (il dépend de la matière et de la
    longueur de ce qui y est attaché), puis soustrait des mesures. Les plages
    restent donc exploitables quel que soit le capuchon / câble branché.
    Bouton « Calibrer silence » pour recalibrer à chaud après modification
    d'une matière (les valeurs de bruit sont aussi sauvegardées en config).
"""

import json
import os
import time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import pygame
from pygame.mixer import Sound

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------
APP_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CONFIG_FILE = os.path.join(APP_DIR, "config.json")
TRACKS_DIR = os.path.join(APP_DIR, "tracks")

NUM_ELECTRODES = 12
NUM_SOUNDS = 12
NUM_COLS = NUM_SOUNDS * 2          # une colonne min + une colonne max par son
POLL_MS = 100                      # période de lecture capteur / rafraîchissement
DEFAULT_TOUCH_THRESHOLD = 40
DEFAULT_RELEASE_THRESHOLD = 20
DEFAULT_RETRIGGER_DELAY = 1.0      # secondes avant de rejouer la même plage
DEFAULT_SPREAD_MAX = 60            # valeur max pour le bouton « Répartir »
DIFF_DISPLAY_MAX = 120             # échelle des barres de niveau
KIOSK_MODE = True                 # plein écran au démarrage (Échap pour sortir)
CALIB_DURATION = 3.0              # secondes de calibration du silence
CALIB_MARGIN = 2                  # marge de sécurité ajoutée au bruit mesuré

# --- Thème sombre : une couleur par zone -----------------------------------
BG_ROOT = "#12121a"                  # fond général
BG_ROW_EVEN = "#1d1d2c"              # lignes électrodes (alternées)
BG_ROW_ODD = "#171722"
BG_HEADER_S0 = "#26263c"             # en-têtes colonnes sons (alternés)
BG_HEADER_S1 = "#1c1c2e"
FG_MAIN = "#e6e6f0"                  # texte principal
FG_MUTED = "#8f96ad"                 # texte secondaire

ZONE_SEUILS = "#2a2015"              # orange  : seuils, répartition, calibration
ACCENT_SEUILS = "#e8a33d"
ZONE_LECTURE = "#14261a"             # vert    : play/stop, rejeu
ACCENT_LECTURE = "#43c05a"
ZONE_VOLUME = "#0f2230"              # bleu    : volume
ACCENT_VOLUME = "#3fb7d8"
ZONE_FICHIERS = "#221830"            # violet  : charger / sauver / reset
ACCENT_FICHIERS = "#a48ad0"
ACCENT_ELECTRODES = "#3fb7d8"        # cyan    : noms E, barres de niveau
COL_DIFF = "#ffb454"                 # valeur diff en orange vif

COL_EVEN = BG_ROW_EVEN               # couleur de base des cellules (lignes paires)
COL_ODD = BG_ROW_ODD
COL_ACTIVE = "#1f7a34"               # plage en cours (vert)
COL_DISABLED = "#101018"             # min == max : désactivée
COL_INVALID = "#8a3030"              # min > max (rouge)

# ---------------------------------------------------------------------------
# Capteur MPR121 (mode démo si indisponible)
# ---------------------------------------------------------------------------
sensor = None
sensor_error = ""
try:
    import MPR121
    sensor = MPR121.begin()
    sensor.set_touch_threshold(DEFAULT_TOUCH_THRESHOLD)
    sensor.set_release_threshold(DEFAULT_RELEASE_THRESHOLD)
    SENSOR_OK = True
except Exception as exc:            # capteur absent, I2C HS, lib manquante...
    sensor_error = str(exc)
    SENSOR_OK = False

# ---------------------------------------------------------------------------
# Pygame / audio
# ---------------------------------------------------------------------------
pygame.mixer.pre_init(frequency=44100, size=-16, channels=2, buffer=512)
pygame.init()
pygame.mixer.set_num_channels(64)   # nb de voix simultanées (pas "channels=")

sounds = {}
sounds_found = 0
for e in range(NUM_ELECTRODES):
    sounds[e] = []
    folder = os.path.join(TRACKS_DIR, f"E{e}")
    for i in range(NUM_SOUNDS):
        path = os.path.join(folder, f"{i:03}.wav")
        if os.path.exists(path):
            try:
                sounds[e].append(Sound(path))
                sounds_found += 1
            except Exception:
                sounds[e].append(None)
        else:
            sounds[e].append(None)


def get_int(var, default=0):
    """Lecture sûre d'une IntVar (l'utilisateur peut être en train de taper)."""
    try:
        return int(var.get())
    except (tk.TclError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------
class CapTouchApp:
    def __init__(self, root):
        self.root = root
        root.title("CapTouch Sound Mapper — PiCap MPR121")
        root.geometry("1280x640")
        root.minsize(900, 480)
        if KIOSK_MODE:
            root.attributes("-fullscreen", True)

        # --- thème sombre global -------------------------------------------
        root.configure(bg=BG_ROOT)
        style = ttk.Style()
        style.theme_use("clam")
        style.configure(".", background=BG_ROOT, foreground=FG_MAIN)
        style.configure("TFrame", background=BG_ROOT)
        style.configure("TLabel", background=BG_ROOT, foreground=FG_MAIN)
        style.configure("TButton", background="#2b2b3e", foreground=FG_MAIN,
                         borderwidth=0, focusthickness=0)
        style.map("TButton", background=[("active", "#3d3d55")])
        style.configure("TEntry", fieldbackground="#26263a", foreground=FG_MAIN,
                        insertcolor=FG_MAIN, bordercolor="#3a3a52")
        style.configure("TSpinbox", fieldbackground="#26263a", foreground=FG_MAIN,
                        insertcolor=FG_MAIN, arrowcolor=FG_MAIN,
                        bordercolor="#3a3a52", background="#2b2b3e",
                        buttonbackground="#2b2b3e")
        style.configure("Horizontal.TScrollbar", troughcolor="#1b1b28",
                        background="#33334a", bordercolor=BG_ROOT,
                        arrowcolor=FG_MAIN)
        self.style = style

        # --- état ---------------------------------------------------------
        self.touch_var = tk.IntVar(value=DEFAULT_TOUCH_THRESHOLD)
        self.release_var = tk.IntVar(value=DEFAULT_RELEASE_THRESHOLD)
        self.volume_var = tk.IntVar(value=100)
        self.retrigger_var = tk.DoubleVar(value=DEFAULT_RETRIGGER_DELAY)
        self.spread_max_var = tk.IntVar(value=DEFAULT_SPREAD_MAX)

        # plages[e][2i] = min du son i, plages[e][2i+1] = max du son i
        # par défaut tout est désactivé (min == max == 0) => silence.
        self.plages = [[tk.IntVar(value=0) for _ in range(NUM_COLS)]
                       for _ in range(NUM_ELECTRODES)]

        # synchronisation : une case par SON (sync min + max ensemble)
        self.sync_vars = [tk.BooleanVar(value=False) for _ in range(NUM_SOUNDS)]

        self.diff_labels = [tk.StringVar(value="0") for _ in range(NUM_ELECTRODES)]
        self.entry_widgets = [[None] * NUM_COLS for _ in range(NUM_ELECTRODES)]
        self.entry_colors = {}          # cache : évite de reconfigurer 288 widgets
        self.bars = []
        self.bar_rects = []

        self.active_range = [None] * NUM_ELECTRODES
        self.last_play_time = [0.0] * NUM_ELECTRODES

        self.running = False
        self._syncing = False

        # calibration du silence (bruit de repos mesuré par électrode)
        self.noise_floor = [0] * NUM_ELECTRODES
        self._calibrating = False
        self._calib_end = 0.0
        self._calib_samples = [0] * NUM_ELECTRODES
        self._calib_was_running = False

        self._build_toolbar()
        self._build_table()
        self._build_statusbar()

        # synchronisation temps réel des colonnes
        for e in range(NUM_ELECTRODES):
            for c in range(NUM_COLS):
                self.plages[e][c].trace_add(
                    "write", lambda *_a, e=e, c=c: self._on_range_change(e, c))

        # raccourcis
        root.bind("<space>", self._on_space)
        root.bind("<Escape>", self._exit_fullscreen)
        root.bind("<F11>", self._toggle_fullscreen)
        root.protocol("WM_DELETE_WINDOW", self.on_close)

        self.load_config(DEFAULT_CONFIG_FILE, quiet=True)
        self.set_volume(self.volume_var.get())

        if not SENSOR_OK:
            self.set_status(f"Mode démo — capteur indisponible : {sensor_error}")
        else:
            self.set_status(f"Capteur OK — {sounds_found} sons chargés."
                            if sounds_found else
                            "Capteur OK — aucun son trouvé dans ./tracks/E*/")

        self._poll()
        self.toggle_running()          # démarre en lecture
        if SENSOR_OK:
            # calibration automatique du silence ~0,6 s après l'ouverture
            # (laisse le temps aux lignes I2C de se stabiliser)
            self.root.after(600, self.start_calibration)

    # ------------------------------------------------------------------ UI
    def _build_toolbar(self):
        bar = tk.Frame(self.root, bg=BG_ROOT)
        bar.pack(side="top", fill="x", padx=10, pady=(10, 2))

        row1 = tk.Frame(bar, bg=BG_ROOT)
        row1.pack(side="top", fill="x")
        row2 = tk.Frame(bar, bg=BG_ROOT)
        row2.pack(side="top", fill="x", pady=(6, 2))

        def zone(parent, title, bg, accent):
            z = tk.Frame(parent, bg=bg, padx=12, pady=7)
            z.pack(side="left", fill="y", padx=(0, 10))
            tk.Label(z, text=title, bg=bg, fg=accent,
                     font=("TkDefaultFont", 8, "bold")).pack(side="top", anchor="w")
            inner = tk.Frame(z, bg=bg)
            inner.pack(side="top", anchor="w", pady=(4, 0))
            return inner

        def mklabel(parent, text, bg, fg=FG_MAIN):
            return tk.Label(parent, text=text, bg=bg, fg=fg)

        def mkentry(parent, var):
            return tk.Entry(parent, textvariable=var, width=4, justify="center",
                            bg="#26263a", fg=FG_MAIN, insertbackground=FG_MAIN,
                            relief="flat", highlightthickness=1,
                            highlightbackground="#3a3a52")

        def mkbtn(parent, text, cmd, bg, fg, bg_active, padx=8, bold=False):
            font = ("TkDefaultFont", 10, "bold") if bold else "TkDefaultFont"
            return tk.Button(parent, text=text, command=cmd, bd=0, relief="flat",
                             bg=bg, fg=fg, activebackground=bg_active,
                             activeforeground=fg, padx=padx, pady=3,
                             cursor="hand2", font=font)

        # --- zone ORANGE : seuils & configuration -------------------------
        seuils = zone(row1, "SEUILS / CONFIG", ZONE_SEUILS, ACCENT_SEUILS)
        mklabel(seuils, "Touch:", ZONE_SEUILS).pack(side="left")
        mkentry(seuils, self.touch_var).pack(side="left", padx=(3, 8))
        mklabel(seuils, "Release:", ZONE_SEUILS).pack(side="left")
        mkentry(seuils, self.release_var).pack(side="left", padx=(3, 8))
        mkbtn(seuils, "Appliquer", self.apply_thresholds,
              "#3d2f1c", "#ffcb7a", "#54401f").pack(side="left", padx=(0, 10))
        mklabel(seuils, "Répartir jusqu'à:", ZONE_SEUILS).pack(side="left")
        mkentry(seuils, self.spread_max_var).pack(side="left", padx=(3, 3))
        mkbtn(seuils, "Répartir", self.spread_ranges,
              "#3d2f1c", "#ffcb7a", "#54401f").pack(side="left", padx=(0, 10))
        mkbtn(seuils, "Calibrer silence", self.start_calibration,
              "#7a5a1e", "#ffe9bd", "#96722a").pack(side="left")

        # --- zone VERTE : lecture --------------------------------------------
        lecture = zone(row1, "LECTURE", ZONE_LECTURE, ACCENT_LECTURE)
        self.playstop_btn = mkbtn(lecture, "■ Stop", self.toggle_running,
                                  "#b23a31", "#ffffff", "#d04c40",
                                  padx=18, bold=True)
        self.playstop_btn.pack(side="left", padx=(0, 12))
        mklabel(lecture, "Rejeu (s):", ZONE_LECTURE).pack(side="left")
        ttk.Spinbox(lecture, from_=0.1, to=10.0, increment=0.1, width=5,
                    textvariable=self.retrigger_var).pack(side="left", padx=(3, 0))

        # --- zone BLEUE : volume ---------------------------------------------
        vol = zone(row2, "VOLUME", ZONE_VOLUME, ACCENT_VOLUME)
        self.volume_scale = tk.Scale(vol, from_=0, to=100, orient="horizontal",
                                     length=160, showvalue=0, bg=ZONE_VOLUME,
                                     fg=FG_MAIN, troughcolor="#0a1822",
                                     highlightthickness=0,
                                     highlightbackground=ZONE_VOLUME,
                                     activebackground=ACCENT_VOLUME,
                                     command=self.set_volume)
        self.volume_scale.set(100)
        self.volume_scale.pack(side="left")
        self.volume_label = mklabel(vol, "100%", ZONE_VOLUME, ACCENT_VOLUME)
        self.volume_label.pack(side="left", padx=(8, 0))

        # --- zone VIOLETTE : fichiers -----------------------------------------
        fichiers = zone(row2, "FICHIERS", ZONE_FICHIERS, ACCENT_FICHIERS)
        for text, cmd in (("Charger…", lambda: self.load_config(None)),
                          ("Sauver", self.save_config),
                          ("Sauver sous…", self.save_config_as),
                          ("Réinitialiser", self.reset_all)):
            mkbtn(fichiers, text, cmd, "#322748", "#d8c9f5", "#443561"
                  ).pack(side="left", padx=(0, 6))

    def _build_table(self):
        container = tk.Frame(self.root, bg=BG_ROOT)
        container.pack(side="top", fill="both", expand=True)

        tk.Label(container, text="ÉLECTRODES", bg=BG_ROOT, fg=ACCENT_ELECTRODES,
                 font=("TkDefaultFont", 9, "bold")
                 ).pack(side="top", anchor="w", padx=14, pady=(8, 0))

        self.canvas = tk.Canvas(container, highlightthickness=0, bg=BG_ROOT)
        hscroll = ttk.Scrollbar(container, orient="horizontal",
                                command=self.canvas.xview)
        self.canvas.configure(xscrollcommand=hscroll.set)
        self.canvas.pack(side="top", fill="both", expand=True)
        hscroll.pack(side="bottom", fill="x")

        frame = tk.Frame(self.canvas, bg=BG_ROOT)
        self.canvas.create_window((0, 0), window=frame, anchor="nw")
        frame.bind("<Configure>",
                   lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))

        # molette horizontale (X11 : boutons 6/7)
        self.canvas.bind_all("<Button-6>",
                             lambda e: self.canvas.xview_scroll(2, "units"))
        self.canvas.bind_all("<Button-7>",
                             lambda e: self.canvas.xview_scroll(-2, "units"))

        # --- en-tête -------------------------------------------------------
        for col, txt in ((0, "Test"), (1, "E"), (2, "Diff"), (3, "Niveau")):
            tk.Label(frame, text=txt, width=5, bg="#101018", fg=FG_MUTED,
                     font=("TkDefaultFont", 9, "bold")).grid(
                         row=0, column=col, sticky="nsew")
        for i in range(NUM_SOUNDS):
            bg = BG_HEADER_S0 if i % 2 == 0 else BG_HEADER_S1
            tk.Label(frame, text=f"S{i}\nmin", bg=bg, width=4, fg=FG_MAIN,
                     font=("TkDefaultFont", 9, "bold")).grid(
                         row=0, column=4 + 2 * i, sticky="nsew")
            tk.Label(frame, text=f"S{i}\nmax", bg=bg, width=4, fg=FG_MAIN,
                     font=("TkDefaultFont", 9, "bold")).grid(
                         row=0, column=5 + 2 * i, sticky="nsew")

        # --- lignes électrodes ---------------------------------------------
        for e in range(NUM_ELECTRODES):
            row = e + 1
            color = COL_EVEN if e % 2 == 0 else COL_ODD
            tk.Button(frame, text="▶", width=2, relief="flat", bd=0,
                      bg="#23232e", fg=ACCENT_VOLUME, activebackground="#3a3a52",
                      activeforeground=ACCENT_VOLUME, cursor="hand2",
                      command=lambda e=e: self.test_electrode(e)
                      ).grid(row=row, column=0, sticky="nsew")
            tk.Label(frame, text=f"E{e}", width=4, bg=color,
                     fg=ACCENT_ELECTRODES,
                     font=("TkDefaultFont", 9, "bold")).grid(
                         row=row, column=1, sticky="nsew")
            tk.Label(frame, textvariable=self.diff_labels[e], width=5,
                     fg=COL_DIFF, bg=color).grid(row=row, column=2, sticky="nsew")

            bar = tk.Canvas(frame, width=96, height=16, bg="#0d0d15",
                            highlightthickness=0)
            bar.grid(row=row, column=3, sticky="nsew", padx=2, pady=2)
            self.bars.append(bar)
            self.bar_rects.append(None)

            for i in range(NUM_SOUNDS):
                cmin, cmax = 4 + 2 * i, 5 + 2 * i
                wmin = tk.Entry(frame, textvariable=self.plages[e][2 * i],
                                width=4, justify="center", bg=color, fg=FG_MAIN,
                                insertbackground=FG_MAIN, relief="flat",
                                highlightthickness=1,
                                highlightbackground="#31314a")
                wmin.grid(row=row, column=cmin)
                wmax = tk.Entry(frame, textvariable=self.plages[e][2 * i + 1],
                                width=4, justify="center", bg=color, fg=FG_MAIN,
                                insertbackground=FG_MAIN, relief="flat",
                                highlightthickness=1,
                                highlightbackground="#31314a")
                wmax.grid(row=row, column=cmax)
                self.entry_widgets[e][2 * i] = wmin
                self.entry_widgets[e][2 * i + 1] = wmax

        # --- ligne « Sync » (une case par son) ------------------------------
        tk.Label(frame, text="Sync", fg=FG_MUTED, bg=BG_ROOT).grid(
            row=NUM_ELECTRODES + 2, column=0, columnspan=4, sticky="e")
        for i in range(NUM_SOUNDS):
            tk.Checkbutton(frame, variable=self.sync_vars[i], bg=BG_ROOT,
                           fg=FG_MAIN, selectcolor="#26263a",
                           activebackground=BG_ROOT, activeforeground=FG_MAIN
                           ).grid(row=NUM_ELECTRODES + 2, column=4 + 2 * i,
                                  columnspan=2, sticky="nsew")

        # --- légende --------------------------------------------------------
        tk.Label(frame,
                 text="vert = plage active   ·   gris foncé = désactivée (min = max)   ·   "
                      "rouge = min > max   ·   Sync = copie min & max de la colonne "
                      "sur toutes les électrodes",
                 fg=FG_MUTED, bg=BG_ROOT).grid(
                     row=NUM_ELECTRODES + 3, column=0,
                     columnspan=4 + NUM_COLS, sticky="w", pady=(6, 0))

    def _build_statusbar(self):
        self.status_label = tk.Label(self.root, text="", bg="#0c0c13",
                                     fg=FG_MUTED, anchor="w", padx=10, pady=3)
        self.status_label.pack(side="bottom", fill="x")

    def set_status(self, msg):
        self.status_label.config(text=msg)

    # ------------------------------------------------------------- capteur
    def read_diffs(self):
        diffs = [0] * NUM_ELECTRODES
        if SENSOR_OK and sensor is not None:
            try:
                sensor.update_baseline_data()
                sensor.update_filtered_data()
                for e in range(NUM_ELECTRODES):
                    diffs[e] = (sensor.get_baseline_data(e)
                                - sensor.get_filtered_data(e))
            except Exception as exc:
                self.set_status(f"Erreur capteur : {exc}")
        return diffs

    # ------------------------------------------------------------ boucle
    def _poll(self):
        raw_diffs = self.read_diffs()
        now = time.monotonic()
        retrigger = self.retrigger_var.get()

        # --- calibration du silence en cours ------------------------------
        if self._calibrating:
            remaining = self._calib_end - now
            if remaining > 0:
                for e in range(NUM_ELECTRODES):
                    self._calib_samples[e] = max(self._calib_samples[e],
                                                  raw_diffs[e])
                self.set_status(f"Calibration du silence… {remaining:.1f}s "
                                f"— NE TOUCHEZ PAS les électrodes")
                self.root.after(POLL_MS, self._poll)
                return
            self._finish_calibration()

        for e in range(NUM_ELECTRODES):
            # diff calibré : bruit de repos propre à l'électrode (matière,
            # longueur de fil/capuchon) soustrait -> 0 = silence garanti
            diff = max(0, raw_diffs[e] - self.noise_floor[e])
            self.diff_labels[e].set(str(diff))
            self._update_bar(e, diff)

            # détection de la plage active (première plage valide qui contient diff)
            active = None
            for i in range(NUM_SOUNDS):
                mn = get_int(self.plages[e][2 * i])
                mx = get_int(self.plages[e][2 * i + 1])
                if mx > mn and mn <= diff <= mx:
                    active = i
                    break

            # rejeu immédiat à l'entrée dans une nouvelle plage
            if (self.running and active is not None
                    and sounds[e][active] is not None
                    and (self.active_range[e] != active
                         or now - self.last_play_time[e] >= retrigger)):
                sounds[e][active].play()
                self.last_play_time[e] = now

            self.active_range[e] = active
            self._refresh_entry_colors(e, active)

        self.root.after(POLL_MS, self._poll)

    def _update_bar(self, e, diff):
        bar = self.bars[e]
        w = max(20, bar.winfo_width())
        length = max(0, min(1.0, diff / float(DIFF_DISPLAY_MAX))) * (w - 2)
        fill = ACCENT_LECTURE if self.active_range[e] is not None else ACCENT_ELECTRODES
        if self.bar_rects[e] is None:
            self.bar_rects[e] = bar.create_rectangle(1, 1, 1 + length, 15, fill=fill)
        else:
            bar.coords(self.bar_rects[e], 1, 1, 1 + length, 15)
            bar.itemconfig(self.bar_rects[e], fill=fill)

    def _refresh_entry_colors(self, e, active):
        base = COL_EVEN if e % 2 == 0 else COL_ODD
        for i in range(NUM_SOUNDS):
            mn = get_int(self.plages[e][2 * i])
            mx = get_int(self.plages[e][2 * i + 1])
            if mn > mx:
                color = COL_INVALID
            elif mx == mn:
                color = COL_DISABLED
            elif i == active:
                color = COL_ACTIVE
            else:
                color = base
            for c in (2 * i, 2 * i + 1):
                widget = self.entry_widgets[e][c]
                if self.entry_colors.get(id(widget)) != color:
                    widget.configure(bg=color)
                    self.entry_colors[id(widget)] = color

    # ------------------------------------------------------------- actions
    def toggle_running(self):
        self.running = not self.running
        if self.running:
            self.playstop_btn.config(text="■ Stop", bg="#b23a31",
                                     activebackground="#d04c40")
            self.set_status("Lecture : ON")
        else:
            self.playstop_btn.config(text="▶ Play", bg="#2e7d38",
                                     activebackground="#3f9c4a")
            self.set_status("Lecture : OFF")

    # -------------------------------------------------- plein écran
    def _toggle_fullscreen(self, _event=None):
        self.root.attributes("-fullscreen",
                             not self.root.attributes("-fullscreen"))

    def _exit_fullscreen(self, _event=None):
        self.root.attributes("-fullscreen", False)

    # -------------------------------------------------- calibration silence
    def start_calibration(self, duration=CALIB_DURATION):
        """Mesure le bruit capacitif de repos de chaque électrode.
        À lancer sans rien toucher : le max observé par électrode (matière,
        longueur du câble...) devient le zéro de référence."""
        if self._calibrating:
            return
        if not SENSOR_OK:
            self.set_status("Calibration impossible : capteur indisponible.")
            return
        self._calibrating = True
        self._calib_end = time.monotonic() + duration
        self._calib_samples = [0] * NUM_ELECTRODES
        self._calib_was_running = self.running
        self.running = False            # aucun son pendant la mesure

    def _finish_calibration(self):
        self._calibrating = False
        self.noise_floor = [s + CALIB_MARGIN for s in self._calib_samples]
        self.running = self._calib_was_running
        floors = ", ".join(f"E{e}:{self.noise_floor[e]}"
                           for e in range(NUM_ELECTRODES))
        self.set_status(f"Silence calibré (bruit/câble/matière par "
                        f"électrode) : {floors}")

    def _on_space(self, _event):
        focused = self.root.focus_get()
        if isinstance(focused, (tk.Entry, ttk.Entry, ttk.Spinbox)):
            return                       # ne pas voler la barre d'espace
        self.toggle_running()

    def set_volume(self, val):
        try:
            volume = float(val) / 100.0
        except (TypeError, ValueError):
            return
        self.volume_var.set(int(round(volume * 100)))
        self.volume_label.config(text=f"{int(round(volume * 100))}%")
        for e in range(NUM_ELECTRODES):
            for i in range(NUM_SOUNDS):
                if sounds[e][i] is not None:
                    sounds[e][i].set_volume(volume)

    def apply_thresholds(self):
        touch = get_int(self.touch_var, DEFAULT_TOUCH_THRESHOLD)
        release = get_int(self.release_var, DEFAULT_RELEASE_THRESHOLD)
        if SENSOR_OK and sensor is not None:
            try:
                sensor.set_touch_threshold(touch)
                sensor.set_release_threshold(release)
                self.set_status(f"Seuils appliqués : Touch={touch} / Release={release}")
            except Exception as exc:
                self.set_status(f"Erreur seuils : {exc}")
        else:
            self.set_status("Seuils enregistrés (capteur indisponible).")

    def test_electrode(self, e):
        for i in range(NUM_SOUNDS):
            if sounds[e][i] is not None:
                sounds[e][i].play()
                self.set_status(f"Test E{e} → son S{i}")
                return
        self.set_status(f"Aucun son trouvé pour E{e} (tracks/E{e}/xxx.wav)")

    def spread_ranges(self):
        """Génère NUM_SOUNDS plages contiguës de 1 à spread_max (0 = silence)."""
        spread_max = get_int(self.spread_max_var, DEFAULT_SPREAD_MAX)
        if spread_max < NUM_SOUNDS:
            self.set_status("Valeur trop petite pour répartir 12 plages.")
            return
        step = spread_max / float(NUM_SOUNDS)
        for e in range(NUM_ELECTRODES):
            for i in range(NUM_SOUNDS):
                lo = int(i * step) + (1 if i == 0 else 2)
                hi = int((i + 1) * step) + 1
                self.plages[e][2 * i].set(lo)
                self.plages[e][2 * i + 1].set(hi)
        self.set_status(f"Plages réparties de 1 à {spread_max} sur toutes "
                        f"les électrodes (0 = silence).")

    def reset_all(self):
        if not messagebox.askyesno("Confirmation",
                                   "Réinitialiser seuils et plages ?"):
            return
        self.touch_var.set(DEFAULT_TOUCH_THRESHOLD)
        self.release_var.set(DEFAULT_RELEASE_THRESHOLD)
        for e in range(NUM_ELECTRODES):
            for c in range(NUM_COLS):
                self.plages[e][c].set(0)
        self.apply_thresholds()
        self.set_status("Configuration réinitialisée (plages désactivées).")

    # ------------------------------------------------------- sync colonnes
    def _on_range_change(self, e, c):
        """Toute édition d'une case avec Sync actif propage la valeur."""
        if self._syncing:
            return
        sound_idx = c // 2
        if not self.sync_vars[sound_idx].get():
            return
        self._syncing = True
        try:
            value = get_int(self.plages[e][c])
            for other in range(NUM_ELECTRODES):
                if other != e:
                    self.plages[other][c].set(value)
        finally:
            self._syncing = False

    # ----------------------------------------------------------- config IO
    def _config_dict(self):
        return {
            "touch_threshold": get_int(self.touch_var, DEFAULT_TOUCH_THRESHOLD),
            "release_threshold": get_int(self.release_var, DEFAULT_RELEASE_THRESHOLD),
            "volume": self.volume_var.get(),
            "retrigger_delay": self.retrigger_var.get(),
            "spread_max": get_int(self.spread_max_var, DEFAULT_SPREAD_MAX),
            "noise_floor": list(self.noise_floor),
            "plages": [[get_int(v) for v in row] for row in self.plages],
        }

    def save_config(self):
        self._write_config(DEFAULT_CONFIG_FILE)
        self.set_status(f"Configuration sauvegardée : {DEFAULT_CONFIG_FILE}")

    def save_config_as(self):
        filepath = filedialog.asksaveasfilename(
            defaultextension=".json", filetypes=[("JSON files", "*.json")],
            initialdir=APP_DIR)
        if filepath:
            self._write_config(filepath)
            self.set_status(f"Configuration sauvegardée : {filepath}")

    def _write_config(self, filepath):
        with open(filepath, "w") as f:
            json.dump(self._config_dict(), f, indent=4)

    def load_config(self, filepath=None, quiet=False):
        if filepath is None:
            filepath = filedialog.askopenfilename(
                filetypes=[("JSON files", "*.json")], initialdir=APP_DIR)
        if not filepath or not os.path.exists(filepath):
            return
        try:
            with open(filepath, "r") as f:
                config = json.load(f)

            self._syncing = True          # éviter les propagations en cascade
            try:
                self.touch_var.set(config.get("touch_threshold",
                                              DEFAULT_TOUCH_THRESHOLD))
                self.release_var.set(config.get("release_threshold",
                                                 DEFAULT_RELEASE_THRESHOLD))
                self.retrigger_var.set(config.get("retrigger_delay",
                                                  DEFAULT_RETRIGGER_DELAY))
                nf = config.get("noise_floor", [])
                for e in range(NUM_ELECTRODES):
                    self.noise_floor[e] = int(nf[e]) if e < len(nf) else 0
                if "volume" in config:
                    self.set_volume(config["volume"])

                plages = config.get("plages", [])
                for e in range(NUM_ELECTRODES):
                    if e < len(plages):
                        row = plages[e]
                        for c in range(NUM_COLS):
                            if c < len(row):
                                self.plages[e][c].set(row[c])
            finally:
                self._syncing = False

            self.apply_thresholds()
            if not quiet:
                self.set_status(f"Configuration chargée : {filepath}")
        except Exception as exc:
            if not quiet:
                messagebox.showerror("Erreur", f"Chargement impossible : {exc}")

    # ------------------------------------------------------------- fermeture
    def on_close(self):
        self.running = False
        try:
            pygame.mixer.quit()
            pygame.quit()
        except Exception:
            pass
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    CapTouchApp(root)
    root.mainloop()
