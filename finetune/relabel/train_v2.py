"""
Re-entrena el clasificador local con el corpus re-etiquetado por los anotadores.

Diferencias frente a train.py (v1):
  - Corpus real de navegación (2017 items únicos) en vez del corpus sintético.
  - DOS splits evaluados por separado, porque miden cosas distintas:
      * por host   → generalización a sitios NUNCA vistos (cota inferior honesta)
      * aleatorio  → rendimiento en sitios ya vistos (el caso de uso real del producto,
                     donde el usuario revisita los mismos dominios)
    v1 reportaba LOO sobre el corpus de entrenamiento, que infla el resultado.
  - Evalúa por centroide más cercano, replicando exactamente offscreen.js
    (embed → coseno vs. centroides construidos SOLO con train → argmax + umbral).
  - Barrido del umbral de similitud para recalibrar SIM_THRESHOLD (hoy 0.65, arbitrario).

Uso:  python relabel/train_v2.py            (desde finetune/, con el .venv activo)
"""
import json
import os
import random
import collections

import numpy as np
import torch
from sentence_transformers import SentenceTransformer, InputExample, losses
from torch.utils.data import DataLoader

BASE = os.path.dirname(os.path.abspath(__file__))
BASE_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
V1_DIR = "output/finetuned"
OUT_DIR = "output/finetuned-v2"
EPOCHS = 8
BATCH_SIZE = 32
POS_PER_ITEM = 4
TEST_FRAC = 0.20
SEED = 20260808

CATEGORIES = [
    "💻 Desarrollo", "🔬 Investigación", "🤖 IA", "💬 Redes Sociales",
    "🎬 Entretenimiento", "⚡ Productividad", "🛒 Compras", "📰 Noticias",
    "💰 Finanzas", "📚 Aprendizaje", "✈️ Viajes",
]


def text_of(r):
    """Replica el formato de inferencia de offscreen.js: `${title} ${host+path}`."""
    return f"{r['title']} {r['url']}".strip()[:256]


# ---------------------------------------------------------------- splits
def split_by_host(rows, frac, seed):
    """Ningún host aparece en train y test a la vez → sin fuga por dominio."""
    rng = random.Random(seed)
    byhost = collections.defaultdict(list)
    for r in rows:
        byhost[r["host"]].append(r)
    hosts = sorted(byhost, key=lambda h: (-len(byhost[h]), h))
    target = len(rows) * frac
    # los hosts gigantes van forzados a train: en test aportarían sesgo, no señal
    big = {h for h in hosts if len(byhost[h]) > target * 0.5}
    pool = [h for h in hosts if h not in big]
    rng.shuffle(pool)
    test_hosts, n = set(), 0
    for h in pool:
        if n >= target:
            break
        test_hosts.add(h)
        n += len(byhost[h])
    train = [r for r in rows if r["host"] not in test_hosts]
    test = [r for r in rows if r["host"] in test_hosts]
    return train, test, {"hostsForzadosATrain": sorted(big), "hostsEnTest": len(test_hosts)}


def split_random(rows, frac, seed):
    """Estratificado por etiqueta."""
    rng = random.Random(seed)
    bylab = collections.defaultdict(list)
    for r in rows:
        bylab[r["label"]].append(r)
    train, test = [], []
    for lab, lst in sorted(bylab.items()):
        lst = sorted(lst, key=lambda r: r["id"])
        rng.shuffle(lst)
        k = max(1, int(len(lst) * frac)) if len(lst) > 2 else 0
        test += lst[:k]
        train += lst[k:]
    return train, test, {}


# ---------------------------------------------------------------- evaluación
def centroids(model, train):
    bylab = collections.defaultdict(list)
    for r in train:
        bylab[r["label"]].append(text_of(r))
    cents = {}
    for lab, texts in bylab.items():
        v = model.encode(texts, normalize_embeddings=True, show_progress_bar=False).mean(axis=0)
        cents[lab] = v / (np.linalg.norm(v) or 1)
    return cents


def evaluate(model, train, test, tag):
    cents = centroids(model, train)
    labs = sorted(cents)
    M = np.stack([cents[l] for l in labs])
    X = model.encode([text_of(r) for r in test], normalize_embeddings=True, show_progress_bar=False)
    sims = X @ M.T
    pred_i = sims.argmax(axis=1)
    best = sims.max(axis=1)
    pred = [labs[i] for i in pred_i]
    true = [r["label"] for r in test]
    w = np.array([r["n"] for r in test], dtype=float)   # ponderado por visitas reales

    correct = np.array([p == t for p, t in zip(pred, true)])
    acc = float(correct.mean())
    acc_w = float((correct * w).sum() / w.sum())

    per = {}
    for lab in labs:
        tp = sum(1 for p, t in zip(pred, true) if p == lab and t == lab)
        fp = sum(1 for p, t in zip(pred, true) if p == lab and t != lab)
        fn = sum(1 for p, t in zip(pred, true) if p != lab and t == lab)
        P = tp / (tp + fp) if tp + fp else 0.0
        R = tp / (tp + fn) if tp + fn else 0.0
        per[lab] = {"support": tp + fn, "precision": round(P, 4), "recall": round(R, 4),
                    "f1": round(2 * P * R / (P + R), 4) if P + R else 0.0}
    macro_f1 = round(sum(v["f1"] for v in per.values()) / len(per), 4)

    # barrido de umbral: cobertura vs. precisión sobre lo cubierto
    sweep = []
    for th in [i / 100 for i in range(40, 96, 5)]:
        cov = best >= th
        sweep.append({"threshold": th, "cobertura": round(float(cov.mean()), 4),
                      "precisionCubierta": round(float(correct[cov].mean()), 4) if cov.any() else None})

    cm = collections.defaultdict(collections.Counter)
    for p, t in zip(pred, true):
        cm[t][p] += 1

    return {
        "split": tag, "nTrain": len(train), "nTest": len(test),
        "accuracy": round(acc, 4), "accuracyPonderadaPorVisitas": round(acc_w, 4),
        "macroF1": macro_f1, "porCategoria": per,
        "simMediaAciertos": round(float(best[correct].mean()), 4) if correct.any() else None,
        "simMediaFallos": round(float(best[~correct].mean()), 4) if (~correct).any() else None,
        "barridoUmbral": sweep,
        "matrizConfusion": {t: dict(p) for t, p in cm.items()},
    }


def train_model(train, seed):
    torch.manual_seed(seed)
    model = SentenceTransformer(BASE_MODEL)
    bylab = collections.defaultdict(list)
    for r in train:
        bylab[r["label"]].append(text_of(r))
    rng = random.Random(seed)
    pairs = []
    for lab, texts in bylab.items():
        if len(texts) < 2:
            continue
        for t in texts:
            for _ in range(POS_PER_ITEM):
                other = rng.choice(texts)
                if other != t:
                    pairs.append(InputExample(texts=[t, other]))
    rng.shuffle(pairs)
    print(f"   pares de entrenamiento: {len(pairs)}")
    loader = DataLoader(pairs, shuffle=True, batch_size=BATCH_SIZE, drop_last=True)
    loss = losses.MultipleNegativesRankingLoss(model)
    model.fit(train_objectives=[(loader, loss)], epochs=EPOCHS,
              warmup_steps=100, optimizer_params={"lr": 2e-5}, show_progress_bar=True)
    return model


def main():
    rows = json.load(open(f"{BASE}/labeled.json"))
    print(f"corpus: {len(rows)} items únicos, {len(set(r['host'] for r in rows))} hosts")
    dist = collections.Counter(r["label"] for r in rows)
    for c, k in dist.most_common():
        print(f"   {c:<22} {k}")

    results = {"corpus": {"nItems": len(rows), "nHosts": len(set(r['host'] for r in rows)),
                          "distribucion": dict(dist)}, "splits": {}}

    for tag, splitter in (("porHost", split_by_host), ("aleatorio", split_random)):
        print(f"\n{'='*60}\nSplit {tag}")
        train, test, meta = splitter(rows, TEST_FRAC, SEED)
        print(f"   train={len(train)}  test={len(test)}  {meta}")
        if not test:
            continue

        # baseline 1: MiniLM base sin fine-tune
        base = SentenceTransformer(BASE_MODEL)
        r_base = evaluate(base, train, test, tag)

        # baseline 2: el modelo v1 ya desplegado (si existe), con las etiquetas nuevas
        r_v1 = None
        if os.path.isdir(V1_DIR):
            r_v1 = evaluate(SentenceTransformer(V1_DIR), train, test, tag)

        # modelo v2
        print("   entrenando v2…")
        m2 = train_model(train, SEED)
        r_v2 = evaluate(m2, train, test, tag)
        if tag == "porHost":
            m2.save(OUT_DIR)
            print(f"   modelo guardado en {OUT_DIR}")

        results["splits"][tag] = {"meta": meta, "base": r_base, "v1": r_v1, "v2": r_v2}
        print(f"   {'base':<6} acc={r_base['accuracy']:.4f}  macroF1={r_base['macroF1']:.4f}")
        if r_v1:
            print(f"   {'v1':<6} acc={r_v1['accuracy']:.4f}  macroF1={r_v1['macroF1']:.4f}")
        print(f"   {'v2':<6} acc={r_v2['accuracy']:.4f}  macroF1={r_v2['macroF1']:.4f}  "
              f"(ponderada por visitas {r_v2['accuracyPonderadaPorVisitas']:.4f})")

    json.dump(results, open("output/metrics_v2.json", "w"), ensure_ascii=False, indent=2)
    print("\nmétricas → output/metrics_v2.json")


if __name__ == "__main__":
    main()
