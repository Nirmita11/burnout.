"""
Loads the trained burnout-probability model (see train_model.py) and
exposes a single prediction helper.

This is an independent, secondary signal. It is shown ALONGSIDE risk.py's
rule-based analysis on the dashboard, never used to replace it.
"""

import json
from pathlib import Path

# joblib/pandas (and, transitively, scikit-learn to unpickle the model)
# are imported lazily inside the functions that actually need them, not
# here at module load — main.py imports this module unconditionally, so
# a top-level import here paid that cost on every cold start, even for
# requests that never touch the ML prediction at all (login, landing
# page, static assets, ...).
MODEL_PATH = Path(__file__).with_name("burnout_model.pkl")
METRICS_PATH = Path(__file__).with_name("model_metrics.json")

_model = None
_load_error = None


def _get_model():
    global _model, _load_error
    if _model is None and _load_error is None:
        import joblib
        try:
            _model = joblib.load(MODEL_PATH)
        except FileNotFoundError as e:
            _load_error = e
    return _model


def get_model_metrics():
    """
    Returns the real metrics dict written by the last train_model.py run,
    or None if it hasn't been generated yet.
    """

    try:
        with open(METRICS_PATH) as f:
            return json.load(f)
    except FileNotFoundError:
        return None


def predict_probability(study_hours, sleep_hours, mood):
    """
    Returns a float 0-1 burnout probability from the trained model, or
    None if burnout_model.pkl hasn't been generated yet (run train_model.py).
    """

    model = _get_model()
    if model is None:
        return None

    import pandas as pd
    X = pd.DataFrame(
        [[study_hours, sleep_hours, mood]],
        columns=["study_hours", "sleep_hours", "mood"],
    )
    return float(model.predict_proba(X)[0][1])
