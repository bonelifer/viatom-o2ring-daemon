"""Clinical SpO2 category classification (standard pulse-oximetry hypoxemia bands)."""

from __future__ import annotations

SEVERE = "Severe Hypoxemia"
MODERATE = "Moderate Hypoxemia"
MILD = "Mild Hypoxemia"
NORMAL = "Normal"


def classify(spo2: int | None) -> str | None:
    """Classify a reading per the standard SpO2 hypoxemia bands.

    Normal is 95% and above; mild hypoxemia 90-94%; moderate 85-89%; severe
    below 85% -- the widely used clinical bands for pulse oximetry (e.g. as
    summarized by the American Lung Association and hospital pulse-ox
    reference charts).

    Args:
        spo2: Blood oxygen saturation, percent.

    Returns:
        One of NORMAL, MILD, MODERATE, SEVERE, or None if ``spo2`` is missing.
    """
    if spo2 is None:
        return None
    if spo2 < 85:
        return SEVERE
    if spo2 < 90:
        return MODERATE
    if spo2 < 95:
        return MILD
    return NORMAL
