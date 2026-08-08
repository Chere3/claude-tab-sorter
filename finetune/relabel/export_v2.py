"""
Genera los artefactos desplegables a partir del corpus re-etiquetado y del modelo v2.

Escribe:
  finetune/dataset_v2.py                     corpus real, formato compatible con train.py
  extension/prototypes_v2.js                 prototipos reales (medoids por categoría)
  extension/models/tab-classifier-v2/        modelo ONNX int8 en layout transformers.js

NO toca el modelo ni los prototipos en producción. El despliegue es una decisión aparte:
ver el bloque final que imprime los pasos.
"""
import json
import os
import shutil
import collections

import numpy as np
from sentence_transformers import SentenceTransformer

BASE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(f"{BASE}/../..")
V2_DIR = "output/finetuned-v2"
N_PROTOS = 18            # ejemplos por categoría en prototypes_v2.js
PROTO_VERSION = 5        # v4 es el desplegado; bumpear invalida la caché de embeddings

# emoji + color actuales, para no romper el histórico de stats (las claves son los títulos)
META = {
    "Desarrollo":      ("💻", "purple"),
    "Investigación":   ("🔬", "cyan"),
    "IA":              ("🤖", "blue"),
    "Redes Sociales":  ("💬", "pink"),
    "Entretenimiento": ("🎬", "red"),
    "Productividad":   ("⚡", "yellow"),
    "Compras":         ("🛒", "orange"),
    "Noticias":        ("📰", "grey"),
    "Finanzas":        ("💰", "green"),
    "Aprendizaje":     ("📚", "cyan"),
    "Viajes":          ("✈️", "blue"),
}
BARE = lambda lab: lab.split(" ", 1)[1] if " " in lab else lab   # "💻 Desarrollo" → "Desarrollo"


def text_of(r):
    return f"{r['title']} {r['url']}".strip()[:256]


def write_dataset_py(rows):
    """Formato idéntico al que consumen train.py / evaluate.py, con nombres SIN emoji."""
    bycat = collections.defaultdict(list)
    for r in rows:
        bycat[BARE(r["label"])].append(text_of(r))
    cats = [c for c in META if c in bycat]
    L = ['"""', "Corpus real de navegación, re-etiquetado por 14 anotadores LLM independientes.",
         "",
         f"{len(rows)} items únicos deduplicados de 4053 eventos de uso (jun-ago 2026).",
         "Etiquetas = consenso de anotadores, NO verdad humana. Ver relabel/RELABEL_REPORT.md.",
         "Formato de texto idéntico al de inferencia: '{title} {hostname}{path}'.",
         '"""', "", "CATEGORIES = ["]
    L += [f"    {json.dumps(c, ensure_ascii=False)}," for c in cats]
    L += ["]", "", "LABEL2ID = {c: i for i, c in enumerate(CATEGORIES)}", "", "DATASET = {"]
    for c in cats:
        L.append(f"    {json.dumps(c, ensure_ascii=False)}: [")
        for t in sorted(set(bycat[c])):
            L.append(f"        {json.dumps(t, ensure_ascii=False)},")
        L.append("    ],")
    L += ["}", "", "", "def build_inputs():", '    """Return list of (text, label_id) tuples."""',
          "    items = []", "    for cat, texts in DATASET.items():", "        label = LABEL2ID[cat]",
          "        for t in texts:", "            items.append((t, label))", "    return items", ""]
    path = f"{REPO}/finetune/dataset_v2.py"
    open(path, "w").write("\n".join(L))
    print(f"  → finetune/dataset_v2.py ({len(rows)} items, {len(cats)} categorías)")
    return {c: len(set(bycat[c])) for c in cats}


def write_prototypes_js(rows, model):
    """Emite los centroides como VECTORES precomputados, no como textos.

    El corpus es navegación personal y este archivo va a un repo público: publicar los
    títulos medoides expondría el historial. Un centroide es la media normalizada de
    ~180 embeddings, de la que no se reconstruyen los títulos de origen.

    Efecto colateral bueno: offscreen.js deja de embeber ~200 ejemplos en el primer
    arranque, así que el modelo queda listo de inmediato.
    """
    bycat = collections.defaultdict(list)
    for r in rows:
        bycat[BARE(r["label"])].append(r)
    L = ["// Generado por finetune/relabel/export_v2.py desde el corpus real re-etiquetado.",
         "//",
         "// Los centroides vienen precomputados como vectores: el corpus de origen es",
         "// navegación personal y no puede publicarse. offscreen.js los usa tal cual y se",
         "// salta la fase de embedding en el primer arranque.",
         "//",
         f"// Corpus: {len(rows)} items únicos · modelo: tab-classifier-v2",
         f"export const PROTO_VERSION = {PROTO_VERSION};", "",
         "export const PROTOTYPES = {"]
    dim = None
    for cat in META:
        if cat not in bycat:
            continue
        rs = bycat[cat]
        X = model.encode([text_of(r) for r in rs], normalize_embeddings=True, show_progress_bar=False)
        c = X.mean(axis=0)
        c /= np.linalg.norm(c) or 1
        dim = len(c)
        emoji, color = META[cat]
        vec = ",".join(f"{v:.5f}" for v in c)
        L.append(f"  {json.dumps(cat, ensure_ascii=False)}: {{")
        L.append(f'    color: "{color}",')
        L.append(f'    emoji: "{emoji}",')
        L.append(f"    n: {len(rs)},")
        L.append(f"    vector: [{vec}]")
        L.append("  },")
    L += ["};", ""]
    open(f"{REPO}/extension/prototypes_v2.js", "w").write("\n".join(L))
    kb = os.path.getsize(f"{REPO}/extension/prototypes_v2.js") / 1024
    print(f"  → extension/prototypes_v2.js (PROTO_VERSION={PROTO_VERSION}, "
          f"{len(bycat)} centroides de {dim} dims, {kb:.0f} KB, sin títulos)")


def export_onnx():
    """Encoder → ONNX → int8, en el layout que espera transformers.js."""
    try:
        from optimum.onnxruntime import ORTModelForFeatureExtraction, ORTQuantizer
        from optimum.onnxruntime.configuration import AutoQuantizationConfig
        from transformers import AutoTokenizer
    except ImportError as e:
        print(f"  ⚠️  export ONNX omitido (falta optimum: {e})")
        return False
    hf, onnx_d, q_d = "output/hf-v2", "output/onnx-v2", "output/onnx-v2-q"
    st = SentenceTransformer(V2_DIR)
    st[0].auto_model.save_pretrained(hf)
    st[0].tokenizer.save_pretrained(hf)
    m = ORTModelForFeatureExtraction.from_pretrained(hf, export=True)
    m.save_pretrained(onnx_d)
    AutoTokenizer.from_pretrained(hf).save_pretrained(onnx_d)
    q = ORTQuantizer.from_pretrained(onnx_d)
    q.quantize(save_dir=q_d, quantization_config=AutoQuantizationConfig.avx512_vnni(
        is_static=False, per_channel=False))
    tgt = f"{REPO}/extension/models/tab-classifier-v2"
    os.makedirs(f"{tgt}/onnx", exist_ok=True)
    for f in ("config.json", "tokenizer.json", "tokenizer_config.json",
              "special_tokens_map.json", "vocab.txt"):
        for src in (f"{onnx_d}/{f}", f"{hf}/{f}"):
            if os.path.exists(src):
                shutil.copy(src, f"{tgt}/{f}")
                break
    for f in os.listdir(q_d):
        if f.endswith(".onnx"):
            shutil.copy(f"{q_d}/{f}", f"{tgt}/onnx/model_quantized.onnx")
            break
    mb = os.path.getsize(f"{tgt}/onnx/model_quantized.onnx") / 1e6
    print(f"  → extension/models/tab-classifier-v2/ ({mb:.1f} MB int8)")
    return True


def main():
    rows = json.load(open(f"{BASE}/labeled.json"))
    if not os.path.isdir(V2_DIR):
        raise SystemExit(f"falta {V2_DIR}: ejecuta antes relabel/train_v2.py")
    model = SentenceTransformer(V2_DIR)
    print("generando artefactos:")
    write_dataset_py(rows)
    write_prototypes_js(rows, model)
    ok = export_onnx()
    print("\nNada de esto está desplegado todavía. Para activarlo en la extensión:")
    print("  1. mv extension/prototypes_v2.js extension/prototypes.js")
    if ok:
        print('  2. en extension/offscreen.js: MODEL_NAME = "tab-classifier-v2"')
    print("  3. recargar la extensión en chrome://extensions")
    print("  4. el bump de PROTO_VERSION recalcula los centroides solo")
    print("\nRevisar antes relabel/RELABEL_REPORT.md: el umbral SIM_THRESHOLD también debería")
    print("recalibrarse (offscreen.js:11) según el barrido del informe.")


if __name__ == "__main__":
    main()
