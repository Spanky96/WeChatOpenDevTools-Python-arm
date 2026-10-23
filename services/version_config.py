"""Shared version profiles for mini-program and H5 injection."""
import json
from pathlib import Path

CONFIG_DIR = Path(__file__).resolve().parent.parent / "configs"


def load_versions(configs_dir=None):
    path = Path(configs_dir or CONFIG_DIR) / "versions.json"
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)["versions"]


def get_version(version_id):
    try:
        return load_versions()[version_id]
    except KeyError as exc:
        raise ValueError(f"Unsupported WeChat version: {version_id}") from exc
