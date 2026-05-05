import json
from functools import lru_cache
from pathlib import Path

_FILE = Path(__file__).parent.parent / "checkov_tests_classification.json"


@lru_cache(maxsize=1)
def _data() -> dict:
    return json.loads(_FILE.read_text())


def check_categories(check_id: str) -> list[str]:
    """Return all category names a check belongs to. Empty list if unclassified."""
    return [cat["name"] for cat in _data()["categories"] if check_id in cat["items"]]


def all_categories() -> list[dict]:
    """Return the full category list with name and optional color fields."""
    return _data()["categories"]


def category_meta(name: str) -> dict:
    """Return the metadata dict for a category by name."""
    for cat in _data()["categories"]:
        if cat["name"] == name:
            return cat
    return {"name": name}


def group_findings(finding_reports: list) -> dict[str, list]:
    """
    Group FindingReport objects by their primary category.
    Reports with no classification go under 'Uncategorized'.
    Returns an ordered dict: category_name → [FindingReport, ...]
    """
    # Preserve declaration order from the JSON
    ordered: dict[str, list] = {cat["name"]: [] for cat in all_categories()}
    ordered["Uncategorized"] = []

    for fr in finding_reports:
        cats = fr.categories
        primary = cats[0] if cats else "Uncategorized"
        ordered[primary].append(fr)

    # Drop empty categories
    return {k: v for k, v in ordered.items() if v}
