"""
Reconciles the two independent burnout signals shown on the dashboard...
"""

_EXPECTED_BAND = {
    "Low": (0.0, 0.45),
    "Moderate": (0.25, 0.75),
    "High": (0.55, 1.0),
}


def reconcile_signals(rule_level, ml_probability):
    if ml_probability is None or rule_level not in _EXPECTED_BAND:
        return {"agree": None, "note": None}

    low, high = _EXPECTED_BAND[rule_level]
    if low <= ml_probability <= high:
        return {"agree": True, "note": None}

    if ml_probability > high:
        note = (
            "Today's numbers alone score higher on the model than your "
            "recent trend would suggest. Your plan still follows the trend "
            "signal, but today may be worth a closer look on its own."
        )
    else:
        note = (
            "Today's numbers alone score lower on the model than your "
            "recent trend would suggest. That's not a reason to ignore the "
            "trend — a single good day doesn't undo a building pattern — "
            "but it's a reasonable sign to watch for continued improvement."
        )

    return {"agree": False, "note": note}
