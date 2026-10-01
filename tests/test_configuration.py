import json

from app.services.configuration import DEFAULT_CONFIG, load_config, save_config


def test_missing_file_returns_defaults(tmp_path):
    assert load_config(tmp_path / "nope.json") == DEFAULT_CONFIG


def test_partial_file_is_merged_over_defaults(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"application": {"theme": "light"}}), encoding="utf-8")

    config = load_config(path)

    assert config["application"]["theme"] == "light"
    assert config["application"]["default_layout"] == DEFAULT_CONFIG["application"]["default_layout"]
    assert config["recording"] == DEFAULT_CONFIG["recording"]


def test_invalid_json_returns_defaults(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("{not json", encoding="utf-8")
    assert load_config(path) == DEFAULT_CONFIG


def test_non_object_json_returns_defaults(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("[1, 2]", encoding="utf-8")
    assert load_config(path) == DEFAULT_CONFIG


def test_defaults_are_not_mutated_by_callers(tmp_path):
    config = load_config(tmp_path / "nope.json")
    config["application"]["theme"] = "light"
    assert DEFAULT_CONFIG["application"]["theme"] == "dark"


def test_save_then_load_round_trips(tmp_path):
    path = tmp_path / "sub" / "config.json"
    config = load_config(path)
    config["recording"]["format"] = "raw"
    save_config(config, path)
    assert load_config(path) == config
