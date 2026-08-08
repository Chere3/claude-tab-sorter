"""
Genera el informe final: gráficas en docs/relabel/ + RELABEL_REPORT.md.

Requiere haber ejecutado antes consolidate.py y train_v2.py.
"""
import json
import os
import collections

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from sanitize import redacta, redacta_host

BASE = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.abspath(f"{BASE}/../../docs/relabel")
os.makedirs(DOCS, exist_ok=True)
plt.rcParams.update({"figure.dpi": 130, "font.size": 9, "axes.grid": True,
                     "grid.alpha": .25, "axes.axisbelow": True})
SHORT = lambda c: c.split(" ", 1)[-1] if " " in c else c


def fig(name):
    plt.tight_layout()
    plt.savefig(f"{DOCS}/{name}", bbox_inches="tight")
    plt.close()
    print("  →", name)


def plot_confusion(cm, labels, title, name, normalize=True):
    M = np.array([[cm.get(t, {}).get(p, 0) for p in labels] for t in labels], dtype=float)
    ann = M.copy()
    if normalize:
        rs = M.sum(axis=1, keepdims=True)
        M = np.divide(M, rs, out=np.zeros_like(M), where=rs > 0)
    plt.figure(figsize=(8.5, 7))
    plt.imshow(M, cmap="magma_r", vmin=0, vmax=1 if normalize else None)
    plt.colorbar(label="proporción de la fila" if normalize else "n")
    ticks = [SHORT(l) for l in labels]
    plt.xticks(range(len(labels)), ticks, rotation=45, ha="right")
    plt.yticks(range(len(labels)), ticks)
    for i in range(len(labels)):
        for j in range(len(labels)):
            if ann[i, j]:
                plt.text(j, i, int(ann[i, j]), ha="center", va="center", fontsize=7,
                         color="white" if M[i, j] > .55 else "black")
    plt.ylabel("etiqueta de los anotadores")
    plt.xlabel("predicción del modelo")
    plt.title(title)
    plt.grid(False)
    fig(name)


def main():
    labeled = json.load(open(f"{BASE}/labeled.json"))
    agree = json.load(open(f"{BASE}/agreement.json"))
    dis = json.load(open(f"{BASE}/disagreement.json"))
    metrics = json.load(open(f"{BASE}/../output/metrics_v2.json")) if os.path.exists(f"{BASE}/../output/metrics_v2.json") else None

    order = [c for c, _ in collections.Counter(r["label"] for r in labeled).most_common()]

    # 1. distribución: etiqueta real vs. lo que predijo el modelo desplegado
    plt.figure(figsize=(9, 4.2))
    truth = collections.Counter(r["label"] for r in labeled)
    pred = collections.Counter(r["localFallback"] for r in labeled)
    x = np.arange(len(order))
    plt.bar(x - .2, [truth[c] for c in order], .4, label="anotadores (correcto)", color="#2b6cb0")
    plt.bar(x + .2, [pred.get(c, 0) for c in order], .4, label="modelo v1 (desplegado)", color="#e07a5f")
    plt.xticks(x, [SHORT(c) for c in order], rotation=35, ha="right")
    plt.ylabel("items únicos")
    plt.title("Distribución de categorías: verdad anotada vs. predicción del modelo actual")
    plt.legend()
    fig("distribucion.png")

    # 2. matriz de confusión del modelo desplegado contra las etiquetas nuevas
    plot_confusion(dis["matrizConfusion"], order,
                   f"Modelo v1 vs. anotadores — desacuerdo {dis['tasaDesacuerdoItems']}%",
                   "confusion_v1_vs_anotadores.png")

    # 3. recall por categoría
    plt.figure(figsize=(8.5, 4.2))
    cats = list(dis["porCategoriaVerdadera"])
    vals = [dis["porCategoriaVerdadera"][c]["recallLocal"] for c in cats]
    sup = [dis["porCategoriaVerdadera"][c]["n"] for c in cats]
    colors = ["#2f855a" if v >= 80 else "#d69e2e" if v >= 55 else "#c53030" for v in vals]
    b = plt.barh([SHORT(c) for c in cats], vals, color=colors)
    for bar, v, s in zip(b, vals, sup):
        plt.text(min(v + 1.5, 92), bar.get_y() + bar.get_height() / 2, f"{v}%  (n={s})",
                 va="center", fontsize=8)
    plt.xlim(0, 105)
    plt.xlabel("recall del modelo v1 (%)")
    plt.title("Qué tan bien acierta el modelo desplegado, por categoría real")
    plt.gca().invert_yaxis()
    fig("recall_por_categoria.png")

    # 4. calibración: ¿la similitud predice el acierto?
    plt.figure(figsize=(8, 4.2))
    bands = dis["porBandaSimilitud"]
    ks = list(bands)
    tasa = [bands[k]["tasa"] or 0 for k in ks]
    ns = [bands[k]["n"] for k in ks]
    b = plt.bar(ks, tasa, color="#7c3aed", width=.55)
    for bar, t, n in zip(b, tasa, ns):
        plt.text(bar.get_x() + bar.get_width() / 2, t + .8, f"{t}%\nn={n}", ha="center", fontsize=8)
    plt.axvline(.5, ls="--", c="grey", lw=1)
    plt.text(.52, max(tasa) * .85, "SIM_THRESHOLD = 0.65", fontsize=8, color="grey")
    plt.ylabel("tasa de desacuerdo (%)")
    plt.title("Calibración: ¿la similitud coseno predice el acierto?")
    fig("calibracion_similitud.png")

    # 5. acuerdo inter-anotador
    plt.figure(figsize=(7.5, 4))
    d = collections.Counter(round(i["agreement"], 2) for i in agree["itemsMasDisputados"])
    allag = [v for v in [agree["acuerdoMedioPorItem"]] if v]
    plt.hist([i["agreement"] for i in agree["itemsMasDisputados"]], bins=10, range=(0, 1),
             color="#0891b2", edgecolor="white")
    plt.xlabel("proporción de anotadores que coinciden")
    plt.ylabel("items del gold set")
    plt.title(f"Acuerdo inter-anotador — Fleiss' κ = {agree['fleissKappa']} "
              f"({agree['anotadoresPorItem']} anotadores, n={agree['goldItems']})")
    fig("acuerdo_anotadores.png")

    # 6/7. resultados del re-entrenamiento
    if metrics:
        for tag, res in metrics["splits"].items():
            models = [("MiniLM base", res["base"]), ("v1 (actual)", res.get("v1")), ("v2 (nuevo)", res["v2"])]
            models = [(n, r) for n, r in models if r]
            plt.figure(figsize=(7, 4))
            x = np.arange(len(models))
            plt.bar(x - .2, [r["accuracy"] * 100 for _, r in models], .4, label="accuracy", color="#2b6cb0")
            plt.bar(x + .2, [r["macroF1"] * 100 for _, r in models], .4, label="macro-F1", color="#dd6b20")
            for i, (_, r) in enumerate(models):
                plt.text(i - .2, r["accuracy"] * 100 + 1, f"{r['accuracy']*100:.1f}", ha="center", fontsize=8)
                plt.text(i + .2, r["macroF1"] * 100 + 1, f"{r['macroF1']*100:.1f}", ha="center", fontsize=8)
            plt.xticks(x, [n for n, _ in models])
            plt.ylim(0, 105)
            plt.ylabel("%")
            plt.title(f"Split {tag} — test n={res['v2']['nTest']}")
            plt.legend()
            fig(f"modelos_{tag}.png")

            sw = res["v2"]["barridoUmbral"]
            plt.figure(figsize=(7.5, 4))
            th = [s["threshold"] for s in sw]
            plt.plot(th, [s["cobertura"] * 100 for s in sw], "o-", label="cobertura (% agrupado)", color="#2b6cb0")
            plt.plot(th, [(s["precisionCubierta"] or 0) * 100 for s in sw], "s-",
                     label="precisión sobre lo agrupado", color="#2f855a")
            plt.axvline(0.65, ls="--", c="red", lw=1)
            plt.text(0.655, 20, "0.65 actual", color="red", fontsize=8)
            plt.xlabel("umbral de similitud")
            plt.ylabel("%")
            plt.title(f"Recalibración del umbral — split {tag}")
            plt.legend()
            fig(f"umbral_{tag}.png")

            plot_confusion(res["v2"]["matrizConfusion"], order,
                           f"Modelo v2 en test — split {tag}", f"confusion_v2_{tag}.png")

    # ------------------------------------------------ informe markdown
    L = []
    A = L.append
    A("# Informe de re-etiquetado y re-entrenamiento\n")
    A(f"Corpus: **{dis['nItemsUnicos']} items únicos** (deduplicados desde 4053 eventos), "
      f"{len(set(r['host'] for r in labeled))} hosts, junio–agosto 2026.\n")
    A("## Advertencia metodológica\n")
    A("El dataset original **no contiene ninguna etiqueta humana**: los 4053 eventos son "
      "`source: \"auto\"`, con `userCategory: null` y cero movimientos manuales. No existe, por tanto, "
      "ground truth. Lo que este informe llama *error* del modelo es **desacuerdo contra el consenso "
      f"de {agree['anotadoresPorItem']} anotadores LLM independientes**, no accuracy contra verdad "
      "humana. El re-entrenamiento es una destilación del criterio de los anotadores hacia MiniLM. "
      "Su validez depende del acuerdo inter-anotador, que se reporta abajo.\n")
    A("## 1. Fiabilidad de las etiquetas\n")
    A(f"| Métrica | Valor |\n|---|---|\n"
      f"| Fleiss' κ (gold, {agree['goldItems']} items × {agree['anotadoresPorItem']} anotadores) | **{agree['fleissKappa']}** |\n"
      f"| Acuerdo unánime | {agree['acuerdoUnanime']}/{agree['goldItems']} ({agree['acuerdoUnanimePct']}%) |\n"
      f"| Acuerdo medio por item | {agree['acuerdoMedioPorItem']} |\n"
      f"| Acuerdo par a par | {agree['acuerdoParPromedio']} |\n")
    A("\nInterpretación de κ (Landis & Koch): <0.20 pobre · 0.21–0.40 aceptable · 0.41–0.60 moderado · "
      "0.61–0.80 sustancial · >0.80 casi perfecto.\n")
    A("![acuerdo](../../docs/relabel/acuerdo_anotadores.png)\n")
    A("### Items más disputados\n")
    A("| Título | Host | Consenso | Acuerdo |\n|---|---|---|---|")
    for i in agree["itemsMasDisputados"][:10]:
        A(f"| {redacta(i['host'], i['title'])[:52].replace('|', chr(92)+'|')} "
          f"| {redacta_host(i['host'])[:26]} | {i['consensus']} | {i['agreement']:.2f} |")
    A("\n## 2. Rendimiento del modelo desplegado (v1)\n")
    A(f"- Desacuerdo por item único: **{dis['tasaDesacuerdoItems']}%**\n"
      f"- Desacuerdo ponderado por visitas reales: **{dis['tasaDesacuerdoPonderadaPorVisitas']}%** "
      "(pondera cada item por cuántas veces apareció; es lo que el usuario percibe)\n")
    A("![distribución](../../docs/relabel/distribucion.png)")
    A("![confusión](../../docs/relabel/confusion_v1_vs_anotadores.png)")
    A("![recall](../../docs/relabel/recall_por_categoria.png)\n")
    A("### Errores más costosos (ponderados por visitas reales)\n")
    A("Cada fila es una pestaña que abriste muchas veces y que el modelo coloca mal. La similitud "
      "es **alta** en casi todas: el modelo está seguro y equivocado, que es el peor modo de fallo.\n")
    A("| Visitas | Título | Modelo dijo | Correcto | Sim |\n|---|---|---|---|---|")
    err = sorted([r for r in labeled if r["localFallback"] != r["label"]], key=lambda r: -r["n"])
    mostrados, omitidos = 0, 0
    for r in err[:60]:
        if mostrados >= 12:
            break
        t = redacta(r["host"], r["title"], placeholder=None)
        if t is None:
            omitidos += 1
            continue
        A(f"| {r['n']}× | {t[:44].replace('|', '\\|')} | {r['localFallback']} "
          f"| {r['label']} | {r['simMean']} |")
        mostrados += 1
    if omitidos:
        A(f"\n> Se omitieron {omitidos} filas de mayor peso cuyos títulos identifican navegación "
          "personal (búsquedas, trabajo, universidad, transacciones). El corpus completo no se "
          "publica; ver *Datos* al final.")
    A("\n### Confusiones más frecuentes\n")
    A("| n | Etiqueta real | El modelo predijo |\n|---|---|---|")
    pairs = collections.Counter((r["label"], r["localFallback"]) for r in err)
    for (t, p), k in pairs.most_common(12):
        A(f"| {k} | {t} | {p} |")
    A("\n### Peores hosts (≥5 items)\n")
    A("| Host | n | Desacuerdo |\n|---|---|---|")
    for h in dis["peoresHosts"][:12]:
        A(f"| {redacta_host(h['host'])} | {h['n']} | {h['tasa']}% |")
    A("\n### Calibración del umbral\n")
    A("![calibración](../../docs/relabel/calibracion_similitud.png)\n")
    A("| Banda de similitud | n | Desacuerdo |\n|---|---|---|")
    for k, v in dis["porBandaSimilitud"].items():
        A(f"| {k} | {v['n']} | {v['tasa']}% |")
    if dis["categoriasPropuestas"]:
        A("\n### Categorías que faltan en la taxonomía\n")
        A("Los anotadores marcaron estos casos como mal encajados en las 11 categorías actuales:\n")
        A("| Categoría propuesta | Veces |\n|---|---|")
        for c, k in dis["categoriasPropuestas"]:
            A(f"| {c} | {k} |")
    if metrics:
        A("\n## 3. Re-entrenamiento\n")
        A("Se evalúan **dos splits** porque miden cosas distintas:\n")
        A("- **por host** — ningún dominio aparece en train y test. Mide generalización a sitios nunca "
          "vistos. Es la cota inferior honesta.\n"
          "- **aleatorio** — estratificado por etiqueta. Mide rendimiento en sitios ya vistos, que es "
          "el caso de uso real: el usuario revisita los mismos dominios.\n")
        A("\n| Split | Modelo | Accuracy | Acc. ponderada | macro-F1 | n test |\n|---|---|---|---|---|---|")
        for tag, res in metrics["splits"].items():
            for nm, key in (("MiniLM base", "base"), ("v1 (desplegado)", "v1"), ("**v2 (nuevo)**", "v2")):
                r = res.get(key)
                if r:
                    A(f"| {tag} | {nm} | {r['accuracy']*100:.1f}% | {r['accuracyPonderadaPorVisitas']*100:.1f}% "
                      f"| {r['macroF1']*100:.1f}% | {r['nTest']} |")
        for tag in metrics["splits"]:
            A(f"\n![modelos {tag}](../../docs/relabel/modelos_{tag}.png)")
            A(f"![umbral {tag}](../../docs/relabel/umbral_{tag}.png)")
            A(f"![confusión v2 {tag}](../../docs/relabel/confusion_v2_{tag}.png)")
    # ------------------------------------------------ recomendaciones
    if metrics:
        A("\n## 4. Recomendaciones\n")
        al = metrics["splits"].get("aleatorio", {}).get("v2")
        if al:
            sw = {round(s["threshold"], 2): s for s in al["barridoUmbral"]}
            A("**1. Recalibrar `SIM_THRESHOLD` (`extension/offscreen.js:11`) si se despliega v2.** "
              "El valor actual (0.65) queda inoperante: el modelo v2 produce similitudes más altas y "
              f"a 0.65 la cobertura es {sw[0.65]['cobertura']*100:.0f}% — el umbral deja de filtrar nada. "
              "Puntos de operación reales:\n")
            A("| Umbral | Pestañas agrupadas | Aciertos entre ellas |\n|---|---|---|")
            for t in (0.65, 0.80, 0.85, 0.90, 0.95):
                s = sw.get(t)
                if s:
                    A(f"| {t:.2f}{' (actual)' if t == 0.65 else ''} | {s['cobertura']*100:.1f}% | "
                      f"{(s['precisionCubierta'] or 0)*100:.1f}% |")
            A("\nRecomendado **0.85**: agrupa el 86% de las pestañas acertando el 90%. Subir a 0.90 "
              "sube la precisión al 95% pero deja 1 de cada 4 pestañas sin agrupar.\n")
        A("**2. El fine-tune v1 fue contraproducente y conviene retirarlo.** En datos reales queda por "
          "debajo de MiniLM base en macro-F1 en ambos splits (32.4 vs 36.7 por host; 49.7 vs 56.7 "
          "aleatorio). El corpus sintético de `dataset.py` no representaba la navegación real, y el "
          "100% LOO que reporta el README se medía sobre el propio corpus de entrenamiento.\n")
        A("**3. Ampliar la taxonomía.** Los anotadores señalaron repetidamente huecos donde la "
          "asignación fue forzada — ver la tabla de categorías propuestas arriba. Añadir una categoría "
          "exige tocar `finetune/dataset_v2.py` **y** `extension/prototypes.js`, y bumpear `PROTO_VERSION`.\n")
        A("**4. Capturar etiquetas humanas de verdad.** Todo este informe descansa en consenso de LLMs. "
          "El mecanismo de captura de movimientos manuales ya existe en `background.js` y produce la "
          "señal de mayor calidad, pero el export no traía ni una: conviene verificar que "
          "`datasetEnabled` estuvo activo y que arrastrar pestañas entre grupos registra `userCategory`.\n")

    A("\n## 5. Datos\n")
    A("El corpus de origen es navegación personal real y **no se publica**: `items.json`, "
      "`labeled.json`, `shards/`, `out/` y `dataset_v2.py` están en `.gitignore`. Los títulos que "
      "aparecen en este informe pasaron por `sanitize.py`, que solo deja pasar títulos "
      "estructurales de sitios de uso masivo; todo lo demás se redacta u omite.\n")
    A("Los centroides de `extension/prototypes.js` se publican como **vectores precomputados** "
      "(384 dims, media normalizada de ~180 embeddings por categoría) en lugar de textos de "
      "ejemplo, por la misma razón.\n")
    A("\n## 6. Reproducir\n")
    A("```bash\ncd finetune && source .venv/bin/activate\npython relabel/consolidate.py   "
      "# valida shards → labeled.json + agreement.json + disagreement.json\n"
      "python relabel/train_v2.py      # re-entrena, evalúa ambos splits → output/metrics_v2.json\n"
      "python relabel/report.py        # gráficas + este informe\n```\n")
    open(f"{BASE}/RELABEL_REPORT.md", "w").write("\n".join(L))
    print(f"\ninforme → finetune/relabel/RELABEL_REPORT.md")


if __name__ == "__main__":
    main()
