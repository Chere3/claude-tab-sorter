"""
Fine-tune sentence-transformers/all-MiniLM-L6-v2 for tab classification.

Uses BatchAllTripletLoss: each batch needs >= 2 examples of each label, so we
draw balanced batches manually.
"""
import json
import os
import random
import shutil

import torch
from sentence_transformers import SentenceTransformer, InputExample, losses
from torch.utils.data import DataLoader

from dataset import build_inputs, LABEL2ID, CATEGORIES, DATASET

random.seed(42)
torch.manual_seed(42)

BASE_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
OUTPUT_DIR = "output/finetuned"
EPOCHS = 8
BATCH_SIZE = 32
PAIRS_PER_EXAMPLE = 4
WARMUP_STEPS = 100


def make_pairs(items, pairs_per_example, seed=42):
    """For each example, create `pairs_per_example` positive pairs with other
    examples from the same category. MNRL treats other items in the batch as
    implicit negatives."""
    rng = random.Random(seed)
    by_label = {}
    for text, label in items:
        by_label.setdefault(label, []).append(text)

    pairs = []
    for label, texts in by_label.items():
        for anchor in texts:
            for _ in range(pairs_per_example):
                positive = anchor
                while positive == anchor:
                    positive = rng.choice(texts)
                pairs.append(InputExample(texts=[anchor, positive]))
    rng.shuffle(pairs)
    return pairs


def main():
    print(f"loading base model: {BASE_MODEL}")
    model = SentenceTransformer(BASE_MODEL)

    items = build_inputs()
    print(f"dataset: {len(items)} examples across {len(LABEL2ID)} categories")

    pairs = make_pairs(items, PAIRS_PER_EXAMPLE)
    print(f"training pairs (anchor, positive): {len(pairs)}")

    dataloader = DataLoader(pairs, batch_size=BATCH_SIZE, shuffle=True)
    train_loss = losses.MultipleNegativesRankingLoss(model=model)

    print(f"training for {EPOCHS} epochs, batch_size={BATCH_SIZE}")

    loss_history = []  # one entry per epoch: {epoch, mean_loss, steps}
    epoch_buffer = {"sum": 0.0, "n": 0}

    def on_step(score=None, epoch=None, steps=None, **kw):
        # sentence-transformers calls callback at end of each epoch with
        # score (eval) — we don't have an evaluator, so we log epoch boundaries.
        pass

    # Patch the loss module to capture per-step values without touching the
    # SentenceTransformer.fit() loop. We wrap forward() and accumulate the
    # scalar loss; we also flush per epoch using a step counter.
    steps_per_epoch = max(1, len(dataloader))
    original_forward = train_loss.forward
    step_counter = {"i": 0, "epoch": 0}

    def wrapped_forward(*args, **kwargs):
        out = original_forward(*args, **kwargs)
        try:
            v = float(out.detach().cpu().item())
            epoch_buffer["sum"] += v
            epoch_buffer["n"] += 1
            step_counter["i"] += 1
            if step_counter["i"] % steps_per_epoch == 0:
                mean = epoch_buffer["sum"] / max(1, epoch_buffer["n"])
                loss_history.append(
                    {
                        "epoch": step_counter["epoch"] + 1,
                        "mean_loss": mean,
                        "steps": epoch_buffer["n"],
                    }
                )
                step_counter["epoch"] += 1
                epoch_buffer["sum"] = 0.0
                epoch_buffer["n"] = 0
        except Exception:
            pass
        return out

    train_loss.forward = wrapped_forward

    model.fit(
        train_objectives=[(dataloader, train_loss)],
        epochs=EPOCHS,
        warmup_steps=WARMUP_STEPS,
        show_progress_bar=False,
        optimizer_params={"lr": 2e-5},
    )

    metrics_path = "output/training_metrics.json"
    with open(metrics_path, "w") as f:
        json.dump(
            {
                "base_model": BASE_MODEL,
                "epochs": EPOCHS,
                "batch_size": BATCH_SIZE,
                "pairs_per_example": PAIRS_PER_EXAMPLE,
                "warmup_steps": WARMUP_STEPS,
                "dataset_examples": len(items),
                "training_pairs": len(pairs),
                "categories": list(CATEGORIES),
                "loss_history": loss_history,
            },
            f,
            indent=2,
        )
    print(f"wrote {metrics_path}: {len(loss_history)} epoch entries")

    if os.path.exists(OUTPUT_DIR):
        shutil.rmtree(OUTPUT_DIR)
    model.save(OUTPUT_DIR)
    print(f"saved sentence-transformers model to {OUTPUT_DIR}")

    print("\nbuilding centroids from training examples…")
    import numpy as np
    cats = list(CATEGORIES)
    centroids = {}
    for cat in cats:
        texts = DATASET[cat]
        embs = model.encode(texts, normalize_embeddings=True)
        c = embs.mean(axis=0)
        c = c / (np.linalg.norm(c) + 1e-12)
        centroids[cat] = c

    centroid_matrix = np.stack([centroids[c] for c in cats])

    print("\nsanity check — closest centroid for probes (held out):")
    probes = [
        ("[2401.12345] Attention is all you need arxiv.org/abs/2401.12345", "Investigación"),
        ("arxiv 2501.99999 paper arxiv.org/abs/2501.99999", "Investigación"),
        ("Google Scholar - cited by scholar.google.com", "Investigación"),
        ("GitHub - vercel/next.js github.com/vercel/next.js", "Desarrollo"),
        ("Stack Overflow - typescript generics stackoverflow.com", "Desarrollo"),
        ("YouTube - new music video youtube.com/watch", "Entretenimiento"),
        ("Netflix - Stranger Things netflix.com/title", "Entretenimiento"),
        ("Booking.com hotel reserva booking.com/hotel", "Viajes"),
        ("vuelo a Tokio Skyscanner skyscanner.com", "Viajes"),
        ("ChatGPT chatgpt.com", "IA"),
        ("Hugging Face Llama 3 model huggingface.co", "IA"),
        ("Amazon producto amazon.com/dp/B0", "Compras"),
        ("BBVA cuenta corriente bbva.es", "Finanzas"),
        ("Bitcoin price USD tradingview tradingview.com", "Finanzas"),
        ("Reuters breaking news reuters.com", "Noticias"),
        ("El País última hora elpais.com", "Noticias"),
        ("Coursera python course coursera.org/learn", "Aprendizaje"),
        ("Khan Academy math khanacademy.org", "Aprendizaje"),
        ("Twitter / X home x.com", "Redes Sociales"),
        ("Reddit r/programming reddit.com/r/programming", "Redes Sociales"),
        ("Gmail inbox mail.google.com", "Productividad"),
        ("Notion workspace notion.so", "Productividad"),
    ]

    probe_texts = [p[0] for p in probes]
    probe_embs = model.encode(probe_texts, normalize_embeddings=True)
    sims = probe_embs @ centroid_matrix.T

    hits = 0
    for (text, expected), row in zip(probes, sims):
        best = int(np.argmax(row))
        ok = cats[best] == expected
        if ok: hits += 1
        marker = " OK " if ok else "MISS"
        print(f"  [{marker}] {expected:>16} → {cats[best]:>16} ({row[best]:.3f})  | {text[:60]}")
    print(f"\naccuracy: {hits}/{len(probes)} = {hits/len(probes):.1%}")


if __name__ == "__main__":
    main()
