"""
Export the fine-tuned sentence-transformer's underlying encoder to ONNX,
quantize to int8 (q8), and lay out files in the format transformers.js expects.

Output structure (target):
    extension/models/tab-classifier-v1/
        config.json
        tokenizer.json
        tokenizer_config.json
        special_tokens_map.json
        vocab.txt
        onnx/
            model_quantized.onnx
"""
import os
import shutil
import json

from sentence_transformers import SentenceTransformer
from optimum.onnxruntime import ORTModelForFeatureExtraction, ORTQuantizer
from optimum.onnxruntime.configuration import AutoQuantizationConfig

ST_DIR = "output/finetuned"
HF_DIR = "output/hf"
ONNX_DIR = "output/onnx"
ONNX_Q_DIR = "output/onnx-quantized"
TARGET_DIR = "../extension/models/tab-classifier-v1"


def extract_hf_model():
    """Pull the underlying transformer (Bert/MiniLM) + tokenizer out of the
    SentenceTransformer wrapper so optimum can re-export it cleanly."""
    if os.path.exists(HF_DIR):
        shutil.rmtree(HF_DIR)
    print(f"loading {ST_DIR}")
    st_model = SentenceTransformer(ST_DIR)
    transformer = st_model[0]  # the Bert/MiniLM module
    inner_model = transformer.auto_model
    tokenizer = transformer.tokenizer

    os.makedirs(HF_DIR, exist_ok=True)
    inner_model.save_pretrained(HF_DIR)
    tokenizer.save_pretrained(HF_DIR)
    print(f"saved hf model to {HF_DIR}")


def export_onnx():
    if os.path.exists(ONNX_DIR):
        shutil.rmtree(ONNX_DIR)
    print(f"exporting ONNX from {HF_DIR}")
    ort = ORTModelForFeatureExtraction.from_pretrained(HF_DIR, export=True)
    ort.save_pretrained(ONNX_DIR)
    print(f"saved unquantized ONNX to {ONNX_DIR}")
    print("files:", os.listdir(ONNX_DIR))


def quantize_onnx():
    if os.path.exists(ONNX_Q_DIR):
        shutil.rmtree(ONNX_Q_DIR)
    print(f"quantizing ONNX → int8")
    quantizer = ORTQuantizer.from_pretrained(ONNX_DIR)
    qconfig = AutoQuantizationConfig.arm64(is_static=False, per_channel=False)
    quantizer.quantize(save_dir=ONNX_Q_DIR, quantization_config=qconfig)
    print(f"saved quantized ONNX to {ONNX_Q_DIR}")
    print("files:", os.listdir(ONNX_Q_DIR))


def lay_out_for_transformersjs():
    """Place files where transformers.js expects them."""
    if os.path.exists(TARGET_DIR):
        shutil.rmtree(TARGET_DIR)
    os.makedirs(os.path.join(TARGET_DIR, "onnx"), exist_ok=True)

    for fname in os.listdir(HF_DIR):
        if fname.endswith((".json", ".txt")):
            shutil.copy(os.path.join(HF_DIR, fname), os.path.join(TARGET_DIR, fname))

    onnx_q_files = os.listdir(ONNX_Q_DIR)
    quantized_file = next(
        (f for f in onnx_q_files if f.endswith(".onnx")),
        None,
    )
    if not quantized_file:
        raise RuntimeError(f"no .onnx file in {ONNX_Q_DIR}: {onnx_q_files}")

    shutil.copy(
        os.path.join(ONNX_Q_DIR, quantized_file),
        os.path.join(TARGET_DIR, "onnx", "model_quantized.onnx"),
    )

    print(f"laid out tab-classifier-v1 at {TARGET_DIR}")
    for root, _, files in os.walk(TARGET_DIR):
        for f in files:
            p = os.path.join(root, f)
            print(f"  {os.path.relpath(p, TARGET_DIR)}  ({os.path.getsize(p)/1024:.1f} KB)")


if __name__ == "__main__":
    extract_hf_model()
    export_onnx()
    quantize_onnx()
    lay_out_for_transformersjs()
