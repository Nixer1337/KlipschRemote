"""klipsch-ble — cross-platform async BLE control for Klipsch powered speakers.

Covers the protocol-identical powered line: The Fives, The Sevens, The Nines
(incl. McLaren variants). The entry point is :class:`KlipschClient`.
"""

from __future__ import annotations

from .client import (
    BleakLike,
    DeviceInfo,
    KlipschAccessError,
    KlipschClient,
    KlipschError,
    KlipschNotFoundError,
    KlipschStatus,
    PowerOffDisabledError,
)
from .constants import (
    EQ_MAX,
    EQ_MIN,
    Input,
    MAX_VOLUME_RAW,
    input_name,
    normalize_input,
    volume_percent_to_raw,
    volume_raw_to_db,
    volume_raw_to_percent,
)
from .discovery import Discovered, discover, find_address
from .models import (
    FEATURES,
    KlipschModel,
    ModelInfo,
    model_from_name,
    model_from_number,
    resolve_model,
)

__version__ = "0.2.0"

__all__ = [
    "__version__",
    # client
    "KlipschClient",
    "KlipschStatus",
    "DeviceInfo",
    "KlipschError",
    "KlipschNotFoundError",
    "KlipschAccessError",
    "PowerOffDisabledError",
    "BleakLike",
    # models
    "KlipschModel",
    "ModelInfo",
    "FEATURES",
    "model_from_number",
    "model_from_name",
    "resolve_model",
    # constants / helpers
    "Input",
    "MAX_VOLUME_RAW",
    "EQ_MIN",
    "EQ_MAX",
    "normalize_input",
    "input_name",
    "volume_percent_to_raw",
    "volume_raw_to_percent",
    "volume_raw_to_db",
    # discovery
    "discover",
    "find_address",
    "Discovered",
]
