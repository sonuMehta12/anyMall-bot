# app/services/question_templates.py
#
# Evergreen fallback templates for suggested questions.
#
# Used when:
#   - Cache miss and no nightly-generated questions available (cold start)
#   - LLM generation fails
#   - Generated questions fail validation after one regeneration attempt
#
# Templates are keyed by language code.  Each language has exactly 4
# questions — matching the product requirement.
#
# For 2-pet households, the caller adjusts targets to the correct mix:
#   pet_a, pet_b, both, pet_a
# The templates themselves use pet_a as the default target since they
# are generic enough for any pet.

from __future__ import annotations

from typing import Any


# ── AnyMall-chan evergreen questions ─────────────────────────────────────────
# These cover general pet care — food, health, behavior, daily life.
# They are safe, module-neutral, and work for any species.

ANYMALLCHAN_EVERGREEN: dict[str, list[dict[str, str]]] = {
    "EN": [
        {"text": "What should I know about caring for my pet?", "target": "pet_a", "reason_type": "evergreen"},
        {"text": "Is my pet eating and sleeping well enough?", "target": "pet_a", "reason_type": "evergreen"},
        {"text": "What everyday signs should I watch for?", "target": "pet_a", "reason_type": "evergreen"},
        {"text": "How do I know if my pet is happy and healthy?", "target": "pet_a", "reason_type": "evergreen"},
    ],
    "JA": [
        {"text": "\u30da\u30c3\u30c8\u306e\u30b1\u30a2\u3067\u5927\u5207\u306a\u3053\u3068\u306f\u4f55\u3067\u3059\u304b\uff1f", "target": "pet_a", "reason_type": "evergreen"},
        {"text": "\u98df\u4e8b\u3084\u7761\u7720\u306f\u5341\u5206\u3067\u3059\u304b\uff1f", "target": "pet_a", "reason_type": "evergreen"},
        {"text": "\u6bce\u65e5\u4f55\u3092\u30c1\u30a7\u30c3\u30af\u3059\u308c\u3070\u3044\u3044\u3067\u3059\u304b\uff1f", "target": "pet_a", "reason_type": "evergreen"},
        {"text": "\u3046\u3061\u306e\u30da\u30c3\u30c8\u306f\u5143\u6c17\u304b\u3069\u3046\u304b\u3001\u3069\u3046\u5224\u65ad\u3059\u308c\u3070\u3044\u3044\uff1f", "target": "pet_a", "reason_type": "evergreen"},
    ],
    "KO": [
        {"text": "\ubc18\ub824\ub3d9\ubb3c \ub3cc\ubcf4\uae30\uc5d0\uc11c \uc911\uc694\ud55c \uac83\uc740 \ubb34\uc5c7\uc778\uac00\uc694?", "target": "pet_a", "reason_type": "evergreen"},
        {"text": "\uc2dd\uc0ac\uc640 \uc218\uba74\uc740 \ucda9\ubd84\ud55c\uac00\uc694?", "target": "pet_a", "reason_type": "evergreen"},
        {"text": "\ub9e4\uc77c \ubb34\uc5c7\uc744 \ud655\uc778\ud574\uc57c \ud558\ub098\uc694?", "target": "pet_a", "reason_type": "evergreen"},
        {"text": "\ubc18\ub824\ub3d9\ubb3c\uc774 \uac74\uac15\ud55c\uc9c0 \uc5b4\ub5bb\uac8c \uc54c \uc218 \uc788\ub098\uc694?", "target": "pet_a", "reason_type": "evergreen"},
    ],
    "ZH": [
        {"text": "\u5ba0\u7269\u62a4\u7406\u4e2d\u6700\u91cd\u8981\u7684\u662f\u4ec0\u4e48\uff1f", "target": "pet_a", "reason_type": "evergreen"},
        {"text": "\u5b83\u7684\u996e\u98df\u548c\u7761\u7720\u5145\u8db3\u5417\uff1f", "target": "pet_a", "reason_type": "evergreen"},
        {"text": "\u6bcf\u5929\u8981\u6ce8\u610f\u4ec0\u4e48\uff1f", "target": "pet_a", "reason_type": "evergreen"},
        {"text": "\u600e\u4e48\u5224\u65ad\u5b83\u662f\u5426\u5065\u5eb7\u5feb\u4e50\uff1f", "target": "pet_a", "reason_type": "evergreen"},
    ],
    "TH": [
        {"text": "\u0e04\u0e27\u0e23\u0e23\u0e39\u0e49\u0e2d\u0e30\u0e44\u0e23\u0e1a\u0e49\u0e32\u0e07\u0e40\u0e01\u0e35\u0e48\u0e22\u0e27\u0e01\u0e31\u0e1a\u0e01\u0e32\u0e23\u0e14\u0e39\u0e41\u0e25\u0e2a\u0e31\u0e15\u0e27\u0e4c\u0e40\u0e25\u0e35\u0e49\u0e22\u0e07?", "target": "pet_a", "reason_type": "evergreen"},
        {"text": "\u0e2d\u0e32\u0e2b\u0e32\u0e23\u0e41\u0e25\u0e30\u0e01\u0e32\u0e23\u0e19\u0e2d\u0e19\u0e40\u0e1e\u0e35\u0e22\u0e07\u0e1e\u0e2d\u0e44\u0e2b\u0e21?", "target": "pet_a", "reason_type": "evergreen"},
        {"text": "\u0e04\u0e27\u0e23\u0e2a\u0e31\u0e07\u0e40\u0e01\u0e15\u0e2d\u0e30\u0e44\u0e23\u0e17\u0e38\u0e01\u0e27\u0e31\u0e19?", "target": "pet_a", "reason_type": "evergreen"},
        {"text": "\u0e23\u0e39\u0e49\u0e44\u0e14\u0e49\u0e2d\u0e22\u0e48\u0e32\u0e07\u0e44\u0e23\u0e27\u0e48\u0e32\u0e2a\u0e31\u0e15\u0e27\u0e4c\u0e40\u0e25\u0e35\u0e49\u0e22\u0e07\u0e2a\u0e38\u0e02\u0e20\u0e32\u0e1e\u0e14\u0e35?", "target": "pet_a", "reason_type": "evergreen"},
    ],
    "ID": [
        {"text": "Apa yang perlu saya ketahui tentang perawatan hewan?", "target": "pet_a", "reason_type": "evergreen"},
        {"text": "Apakah pola makan dan tidurnya sudah cukup baik?", "target": "pet_a", "reason_type": "evergreen"},
        {"text": "Tanda-tanda apa yang harus diperhatikan setiap hari?", "target": "pet_a", "reason_type": "evergreen"},
        {"text": "Bagaimana cara tahu hewan peliharaan saya sehat?", "target": "pet_a", "reason_type": "evergreen"},
    ],
    "VI": [
        {"text": "T\u00f4i c\u1ea7n bi\u1ebft g\u00ec v\u1ec1 vi\u1ec7c ch\u0103m s\u00f3c th\u00fa c\u01b0ng?", "target": "pet_a", "reason_type": "evergreen"},
        {"text": "Th\u00fa c\u01b0ng c\u1ee7a t\u00f4i \u0103n v\u00e0 ng\u1ee7 c\u00f3 \u0111\u1ee7 kh\u00f4ng?", "target": "pet_a", "reason_type": "evergreen"},
        {"text": "M\u1ed7i ng\u00e0y t\u00f4i n\u00ean ch\u00fa \u00fd \u0111i\u1ec1u g\u00ec?", "target": "pet_a", "reason_type": "evergreen"},
        {"text": "L\u00e0m sao bi\u1ebft th\u00fa c\u01b0ng c\u00f3 kh\u1ecfe kh\u00f4ng?", "target": "pet_a", "reason_type": "evergreen"},
    ],
    "MS": [
        {"text": "Apa yang perlu saya tahu tentang penjagaan haiwan?", "target": "pet_a", "reason_type": "evergreen"},
        {"text": "Adakah diet dan tidurnya mencukupi?", "target": "pet_a", "reason_type": "evergreen"},
        {"text": "Apakah tanda yang perlu diperhatikan setiap hari?", "target": "pet_a", "reason_type": "evergreen"},
        {"text": "Bagaimana tahu haiwan peliharaan sihat dan gembira?", "target": "pet_a", "reason_type": "evergreen"},
    ],
}


# ── Helper ──────────────────────────────────────────────────────────────────

def get_evergreen_questions(
    language: str,
    pet_count: int = 1,
) -> list[dict[str, str]]:
    """
    Return 4 evergreen fallback questions for the given language.

    Falls back to JA if language is not found (matches PRD fallback rule).
    For 2-pet households, adjusts targets to the correct mix:
      pet_a, pet_b, both, pet_a
    """
    templates = ANYMALLCHAN_EVERGREEN.get(language, ANYMALLCHAN_EVERGREEN["JA"])
    # Deep copy so callers can mutate without affecting the shared templates
    questions = [dict(q) for q in templates]

    if pet_count == 2:
        # Apply the 2-pet target mix: pet_a, pet_b, both, pet_a
        targets = ["pet_a", "pet_b", "both", "pet_a"]
        for i, q in enumerate(questions):
            q["target"] = targets[i]

    return questions
