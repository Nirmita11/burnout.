"""
Trains a supplementary ML burnout-probability model.

This is DELIBERATELY separate from risk.py's rule-based engine. The rule
engine stays the explainable, primary signal shown on the dashboard; this
model adds a second, independent probability estimate ("what does a model
trained on many student-days think, given just today's numbers") — shown
alongside it, never replacing it.

Run directly to (re)train and save the model:

    python train_model.py

Produces burnout_model.pkl and model_metrics.json next to this file. The
metrics file is what the "Model Insights" page reads — real numbers from
this actual run, never hand-typed into the template.
"""

import json

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
import joblib

MODEL_PATH = "burnout_model.pkl"
METRICS_PATH = "model_metrics.json"


def generate_synthetic_dataset(n=6000, seed=42):
    """
    Synthetic student-days: study_hours, sleep_hours, mood -> burnout label.

    The label is NOT a clean threshold/lookup. It's drawn from a probability
    that follows a realistic direction (more study, less sleep, lower mood
    -> higher burnout chance) but with random noise mixed into the log-odds
    before sampling, so the same feature values can land on either label
    some of the time — same as real student data would.
    """

    rng = np.random.default_rng(seed)

    study_hours = rng.uniform(0, 12, n)
    sleep_hours = rng.uniform(3, 10, n)
    mood = rng.integers(1, 6, n)  # 1-5 inclusive

    # Realistic direction: study up / sleep down / mood down -> higher risk.
    logit = (
        0.35 * (study_hours - 5)
        - 0.55 * (sleep_hours - 7)
        - 0.60 * (mood - 3)
    )

    # Random noise mixed into the log-odds so labels aren't a clean function
    # of the inputs — some low-risk-looking days still burn out, and vice versa.
    noise = rng.normal(0, 1.2, n)

    probability = 1 / (1 + np.exp(-(logit + noise)))
    label = rng.binomial(1, probability)

    return pd.DataFrame({
        "study_hours": study_hours,
        "sleep_hours": sleep_hours,
        "mood": mood,
        "burnout": label,
    })


def train():
    df = generate_synthetic_dataset()

    X = df[["study_hours", "sleep_hours", "mood"]]
    y = df["burnout"]

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y
    )

    model = Pipeline([
        ("scaler", StandardScaler()),
        ("clf", LogisticRegression()),
    ])
    model.fit(X_train, y_train)

    y_pred = model.predict(X_test)

    accuracy = accuracy_score(y_test, y_pred)
    report_dict = classification_report(
        y_test, y_pred, target_names=["no_burnout", "burnout"], output_dict=True
    )
    cm = confusion_matrix(y_test, y_pred)

    print("=" * 60)
    print("Synthetic dataset:", len(df), "student-days")
    print("Burnout label balance:", df["burnout"].value_counts(normalize=True).round(3).to_dict())
    print("=" * 60)
    print(f"Test accuracy: {accuracy:.4f}")
    print()
    print("Classification report (test set):")
    print(classification_report(y_test, y_pred, target_names=["no_burnout", "burnout"]))
    print("Confusion matrix (rows=actual, cols=predicted):")
    print(cm)
    print("=" * 60)

    joblib.dump(model, MODEL_PATH)
    print(f"Saved trained model -> {MODEL_PATH}")

    metrics = {
        "dataset_size": len(df),
        "test_size": len(y_test),
        "accuracy": round(accuracy, 4),
        "no_burnout": {
            "precision": round(report_dict["no_burnout"]["precision"], 4),
            "recall": round(report_dict["no_burnout"]["recall"], 4),
            "f1": round(report_dict["no_burnout"]["f1-score"], 4),
            "support": int(report_dict["no_burnout"]["support"]),
        },
        "burnout": {
            "precision": round(report_dict["burnout"]["precision"], 4),
            "recall": round(report_dict["burnout"]["recall"], 4),
            "f1": round(report_dict["burnout"]["f1-score"], 4),
            "support": int(report_dict["burnout"]["support"]),
        },
        "confusion_matrix": cm.tolist(),
        "model": "LogisticRegression (scikit-learn, StandardScaler pipeline)",
        "features": ["study_hours", "sleep_hours", "mood"],
    }
    with open(METRICS_PATH, "w") as f:
        json.dump(metrics, f, indent=2)
    print(f"Saved metrics -> {METRICS_PATH}")

    return model


if __name__ == "__main__":
    train()
