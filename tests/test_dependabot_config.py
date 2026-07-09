from pathlib import Path

import yaml


def test_all_dependabot_version_updates_have_seven_day_cooldown():
    config = yaml.safe_load(Path(".github/dependabot.yml").read_text(encoding="utf-8"))

    invalid_ecosystems = [
        update["package-ecosystem"]
        for update in config["updates"]
        if update.get("cooldown", {}).get("default-days") != 7
    ]

    assert invalid_ecosystems == []
