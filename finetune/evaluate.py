"""
Evaluate the fine-tuned tab classifier against the base MiniLM and produce
plots for the README:

    docs/training_loss.png         - mean MNRL loss per epoch
    docs/confusion_matrix.png      - leave-one-out centroid confusion matrix
    docs/category_accuracy.png     - per-category accuracy (base vs fine-tuned)
    docs/category_similarity.png   - inter-category cosine sim heatmap
    docs/embedding_clusters.png    - 2D PCA projection of all embeddings

Also writes docs/evaluation.json with the numeric results.
"""
import json
import os
import random

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import seaborn as sns
from sklearn.decomposition import PCA
from sklearn.metrics import confusion_matrix
from sentence_transformers import SentenceTransformer

from dataset import CATEGORIES, DATASET

BASE_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
TUNED_DIR = "output/finetuned"
METRICS_JSON = "output/training_metrics.json"
DOCS_DIR = "../docs"

random.seed(42)
np.random.seed(42)
sns.set_theme(style="whitegrid", context="talk")
PALETTE = sns.color_palette("husl", n_colors=len(CATEGORIES))


def os_makedirs(p):
    os.makedirs(p, exist_ok=True)


def loo_centroid_eval(model):
    """For each example, build centroids from the *other* examples in each
    category, then classify by nearest centroid. Returns (y_true, y_pred,
    per_cat_acc, overall_acc, embeddings, labels)."""
    all_texts, all_labels = [], []
    for cat in CATEGORIES:
        for t in DATASET[cat]:
            all_texts.append(t)
            all_labels.append(cat)
    embs = model.encode(all_texts, normalize_embeddings=True, show_progress_bar=False)
    embs = np.asarray(embs)
    labels = np.asarray(all_labels)
    cats = list(CATEGORIES)

    by_cat_idx = {c: np.where(labels == c)[0] for c in cats}

    y_pred = []
    for i in range(len(all_texts)):
        centroids = []
        for c in cats:
            idx = by_cat_idx[c]
            idx_minus_i = idx[idx != i]
            if len(idx_minus_i) == 0:
                centroids.append(np.zeros(embs.shape[1]))
                continue
            v = embs[idx_minus_i].mean(axis=0)
            v = v / (np.linalg.norm(v) + 1e-12)
            centroids.append(v)
        C = np.stack(centroids)
        sims = C @ embs[i]
        y_pred.append(cats[int(np.argmax(sims))])

    y_pred = np.asarray(y_pred)
    overall = float((y_pred == labels).mean())
    per_cat = {
        c: float((y_pred[by_cat_idx[c]] == c).mean()) for c in cats
    }
    return labels, y_pred, per_cat, overall, embs, cats


def plot_training_loss(metrics, out):
    history = metrics.get("loss_history") or []
    if not history:
        print("no loss history — skipping training loss plot")
        return
    epochs = [h["epoch"] for h in history]
    losses = [h["mean_loss"] for h in history]
    fig, ax = plt.subplots(figsize=(8, 4.5))
    ax.plot(epochs, losses, marker="o", color="#7c3aed", linewidth=2.5, markersize=8)
    ax.fill_between(epochs, losses, min(losses) * 0.95, alpha=0.08, color="#7c3aed")
    ax.set_xlabel("Epoch")
    ax.set_ylabel("Mean MNRL loss")
    ax.set_title(f"Training loss — {metrics['epochs']} epochs, batch={metrics['batch_size']}")
    ax.set_xticks(epochs)
    fig.tight_layout()
    fig.savefig(out, dpi=140, bbox_inches="tight")
    plt.close(fig)
    print("wrote", out)


def plot_confusion(y_true, y_pred, cats, out):
    cm = confusion_matrix(y_true, y_pred, labels=cats)
    cm_norm = cm / cm.sum(axis=1, keepdims=True).clip(min=1)
    fig, ax = plt.subplots(figsize=(9, 7.5))
    sns.heatmap(
        cm_norm,
        annot=cm,
        fmt="d",
        cmap="Purples",
        xticklabels=cats,
        yticklabels=cats,
        cbar_kws={"label": "fracción por fila"},
        annot_kws={"size": 9},
        ax=ax,
    )
    ax.set_xlabel("predicción")
    ax.set_ylabel("etiqueta real")
    ax.set_title("Matriz de confusión (leave-one-out, nearest centroid)")
    plt.setp(ax.get_xticklabels(), rotation=35, ha="right")
    fig.tight_layout()
    fig.savefig(out, dpi=140, bbox_inches="tight")
    plt.close(fig)
    print("wrote", out)


def plot_per_category(base_acc, tuned_acc, cats, out):
    x = np.arange(len(cats))
    w = 0.38
    fig, ax = plt.subplots(figsize=(10, 5))
    bars1 = ax.bar(x - w / 2, [base_acc[c] for c in cats], w, label="MiniLM base", color="#9ca3af")
    bars2 = ax.bar(x + w / 2, [tuned_acc[c] for c in cats], w, label="Fine-tuned", color="#7c3aed")
    ax.set_xticks(x)
    ax.set_xticklabels(cats, rotation=35, ha="right")
    ax.set_ylabel("Accuracy (LOO)")
    ax.set_ylim(0, 1.05)
    ax.set_title("Per-category accuracy: base vs. fine-tuned")
    ax.legend(loc="lower right")
    ax.axhline(1.0, linestyle="--", color="#0001", linewidth=1)
    for bars in (bars1, bars2):
        for b in bars:
            v = b.get_height()
            ax.text(b.get_x() + b.get_width() / 2, v + 0.01, f"{v:.2f}", ha="center", fontsize=8)
    fig.tight_layout()
    fig.savefig(out, dpi=140, bbox_inches="tight")
    plt.close(fig)
    print("wrote", out)


def plot_similarity_heatmap(embs, labels, cats, out, title):
    centroids = []
    for c in cats:
        idx = np.where(labels == c)[0]
        v = embs[idx].mean(axis=0)
        v = v / (np.linalg.norm(v) + 1e-12)
        centroids.append(v)
    C = np.stack(centroids)
    sim = C @ C.T

    fig, ax = plt.subplots(figsize=(8.5, 7))
    sns.heatmap(
        sim,
        annot=True,
        fmt=".2f",
        cmap="rocket_r",
        xticklabels=cats,
        yticklabels=cats,
        vmin=0,
        vmax=1,
        cbar_kws={"label": "cosine sim"},
        annot_kws={"size": 8},
        ax=ax,
    )
    ax.set_title(title)
    plt.setp(ax.get_xticklabels(), rotation=35, ha="right")
    fig.tight_layout()
    fig.savefig(out, dpi=140, bbox_inches="tight")
    plt.close(fig)
    print("wrote", out)


def plot_embedding_clusters(embs, labels, cats, out, title):
    pca = PCA(n_components=2, random_state=42)
    proj = pca.fit_transform(embs)
    fig, ax = plt.subplots(figsize=(9, 7))
    for i, c in enumerate(cats):
        m = labels == c
        ax.scatter(
            proj[m, 0],
            proj[m, 1],
            s=42,
            alpha=0.75,
            color=PALETTE[i],
            label=c,
            edgecolor="white",
            linewidth=0.5,
        )
    ax.set_title(title)
    ax.set_xlabel(f"PC1 ({pca.explained_variance_ratio_[0]*100:.1f}%)")
    ax.set_ylabel(f"PC2 ({pca.explained_variance_ratio_[1]*100:.1f}%)")
    ax.legend(loc="upper right", fontsize=8, ncol=2, frameon=True)
    fig.tight_layout()
    fig.savefig(out, dpi=140, bbox_inches="tight")
    plt.close(fig)
    print("wrote", out)


def main():
    os_makedirs(DOCS_DIR)

    print("loading base model:", BASE_MODEL)
    base = SentenceTransformer(BASE_MODEL)
    print("loading fine-tuned model:", TUNED_DIR)
    tuned = SentenceTransformer(TUNED_DIR)

    print("\n=== leave-one-out evaluation ===")
    print("\n[base]")
    b_true, b_pred, b_per, b_acc, b_embs, cats = loo_centroid_eval(base)
    print(f"  overall accuracy: {b_acc:.3f}")
    print("\n[fine-tuned]")
    t_true, t_pred, t_per, t_acc, t_embs, _ = loo_centroid_eval(tuned)
    print(f"  overall accuracy: {t_acc:.3f}")
    print(f"\n  Δ accuracy: {(t_acc - b_acc) * 100:+.1f} pp")

    metrics = {}
    if os.path.exists(METRICS_JSON):
        with open(METRICS_JSON) as f:
            metrics = json.load(f)

    plot_training_loss(metrics, os.path.join(DOCS_DIR, "training_loss.png"))
    plot_confusion(t_true, t_pred, cats, os.path.join(DOCS_DIR, "confusion_matrix.png"))
    plot_per_category(b_per, t_per, cats, os.path.join(DOCS_DIR, "category_accuracy.png"))
    plot_similarity_heatmap(
        t_embs, t_true, cats,
        os.path.join(DOCS_DIR, "category_similarity.png"),
        "Inter-category cosine similarity (fine-tuned)",
    )
    plot_embedding_clusters(
        t_embs, t_true, cats,
        os.path.join(DOCS_DIR, "embedding_clusters.png"),
        "PCA(2) of fine-tuned embeddings",
    )

    summary = {
        "base_model": BASE_MODEL,
        "dataset_total": int(len(b_true)),
        "categories": cats,
        "base": {"overall_accuracy": b_acc, "per_category": b_per},
        "finetuned": {"overall_accuracy": t_acc, "per_category": t_per},
        "delta_pp": (t_acc - b_acc) * 100,
        "training": {
            "epochs": metrics.get("epochs"),
            "batch_size": metrics.get("batch_size"),
            "pairs": metrics.get("training_pairs"),
            "final_loss": (metrics.get("loss_history") or [{}])[-1].get("mean_loss"),
        },
    }
    with open(os.path.join(DOCS_DIR, "evaluation.json"), "w") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    print("\nwrote", os.path.join(DOCS_DIR, "evaluation.json"))


if __name__ == "__main__":
    main()
