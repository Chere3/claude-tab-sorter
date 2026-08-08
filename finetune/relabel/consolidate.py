"""
Consolida las anotaciones de los 14 shards en un corpus etiquetado único.

Produce:
  - labeled.json          corpus final (item + etiqueta consenso + metadatos de acuerdo)
  - agreement.json        acuerdo inter-anotador sobre el gold set (Fleiss' kappa)
  - disagreement.json     desacuerdo modelo-local vs. anotadores (la "tasa de error")

Nota metodológica: el dataset original no contiene ninguna etiqueta humana
(0/4053 con userCategory). Lo que aquí se llama "error" del modelo local es
desacuerdo contra el consenso de anotadores LLM, no accuracy contra ground truth.
"""
import json
import os
import collections
import itertools

BASE = os.path.dirname(os.path.abspath(__file__))
CATEGORIES = [
    "💻 Desarrollo", "🔬 Investigación", "🤖 IA", "💬 Redes Sociales",
    "🎬 Entretenimiento", "⚡ Productividad", "🛒 Compras", "📰 Noticias",
    "💰 Finanzas", "📚 Aprendizaje", "✈️ Viajes",
]
CATSET = set(CATEGORIES)


def load():
    items = {a["id"]: a for a in json.load(open(f"{BASE}/items.json"))}
    manifest = json.load(open(f"{BASE}/manifest.json"))
    gold = set(manifest["goldIds"])
    shards, problems = {}, []
    for i in range(1, 15):
        path = f"{BASE}/out/shard-{i:02d}.json"
        if not os.path.exists(path):
            problems.append(f"shard-{i:02d}: FALTA el archivo de salida")
            continue
        try:
            raw = json.load(open(path))
        except json.JSONDecodeError as e:
            problems.append(f"shard-{i:02d}: JSON inválido ({e})")
            continue
        expected = {a["id"] for a in json.load(open(f"{BASE}/shards/shard-{i:02d}.json"))}
        got, clean = set(), {}
        for r in raw:
            rid, cat = r.get("id"), r.get("category")
            if rid not in expected:
                problems.append(f"shard-{i:02d}: id fuera del lote → {rid}")
                continue
            if cat not in CATSET:
                problems.append(f"shard-{i:02d}: categoría inválida en {rid} → {cat!r}")
                continue
            got.add(rid)
            clean[rid] = r
        missing = expected - got
        if missing:
            problems.append(f"shard-{i:02d}: faltan {len(missing)} items (p.ej. {sorted(missing)[:3]})")
        shards[i] = clean
    return items, gold, shards, problems


def fleiss_kappa(table):
    """table: lista de dicts categoria->conteo, un dict por item. Anotadores fijos por item."""
    table = [t for t in table if sum(t.values()) > 1]
    if not table:
        return None
    n = sum(table[0].values())
    if any(sum(t.values()) != n for t in table):
        return None  # kappa de Fleiss exige el mismo nº de anotadores por item
    N = len(table)
    p_j = {c: sum(t.get(c, 0) for t in table) / (N * n) for c in CATEGORIES}
    P_i = [(sum(v * v for v in t.values()) - n) / (n * (n - 1)) for t in table]
    P_bar = sum(P_i) / N
    P_e = sum(v * v for v in p_j.values())
    return None if P_e >= 1 else (P_bar - P_e) / (1 - P_e)


def main():
    items, gold, shards, problems = load()
    ok_shards = sorted(shards)
    report = {"shardsPresentes": ok_shards, "problemas": problems}

    # ---------- acuerdo inter-anotador sobre el gold set ----------
    gold_votes, per_item = {}, []
    for gid in sorted(gold):
        votes = [shards[i][gid]["category"] for i in ok_shards if gid in shards[i]]
        if not votes:
            continue
        cnt = collections.Counter(votes)
        top, n_top = cnt.most_common(1)[0]
        gold_votes[gid] = {"consensus": top, "votes": dict(cnt),
                           "agreement": round(n_top / len(votes), 4), "n": len(votes)}
        per_item.append(dict(cnt))

    kappa = fleiss_kappa(per_item)
    unanimous = sum(1 for v in gold_votes.values() if v["agreement"] == 1.0)
    pairwise = []
    for a, b in itertools.combinations(ok_shards, 2):
        common = [g for g in gold_votes if g in shards[a] and g in shards[b]]
        if common:
            pairwise.append(sum(shards[a][g]["category"] == shards[b][g]["category"]
                                for g in common) / len(common))
    agreement = {
        "goldItems": len(gold_votes),
        "anotadoresPorItem": len(ok_shards),
        "fleissKappa": round(kappa, 4) if kappa is not None else None,
        "acuerdoUnanime": unanimous,
        "acuerdoUnanimePct": round(100 * unanimous / len(gold_votes), 2) if gold_votes else None,
        "acuerdoMedioPorItem": round(sum(v["agreement"] for v in gold_votes.values()) / len(gold_votes), 4) if gold_votes else None,
        "acuerdoParPromedio": round(sum(pairwise) / len(pairwise), 4) if pairwise else None,
        "itemsMasDisputados": sorted(
            [{"id": g, "title": items[g]["title"][:70], "host": items[g]["host"], **v}
             for g, v in gold_votes.items()], key=lambda d: d["agreement"])[:15],
    }

    # ---------- corpus final ----------
    labeled, dupes = [], collections.defaultdict(list)
    for i in ok_shards:
        for rid, r in shards[i].items():
            dupes[rid].append((i, r))
    for rid, entries in sorted(dupes.items()):
        it = items[rid]
        if rid in gold:
            g = gold_votes[rid]
            cat, conf, src = g["consensus"], g["agreement"], "consenso"
        else:
            cat, conf, src = entries[0][1]["category"], 1.0, f"shard-{entries[0][0]:02d}"
        confs = [e[1].get("confidence") for e in entries]
        labeled.append({
            "id": rid, "title": it["title"], "url": it["url"], "host": it["host"],
            "n": it["n"], "label": cat, "labelSource": src, "annotatorAgreement": conf,
            "annotatorConfidence": collections.Counter(c for c in confs if c).most_common(1)[0][0] if any(confs) else None,
            "localCategory": it["localCategory"], "localFallback": it["localFallback"],
            "simMean": it["simMean"],
            "proposed": [e[1].get("proposedCategory") for e in entries if e[1].get("proposedCategory")],
        })

    # ---------- desacuerdo vs. modelo local ----------
    conf_matrix = collections.defaultdict(collections.Counter)
    per_cat = collections.defaultdict(lambda: {"n": 0, "dis": 0})
    per_host = collections.defaultdict(lambda: {"n": 0, "dis": 0})
    bands = {"<0.65": [0, 0], "0.65-0.75": [0, 0], "0.75-0.85": [0, 0], ">=0.85": [0, 0]}
    w_tot = w_dis = 0
    for r in labeled:
        pred = r["localFallback"]          # argmax del modelo, exista o no umbral
        truth = r["label"]
        dis = pred != truth
        conf_matrix[truth][pred] += 1
        per_cat[truth]["n"] += 1
        per_cat[truth]["dis"] += dis
        per_host[r["host"]]["n"] += 1
        per_host[r["host"]]["dis"] += dis
        w_tot += r["n"]
        w_dis += r["n"] * dis
        s = r["simMean"]
        if s is not None:
            b = "<0.65" if s < .65 else "0.65-0.75" if s < .75 else "0.75-0.85" if s < .85 else ">=0.85"
            bands[b][0] += 1
            bands[b][1] += dis

    n = len(labeled)
    dis_total = sum(v["dis"] for v in per_cat.values())
    disagreement = {
        "nItemsUnicos": n,
        "tasaDesacuerdoItems": round(100 * dis_total / n, 2) if n else None,
        "tasaDesacuerdoPonderadaPorVisitas": round(100 * w_dis / w_tot, 2) if w_tot else None,
        "porCategoriaVerdadera": {
            c: {"n": v["n"], "desacuerdos": v["dis"],
                "recallLocal": round(100 * (v["n"] - v["dis"]) / v["n"], 2) if v["n"] else None}
            for c, v in sorted(per_cat.items(), key=lambda kv: -kv[1]["n"])},
        "porBandaSimilitud": {
            k: {"n": v[0], "desacuerdos": v[1],
                "tasa": round(100 * v[1] / v[0], 2) if v[0] else None} for k, v in bands.items()},
        "peoresHosts": sorted(
            [{"host": h, "n": v["n"], "desacuerdos": v["dis"],
              "tasa": round(100 * v["dis"] / v["n"], 2)}
             for h, v in per_host.items() if v["n"] >= 5],
            key=lambda d: (-d["tasa"], -d["n"]))[:20],
        "matrizConfusion": {t: dict(p) for t, p in conf_matrix.items()},
        "categoriasPropuestas": collections.Counter(
            p for r in labeled for p in r["proposed"]).most_common(12),
    }

    json.dump(labeled, open(f"{BASE}/labeled.json", "w"), ensure_ascii=False, indent=1)
    json.dump(agreement, open(f"{BASE}/agreement.json", "w"), ensure_ascii=False, indent=2)
    json.dump(disagreement, open(f"{BASE}/disagreement.json", "w"), ensure_ascii=False, indent=2)

    print(f"shards ok: {len(ok_shards)}/14   items etiquetados: {n}")
    if problems:
        print(f"\n⚠️  {len(problems)} problemas de validación:")
        for p in problems[:20]:
            print("   -", p)
    print(f"\nAcuerdo inter-anotador (gold n={agreement['goldItems']}, {len(ok_shards)} anotadores):")
    print(f"   Fleiss' kappa        {agreement['fleissKappa']}")
    print(f"   unánimes             {agreement['acuerdoUnanime']}/{agreement['goldItems']} ({agreement['acuerdoUnanimePct']}%)")
    print(f"   acuerdo medio/item   {agreement['acuerdoMedioPorItem']}")
    print(f"   acuerdo par a par    {agreement['acuerdoParPromedio']}")
    print(f"\nDesacuerdo del modelo local vs. consenso:")
    print(f"   por item único       {disagreement['tasaDesacuerdoItems']}%")
    print(f"   ponderado x visitas  {disagreement['tasaDesacuerdoPonderadaPorVisitas']}%")
    print("   por banda de similitud:")
    for k, v in disagreement["porBandaSimilitud"].items():
        print(f"      {k:>10}  n={v['n']:4d}  desacuerdo={v['tasa']}%")
    print("\n   recall del modelo local por categoría verdadera:")
    for c, v in disagreement["porCategoriaVerdadera"].items():
        print(f"      {c:<22} n={v['n']:4d}  recall={v['recallLocal']}%")
    if disagreement["categoriasPropuestas"]:
        print("\n   categorías que los anotadores echaron en falta:")
        for c, k in disagreement["categoriasPropuestas"]:
            print(f"      {c}: {k}")


if __name__ == "__main__":
    main()
