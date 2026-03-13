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
