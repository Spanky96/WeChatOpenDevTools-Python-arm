"""Capture limits shared by the manager, session daemon, and injected agent."""
import json
import os
import tempfile

CONFIG_FILE = "/tmp/wechat_rea/capture_settings.json"
DEFAULTS = {"request_body": 32768, "response_body": 131072, "headers": 16384}


def validate_config(values):
    if not isinstance(values, dict) or set(values) - set(DEFAULTS):
        raise ValueError("unknown capture setting")
    result = dict(DEFAULTS)
    for key, value in values.items():
        if type(value) is not int or not 256 <= value <= 1048576:
            raise ValueError("capture limits must be integers between 256 and 1048576")
        result[key] = value
    return result


def load_config():
    try:
        with open(CONFIG_FILE, encoding="utf-8") as stream:
            return validate_config(json.load(stream))
    except (OSError, ValueError, TypeError):
        return dict(DEFAULTS)


def save_config(values):
    config = validate_config(values)
    directory = os.path.dirname(CONFIG_FILE)
    os.makedirs(directory, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=directory, prefix="capture_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(config, stream)
        os.replace(temp, CONFIG_FILE)
    finally:
        if os.path.exists(temp):
            os.remove(temp)
    return config
