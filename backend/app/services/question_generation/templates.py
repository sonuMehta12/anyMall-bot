# app/services/question_generation/templates.py
#
# Evergreen question pools — fallback for the per-pet 10-question cold storage.
#
# Structure:
#   EVERGREEN[module][language][target] = list of question dicts
#
# Modules:  food | health | anymall
# Targets:  pet_a | pet_b | both
# Languages: EN, JA (others fall back to EN)
#
# Pool sizes (per module per language):
#   pet_a / pet_b → 8 questions each (sampled randomly at patch time)
#   both          → 4 questions     (sampled randomly at patch time)
#
# Slot layout served per pet (10 questions per pet row):
#   slots 0-2 → dedicated (pet_a or pet_b, 3 questions)  — food
#   slot  3   → both (1 question)                        — food
#   slots 4-6 → dedicated (3 questions)                  — health
#   slot  7   → both (1 question)                        — health
#   slot  8   → dedicated (1 question)                   — anymall
#   slot  9   → both (1 question)                        — anymall
#
# No reason_type field (removed in v2).
# Questions are FROM the owner's perspective, sent TO the AI.

from __future__ import annotations

import random


# ── Question pools ───────────────────────────────────────────────────────────

EVERGREEN: dict[str, dict[str, dict[str, list[dict[str, str]]]]] = {

    # ── Food ────────────────────────────────────────────────────────────────

    "food": {
        "EN": {
            "pet_a": [
                {"text": "What's the best diet for my pet?",          "module": "food", "target": "pet_a"},
                {"text": "How much should I feed my pet each day?",   "module": "food", "target": "pet_a"},
                {"text": "Are there foods I should avoid for my pet?","module": "food", "target": "pet_a"},
                {"text": "Is my pet's current food balanced?",        "module": "food", "target": "pet_a"},
                {"text": "What healthy snacks are safe for my pet?",  "module": "food", "target": "pet_a"},
                {"text": "Should I adjust my pet's diet as they age?","module": "food", "target": "pet_a"},
                {"text": "What portion size is right for my pet?",    "module": "food", "target": "pet_a"},
                {"text": "How do I know if my pet is a healthy weight?","module": "food", "target": "pet_a"},
            ],
            "pet_b": [
                {"text": "What's the best diet for my other pet?",         "module": "food", "target": "pet_b"},
                {"text": "How much should I feed my second pet each day?", "module": "food", "target": "pet_b"},
                {"text": "Are there any foods bad for my other pet?",      "module": "food", "target": "pet_b"},
                {"text": "Is my second pet's food nutritionally complete?","module": "food", "target": "pet_b"},
                {"text": "What treats are safe for my other pet?",         "module": "food", "target": "pet_b"},
                {"text": "Does my second pet need a special diet?",        "module": "food", "target": "pet_b"},
                {"text": "What's the right portion size for my other pet?","module": "food", "target": "pet_b"},
                {"text": "How do I keep my second pet at a healthy weight?","module": "food", "target": "pet_b"},
            ],
            "both": [
                {"text": "Can my two pets share the same food?",          "module": "food", "target": "both"},
                {"text": "Should my pets eat at the same time?",          "module": "food", "target": "both"},
                {"text": "How do I manage different diets for two pets?", "module": "food", "target": "both"},
                {"text": "What feeding schedule works for both my pets?", "module": "food", "target": "both"},
            ],
        },
        "JA": {
            "pet_a": [
                {"text": "うちのペットに最適な食事は何ですか？",             "module": "food", "target": "pet_a"},
                {"text": "1日にどのくらい食べさせればいいですか？",          "module": "food", "target": "pet_a"},
                {"text": "与えてはいけない食べ物はありますか？",             "module": "food", "target": "pet_a"},
                {"text": "今のフードは栄養バランスがとれていますか？",        "module": "food", "target": "pet_a"},
                {"text": "安全なおやつはどんなものがありますか？",            "module": "food", "target": "pet_a"},
                {"text": "年齢に合わせて食事を変えるべきですか？",            "module": "food", "target": "pet_a"},
                {"text": "適切な食事の量はどのくらいですか？",               "module": "food", "target": "pet_a"},
                {"text": "体重が適切かどうか確認する方法は？",               "module": "food", "target": "pet_a"},
            ],
            "pet_b": [
                {"text": "もう1匹のペットに最適な食事は何ですか？",           "module": "food", "target": "pet_b"},
                {"text": "2匹目はどのくらい食べさせればいいですか？",         "module": "food", "target": "pet_b"},
                {"text": "2匹目に与えてはいけない食べ物はありますか？",       "module": "food", "target": "pet_b"},
                {"text": "2匹目のフードは栄養バランスが取れていますか？",     "module": "food", "target": "pet_b"},
                {"text": "2匹目に安全なおやつはありますか？",                "module": "food", "target": "pet_b"},
                {"text": "2匹目には特別な食事が必要ですか？",                "module": "food", "target": "pet_b"},
                {"text": "2匹目の適切な食事量はどのくらいですか？",           "module": "food", "target": "pet_b"},
                {"text": "2匹目の体重管理のポイントは何ですか？",             "module": "food", "target": "pet_b"},
            ],
            "both": [
                {"text": "2匹に同じフードを与えても大丈夫ですか？",            "module": "food", "target": "both"},
                {"text": "2匹を同時に食事させるべきですか？",                 "module": "food", "target": "both"},
                {"text": "食事の内容が違う場合、どう管理すればいいですか？",   "module": "food", "target": "both"},
                {"text": "2匹のための食事スケジュールはどうすればいいですか？","module": "food", "target": "both"},
            ],
        },
    },

    # ── Health ──────────────────────────────────────────────────────────────

    "health": {
        "EN": {
            "pet_a": [
                {"text": "What daily health signs should I watch for?",       "module": "health", "target": "pet_a"},
                {"text": "How often should my pet see the vet?",              "module": "health", "target": "pet_a"},
                {"text": "What vaccines does my pet need?",                   "module": "health", "target": "pet_a"},
                {"text": "How do I keep my pet's coat and skin healthy?",     "module": "health", "target": "pet_a"},
                {"text": "What exercise routine suits my pet?",               "module": "health", "target": "pet_a"},
                {"text": "How do I care for my pet's teeth?",                 "module": "health", "target": "pet_a"},
                {"text": "What parasite prevention does my pet need?",        "module": "health", "target": "pet_a"},
                {"text": "How do I know if my pet is stressed?",              "module": "health", "target": "pet_a"},
            ],
            "pet_b": [
                {"text": "What health signs should I watch in my other pet?", "module": "health", "target": "pet_b"},
                {"text": "How often should my second pet see the vet?",       "module": "health", "target": "pet_b"},
                {"text": "What does my second pet need to stay healthy?",     "module": "health", "target": "pet_b"},
                {"text": "How do I keep my other pet's coat healthy?",        "module": "health", "target": "pet_b"},
                {"text": "What exercise does my second pet need?",            "module": "health", "target": "pet_b"},
                {"text": "How do I care for my second pet's teeth?",          "module": "health", "target": "pet_b"},
                {"text": "What parasite care does my other pet need?",        "module": "health", "target": "pet_b"},
                {"text": "How can I tell if my second pet is unhappy?",       "module": "health", "target": "pet_b"},
            ],
            "both": [
                {"text": "Can my two pets share health routines?",              "module": "health", "target": "both"},
                {"text": "How do I schedule vet visits for two pets?",          "module": "health", "target": "both"},
                {"text": "Do both my pets need the same vaccinations?",         "module": "health", "target": "both"},
                {"text": "What wellness habits work for both my pets?",         "module": "health", "target": "both"},
            ],
        },
        "JA": {
            "pet_a": [
                {"text": "毎日どんな健康サインをチェックすればいいですか？",         "module": "health", "target": "pet_a"},
                {"text": "獣医には何カ月おきに連れて行けばいいですか？",             "module": "health", "target": "pet_a"},
                {"text": "必要なワクチンは何ですか？",                             "module": "health", "target": "pet_a"},
                {"text": "被毛と皮膚を健康に保つコツはありますか？",                 "module": "health", "target": "pet_a"},
                {"text": "うちのペットに合った運動はどんなものですか？",             "module": "health", "target": "pet_a"},
                {"text": "歯のケアはどうすればいいですか？",                       "module": "health", "target": "pet_a"},
                {"text": "寄生虫の予防方法を教えてください",                        "module": "health", "target": "pet_a"},
                {"text": "ストレスを感じているかどうかわかりますか？",               "module": "health", "target": "pet_a"},
            ],
            "pet_b": [
                {"text": "2匹目の日々の健康チェックポイントは何ですか？",            "module": "health", "target": "pet_b"},
                {"text": "2匹目は獣医に何カ月おきに行けばいいですか？",             "module": "health", "target": "pet_b"},
                {"text": "2匹目を健康に保つために必要なことは何ですか？",            "module": "health", "target": "pet_b"},
                {"text": "2匹目の被毛ケアはどうすればいいですか？",                 "module": "health", "target": "pet_b"},
                {"text": "2匹目に合った運動量はどのくらいですか？",                 "module": "health", "target": "pet_b"},
                {"text": "2匹目の歯磨きはどうすればいいですか？",                   "module": "health", "target": "pet_b"},
                {"text": "2匹目の寄生虫対策はどうすればいいですか？",               "module": "health", "target": "pet_b"},
                {"text": "2匹目が不調を感じているかどうかわかりますか？",            "module": "health", "target": "pet_b"},
            ],
            "both": [
                {"text": "2匹のヘルスケアをまとめて管理する方法はありますか？",      "module": "health", "target": "both"},
                {"text": "2匹の通院スケジュールはどう組めばいいですか？",            "module": "health", "target": "both"},
                {"text": "2匹に同じワクチンが必要ですか？",                         "module": "health", "target": "both"},
                {"text": "2匹に共通する健康習慣はどんなものがありますか？",          "module": "health", "target": "both"},
            ],
        },
    },

    # ── AnyMall ─────────────────────────────────────────────────────────────

    "anymall": {
        "EN": {
            "pet_a": [
                {"text": "What should I know about caring for my pet?",       "module": "anymall", "target": "pet_a"},
                {"text": "Is my pet eating and sleeping well enough?",        "module": "anymall", "target": "pet_a"},
                {"text": "What everyday signs should I watch for?",           "module": "anymall", "target": "pet_a"},
                {"text": "How do I know if my pet is happy and healthy?",     "module": "anymall", "target": "pet_a"},
                {"text": "What's the most important thing for my pet today?", "module": "anymall", "target": "pet_a"},
                {"text": "How can I improve my pet's daily routine?",         "module": "anymall", "target": "pet_a"},
                {"text": "What grooming care does my pet need?",              "module": "anymall", "target": "pet_a"},
                {"text": "How do I keep my pet mentally stimulated?",         "module": "anymall", "target": "pet_a"},
            ],
            "pet_b": [
                {"text": "What should I know about caring for my other pet?",  "module": "anymall", "target": "pet_b"},
                {"text": "Is my second pet comfortable and content?",          "module": "anymall", "target": "pet_b"},
                {"text": "What daily care does my other pet need?",            "module": "anymall", "target": "pet_b"},
                {"text": "How do I improve my second pet's daily life?",       "module": "anymall", "target": "pet_b"},
                {"text": "What grooming routine works for my other pet?",      "module": "anymall", "target": "pet_b"},
                {"text": "How do I keep my second pet mentally active?",       "module": "anymall", "target": "pet_b"},
                {"text": "What behavior signs should I watch in my other pet?","module": "anymall", "target": "pet_b"},
                {"text": "How can I bond more with my second pet?",            "module": "anymall", "target": "pet_b"},
            ],
            "both": [
                {"text": "How do I keep both my pets happy together?",        "module": "anymall", "target": "both"},
                {"text": "What daily care routine works for both my pets?",   "module": "anymall", "target": "both"},
                {"text": "How do I give equal attention to both my pets?",    "module": "anymall", "target": "both"},
                {"text": "What helps both my pets feel safe at home?",        "module": "anymall", "target": "both"},
            ],
        },
        "JA": {
            "pet_a": [
                {"text": "ペットのケアで大切なことは何ですか？",              "module": "anymall", "target": "pet_a"},
                {"text": "食事や睡眠は十分ですか？",                         "module": "anymall", "target": "pet_a"},
                {"text": "毎日何をチェックすればいいですか？",               "module": "anymall", "target": "pet_a"},
                {"text": "うちのペットは元気かどうか、どう判断すればいい？", "module": "anymall", "target": "pet_a"},
                {"text": "今日ペットのために一番大切なことは何ですか？",      "module": "anymall", "target": "pet_a"},
                {"text": "ペットの日常ルーティンを改善するには？",            "module": "anymall", "target": "pet_a"},
                {"text": "グルーミングはどのくらいの頻度で行えばいいですか？","module": "anymall", "target": "pet_a"},
                {"text": "ペットの心を刺激する方法はありますか？",            "module": "anymall", "target": "pet_a"},
            ],
            "pet_b": [
                {"text": "もう1匹のペットのケアで大切なことは何ですか？",     "module": "anymall", "target": "pet_b"},
                {"text": "2匹目は快適に過ごせていますか？",                  "module": "anymall", "target": "pet_b"},
                {"text": "2匹目の日々のケアは何が必要ですか？",               "module": "anymall", "target": "pet_b"},
                {"text": "2匹目の日常を改善する方法はありますか？",           "module": "anymall", "target": "pet_b"},
                {"text": "2匹目のグルーミングはどうすればいいですか？",       "module": "anymall", "target": "pet_b"},
                {"text": "2匹目の頭を使わせる方法はありますか？",             "module": "anymall", "target": "pet_b"},
                {"text": "2匹目のどんな行動に注目すべきですか？",             "module": "anymall", "target": "pet_b"},
                {"text": "2匹目ともっと絆を深めるには？",                     "module": "anymall", "target": "pet_b"},
            ],
            "both": [
                {"text": "2匹が仲良く過ごすためにできることは？",             "module": "anymall", "target": "both"},
                {"text": "2匹に共通するケアルーティンはどうすればいいですか？","module": "anymall", "target": "both"},
                {"text": "2匹に平等に愛情を注ぐにはどうすればいいですか？",   "module": "anymall", "target": "both"},
                {"text": "2匹が家で安心できる環境を作るには？",               "module": "anymall", "target": "both"},
            ],
        },
    },
}


# ── Helper ───────────────────────────────────────────────────────────────────

def get_evergreen_questions(
    module: str,
    language: str,
    target: str,
    count: int = 1,
) -> list[dict[str, str]]:
    """
    Return `count` randomised evergreen questions for the given module/language/target.

    Falls back:
      - unknown module   → "anymall"
      - unknown language → "EN"  (v2 changed from JA to EN)
      - unknown target   → "pet_a"

    Returns deep-copied dicts so callers can mutate without affecting shared pools.
    """
    module_pool = EVERGREEN.get(module, EVERGREEN["anymall"])
    lang_pool = module_pool.get(language, module_pool.get("EN", {}))
    target_pool = lang_pool.get(target, lang_pool.get("pet_a", []))

    if not target_pool:
        return []

    sample = random.sample(target_pool, min(count, len(target_pool)))
    return [dict(q) for q in sample]
