"""Source preferences service — toggle data sources on/off."""

import json
import logging
from pathlib import Path

from src.models.enums import SourceType

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent.parent / "data"
DEFAULT_SETTINGS_FILE = DATA_DIR / "settings" / "source_preferences.json"

# US sources enabled by default, EU sources disabled
US_SOURCES = {
    SourceType.OPENFDA_ENFORCEMENT,
    SourceType.FDA_WARNING_LETTER,
    SourceType.FTC_ACTION,
    SourceType.CLASS_ACTION,
    SourceType.PROP_65,
    SourceType.CPSC_RECALL,
    SourceType.NAD_DECISION,
    SourceType.STATE_AG,
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
    SourceType.NAD_DECISION: "NAD Decisions",
    SourceType.STATE_AG: "State AG Actions",
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
        self._prefs: dict[str, bool] = self._load()

    def _default_prefs(self) -> dict[str, bool]:
        prefs = {}
        for st in SourceType:
            prefs[st.value] = st in US_SOURCES
        return prefs

    def _load(self) -> dict[str, bool]:
        if self._settings_file.exists():
            try:
                with open(self._settings_file, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                # Merge with defaults so new sources get added
                defaults = self._default_prefs()
                defaults.update(saved)
                return defaults
            except (json.JSONDecodeError, IOError):
                pass
        return self._default_prefs()

    def _save(self) -> None:
        self._settings_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self._settings_file, "w", encoding="utf-8") as f:
            json.dump(self._prefs, f, indent=2)

    def is_enabled(self, source_type: SourceType) -> bool:
        return self._prefs.get(source_type.value, False)

    def get_all(self) -> list[dict]:
        result = []
        for st in SourceType:
            jurisdiction = "EU" if st in EU_SOURCES else "US"
            result.append({
                "source_key": st.value,
                "label": SOURCE_LABELS.get(st, st.name.replace("_", " ").title()),
                "enabled": self._prefs.get(st.value, False),
                "jurisdiction": jurisdiction,
            })
        return result

    def update(self, source_key: str, enabled: bool) -> bool:
        # Validate source_key exists
        valid_keys = {st.value for st in SourceType}
        if source_key not in valid_keys:
            return False
        self._prefs[source_key] = enabled
        self._save()
        return True
