# Benchmark Whisper historique

> Relevé historique de transcription batch, sans capture Android ni traduction. Il ne mesure pas la latence de bout en bout actuelle. Voir [le rapport streaming synthétique](benchmark-streaming-report.md) pour ses propres limites.

Environnement de référence :

- CPU : Intel Core i5-8300H (4 cœurs / 8 threads)
- GPU : NVIDIA GTX 1050 4 Go (Pascal, compute capability 6.1)
- CUDA 13.0 (driver), cuBLAS + cuDNN 9 fournis par wheels pip
- faster-whisper 1.2.1 / CTranslate2 4.8.4, Python 3.11
- Corpus : énoncé synthétique en japonais (68,4 s, TTS gTTS), un seul échantillon ;
  transcription japonaise vérifiée correcte par inspection.

Commande :

```bash
cd server
source scripts/gpu_env.sh
python tools/benchmark.py --models tiny base small medium \
    --gpu-compute-types int8_float32 int8 float32 --cpu-compute-types int8 \
    --samples samples/ja_sample.wav --runs 2 --json docs/benchmark_raw.json
```

Résultats :

| Modèle | Device | compute_type | Statut | Chargement | VRAM | Audio | Latence | RTF | Langue | Segments |
|---|---|---|---|---|---|---|---|---|---|---|
| tiny | cuda | int8_float32 | ok | 0.66 s | 106 Mo | 68.4 s | 2.15 s | 0.031 | ja 0.99 | 17 |
| tiny | cuda | int8 | ok | 0.34 s | 64 Mo | 68.4 s | 1.97 s | 0.029 | ja 0.99 | 17 |
| tiny | cuda | float32 | ok | 0.37 s | 192 Mo | 68.4 s | 1.99 s | 0.029 | ja 0.99 | 19 |
| tiny | cpu | int8 | ok | 0.34 s | 0 Mo | 68.4 s | 3.92 s | 0.057 | ja 1.00 | 16 |
| base | cuda | int8_float32 | ok | 0.51 s | 128 Mo | 68.4 s | 2.16 s | 0.032 | ja 1.00 | 10 |
| base | cuda | int8 | ok | 0.45 s | 128 Mo | 68.4 s | 2.23 s | 0.033 | ja 1.00 | 10 |
| base | cuda | float32 | ok | 0.46 s | 352 Mo | 68.4 s | 2.61 s | 0.038 | ja 1.00 | 10 |
| base | cpu | int8 | ok | 0.55 s | 0 Mo | 68.4 s | 8.11 s | 0.119 | ja 1.00 | 10 |
| small | cuda | int8_float32 | ok | 1.38 s | 288 Mo | 68.4 s | 4.74 s | 0.069 | ja 1.00 | 7 |
| small | cuda | int8 | ok | 1.16 s | 288 Mo | 68.4 s | 4.78 s | 0.070 | ja 1.00 | 7 |
| small | cuda | float32 | ok | 1.71 s | 1120 Mo | 68.4 s | 6.58 s | 0.096 | ja 1.00 | 7 |
| small | cpu | int8 | ok | 1.67 s | 0 Mo | 68.4 s | 74.39 s | 1.088 | ja 1.00 | 7 |
| medium | cuda | int8_float32 | ok | 6.63 s | 928 Mo | 68.4 s | 10.88 s | 0.159 | ja 1.00 | 7 |
| medium | cuda | int8 | ok | 3.73 s | 928 Mo | 68.4 s | 10.90 s | 0.159 | ja 1.00 | 7 |
| medium | cuda | float32 | **échec OOM** | 8.69 s | – | – | – | – | – | (repli CPU) |
| medium | cpu | int8 | ok | 2.76 s | 0 Mo | 68.4 s | 64.11 s | 0.937 | ja 1.00 | 7 |

Interprétation :

- **`int8` et `int8_float32` sont équivalents sur Pascal** (même VRAM, latence
  quasi identique, int8 légèrement supérieur sur `tiny`). Comme `int8` est supporté
  nativement par CTranslate2 sur CC 6.1 et que c'est le nom canonique d'une
  quantification int8, on garde `int8` pour l'auto sur Pascal. `int8_float32`
  reste disponible pour une sélection explicite.
- **Pas de `float16` / `int8_float16` sur Pascal** : CTranslate2 rapporte
  `['float32', 'int8', 'int8_float32']` seulement.
- **`float32` reste utilisable pour tiny/base/small** mais occupe 3× plus de
  VRAM pour `small` (1120 Mo) sans gain de qualité — aucun intérêt.
- **`medium` en float32 déborde de la VRAM** (OOM) ; repli automatique sur CPU.
- Sur **small CPU**, le RTF dépasse 1 (74 s pour 68 s d'audio) → trop lent pour
  du temps réel ; utiliser `base` ou `tiny` sur CPU, ou passer au GPU.
- Le moteur gère le streaming par segments (≈ 4-12 s), donc la latence par segment
  est largement sous les 3 s cible même pour `medium` sur GPU.

Recommandation :

| Cas d'usage | Modèle | Device / compute_type |
|---|---|---|
| Défaut (GTX 1050 4 Go) | `small` | `cuda` / `int8` |
| Faible latence prioritaires | `base` ou `tiny` | `cuda` / `int8` |
| CPU seul | `base` | `cpu` / `int8` |
| Précision maximale plausible | `medium` | `cuda` / `int8` |
| À éviter | `large-v3` | (VRAM insuffisante sur 4 Go) |

Référence CPU-only sous 4 cœurs : `base` en int8 donne un RTF de 0,12.
