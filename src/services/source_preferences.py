"""Source preferences service — toggle data sources on/off."""

import json
import logging
from pathlib import Path

from src.models.enums import SourceType

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent.parent / "data"
DEFAULT_SETTINGS_FILE = DATA_DIR / "settings" / "source_preferences.json"

DEFAULT_CATEGORIES: dict[str, list[str]] = {
    "eu_rapex": ["Cosmetics", "Food contact materials", "Food-imitating products", "Chemical products"],
}

DEFAULT_LOOKBACK_DAYS = 1825  # 5 years

# US sources enabled by default, EU sources disabled
US_SOURCES = {
    SourceType.OPENFDA_ENFORCEMENT,
    SourceType.FDA_WARNING_LETTER,
    SourceType.FTC_ACTION,
    SourceType.CLASS_ACTION,
    SourceType.PROP_65,
    SourceType.CPSC_RECALL,
    SourceType.STATE_AG,
    SourceType.NAD_DECISION,
    SourceType.FEDERAL_REGISTER,
    SourceType.FDA_GUIDANCE,
}

EU_SOURCES = {
    SourceType.EU_RAPEX,
    SourceType.EU_RASFF,
    SourceType.EU_SCCS,
    SourceType.EU_ECHA_REACH,
    SourceType.EU_OFFICIAL_JOURNAL,
    SourceType.IFRA_AMENDMENT,
}

SOURCE_LABELS = {
    SourceType.OPENFDA_ENFORCEMENT: "openFDA Enforcement",
    SourceType.FDA_WARNING_LETTER: "FDA Warning Letters",
    SourceType.FTC_ACTION: "FTC Actions",
    SourceType.CLASS_ACTION: "Class Actions",
    SourceType.PROP_65: "Prop 65 Notices",
    SourceType.CPSC_RECALL: "CPSC Recalls",
    SourceType.STATE_AG: "State AG Actions",
    SourceType.NAD_DECISION: "NAD Decisions",
    SourceType.EU_RAPEX: "EU Safety Gate (RAPEX)",
    SourceType.EU_RASFF: "EU RASFF Food/Feed Alerts",
    SourceType.EU_SCCS: "EU SCCS Opinions",
    SourceType.EU_ECHA_REACH: "ECHA/REACH Substances",
    SourceType.FEDERAL_REGISTER: "Federal Register",
    SourceType.FDA_GUIDANCE: "FDA Guidance Documents",
    SourceType.EU_OFFICIAL_JOURNAL: "EU Official Journal (EUR-Lex)",
    SourceType.IFRA_AMENDMENT: "IFRA Standards Amendments",
}


class SourcePreferencesService:
    """Manages which data sources are enabled/disabled."""

    def __init__(self, settings_file: Path | None = None):
        self._settings_file = settings_file or DEFAULT_SETTINGS_FILE
        self._prefs: dict[str, bool] = {}
        self._categories: dict[str, list[str]] = {}
        self._lookback: dict[str, int] = {}
        self._load()

    def _default_prefs(self) -> dict[str, bool]:
        prefs = {}
        for st in SourceType:
            prefs[st.value] = st in US_SOURCES
        return prefs

    def _load(self) -> None:
        defaults = self._default_prefs()
        self._categories = dict(DEFAULT_CATEGORIES)
        self._lookback = {}
        if self._settings_file.exists():
            try:
                with open(self._settings_file, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                if isinstance(saved, dict):
                    if "enabled" in saved:
                        defaults.update(saved["enabled"])
                        self._categories.update(saved.get("categories", {}))
                        self._lookback.update(saved.get("lookback_days", {}))
                    else:
                        # Legacy format: flat dict of booleans
                        defaults.update(saved)
            except (json.JSONDecodeError, IOError):
                pass
        self._prefs = defaults

    def _save(self) -> None:
        self._settings_file.parent.mkdir(parents=True, exist_ok=True)
        data = {"enabled": self._prefs, "categories": self._categories}
        if self._lookback:
            data["lookback_days"] = self._lookback
        with open(self._settings_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def is_enabled(self, source_type: SourceType) -> bool:
        return self._prefs.get(source_type.value, False)

    def get_categories(self, source_key: str) -> list[str] | None:
        return self._categories.get(source_key)

    def update_categories(self, source_key: str, categories: list[str]) -> bool:
        valid_keys = {st.value for st in SourceType}
        if source_key not in valid_keys:
            return False
        self._categories[source_key] = categories
        self._save()
        return True

    def get_lookback_days(self, source_key: str) -> int:
        """Return the lookback window in days for a source (default 1825 = 5 years)."""
        return self._lookback.get(source_key, DEFAULT_LOOKBACK_DAYS)

    def update_lookback_days(self, source_key: str, days: int) -> bool:
        valid_keys = {st.value for st in SourceType}
        if source_key not in valid_keys:
            return False
        days = max(30, min(days, 36500))  # clamp 30 days to 100 years
        self._lookback[source_key] = days
        self._save()
        return True

    def get_all(self) -> list[dict]:
        result = []
        for st in SourceType:
            jurisdiction = "EU" if st in EU_SOURCES else "US"
            entry: dict = {
                "source_key": st.value,
                "label": SOURCE_LABELS.get(st, st.name.replace("_", " ").title()),
                "enabled": self._prefs.get(st.value, False),
                "jurisdiction": jurisdiction,
                "lookback_days": self._lookback.get(st.value, DEFAULT_LOOKBACK_DAYS),
            }
            cats = self._categories.get(st.value)
            if cats is not None:
                entry["categories"] = cats
            result.append(entry)
        return result

    def update(self, source_key: str, enabled: bool) -> bool:
        # Validate source_key exists
        valid_keys = {st.value for st in SourceType}
        if source_key not in valid_keys:
            return False
        self._prefs[source_key] = enabled
        self._save()
        return True
