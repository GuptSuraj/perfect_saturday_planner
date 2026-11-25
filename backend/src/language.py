import unicodedata

from schemas import Language


def localized(language: Language, english: str, hindi: str) -> str:
    return hindi if language == "hi" else english


def supported_script(text: str) -> bool:
    return all(
        not character.isalpha() or unicodedata.name(character, "").startswith(("LATIN ", "DEVANAGARI "))
        for character in text
    )


def language_instruction(language: Language) -> str:
    reply = localized(language, "natural English", "natural conversational Hindi using Devanagari for written Hindi")
    return (
        f"This conversation is set to {reply}. Reply in that language. Accept English, Hindi, and Hindi mixed "
        "with English, including romanized Hindi. Use only these languages. If the user speaks another language "
        "or the input is unclear, briefly ask them to repeat in Hindi or English. Treat silence, background noise, "
        "and filler sounds as no new preferences. Never guess an unclear city, budget, or duration: ask only "
        "about the unclear detail. Pay close attention to amounts such as एक हज़ार (1000) and दो हज़ार (2000). "
        "Keep real venue names, addresses, and provider query terms in their usual local or English spelling."
    )


def repeat_request(language: Language) -> str:
    return localized(language, "I can help in English or Hindi. Could you say that again in either language?",
                     "मैं हिंदी या अंग्रेज़ी में मदद कर सकती हूँ। क्या आप फिर से बता सकते हैं?")
