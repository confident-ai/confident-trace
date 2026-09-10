"""Safe value access and bounded primitive decoding."""

import json
from enum import Enum
from itertools import islice


def get(value, key, default=None):
    if type(value) is dict:
        return value.get(key, default)
    # SDK models store data in __dict__; avoid invoking arbitrary properties.
    try:
        data = object.__getattribute__(value, "__dict__")
        if type(data) is dict and key in data:
            return data[key]
        extras = object.__getattribute__(value, "__pydantic_extra__")
        return extras.get(key, default) if type(extras) is dict else default
    except (AttributeError, TypeError):
        return default


def sequence(value, limit=128):
    return islice(value if type(value) in (list, tuple) else (), limit)


def string(value):
    # Accept enum-backed strings without coercing arbitrary objects.
    if isinstance(value, Enum):
        value = value.value
    return value if type(value) is str else None


def arguments(value):
    if type(value) is str and len(value) <= 16384:
        try:
            return json.loads(value)
        except (ValueError, RecursionError):
            pass  # Partial streaming JSON stays a string.
    return value
