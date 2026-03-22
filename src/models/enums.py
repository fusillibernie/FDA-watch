from enum import Enum


class ProductCategory(str, Enum):
    FOOD = "food"
    DIETARY_SUPPLEMENT = "dietary_supplement"
    COSMETIC = "cosmetic"
    OTC_DRUG = "otc_drug"
    DEVICE = "device"


class ViolationType(str, Enum):
    ADULTERATION = "adulteration"
    MISBRANDING = "misbranding"
    GMP_VIOLATION = "gmp_violation"
    UNDECLARED_ALLERGEN = "undeclared_allergen"
    UNDECLARED_INGREDIENT = "undeclared_ingredient"
    UNAPPROVED_DRUG_CLAIM = "unapproved_drug_claim"
    STRUCTURE_FUNCTION_CLAIM = "structure_function_claim"
    LABELING_VIOLATION = "labeling_violation"
    CONTAMINATION = "contamination"
    CGMP_DIETARY_SUPPLEMENT = "cgmp_dietary_supplement"
    UNAUTHORIZED_COLOR_ADDITIVE = "unauthorized_color_additive"
    DECEPTIVE_ADVERTISING = "deceptive_advertising"
    UNSUBSTANTIATED_CLAIM = "unsubstantiated_claim"
    PRODUCT_SAFETY_RISK = "product_safety_risk"
    FOOD_SAFETY_ALERT = "food_safety_alert"
    RESTRICTED_SUBSTANCE = "restricted_substance"
    COSMETIC_SAFETY_CONCERN = "cosmetic_safety_concern"
    OTHER = "other"


class Severity(str, Enum):
    CLASS_I = "class_i"
    CLASS_II = "class_ii"
    CLASS_III = "class_iii"
    WARNING = "warning"
    ADVISORY = "advisory"


class SourceType(str, Enum):
    OPENFDA_ENFORCEMENT = "openfda_enforcement"
    FDA_WARNING_LETTER = "fda_warning_letter"
    FTC_ACTION = "ftc_action"
    CLASS_ACTION = "class_action"
    PROP_65 = "prop_65"
    CPSC_RECALL = "cpsc_recall"
    STATE_AG = "state_ag"
    NAD_DECISION = "nad_decision"
    EU_RAPEX = "eu_rapex"
    EU_RASFF = "eu_rasff"
    EU_SCCS = "eu_sccs"
    EU_ECHA_REACH = "eu_echa_reach"
    FEDERAL_REGISTER = "federal_register"
    FDA_GUIDANCE = "fda_guidance"
    EU_OFFICIAL_JOURNAL = "eu_official_journal"
    IFRA_AMENDMENT = "ifra_amendment"


class RegulationStage(str, Enum):
    PROPOSED_RULE = "proposed_rule"
    FINAL_RULE = "final_rule"
    INTERIM_FINAL_RULE = "interim_final_rule"
    ADVANCE_NOTICE = "advance_notice"
    GUIDANCE_DRAFT = "guidance_draft"
    GUIDANCE_FINAL = "guidance_final"
    AMENDMENT = "amendment"
    NOTICE = "notice"
