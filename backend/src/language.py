import re
import unicodedata

from schemas import Language

LANGUAGE_INSTRUCTIONS = """
Support only English and Hindi, including Hinglish. Follow the language of the user's
latest meaningful message or spoken turn, and switch naturally when they switch.
Short acknowledgements such as 'yes', 'haan', or 'okay' keep the existing language.
Speak natural Hindi when they speak Hindi, and English when they speak English.
For written Hindi use everyday Hinglish in Latin letters, such as 'Aap kis shehar mein
honge?' Keep venue names, addresses, and search queries in their usual spelling.
If the user speaks another language or the input is unclear, briefly ask them to repeat
in Hindi or English. Treat silence, background noise, and filler as no new preferences.
Never guess an unclear city, budget, or duration. Ask only about the unclear detail.
Distinguish ek hazaar (1000) from do hazaar (2000).
"""

CONSONANTS = dict(zip(
    "कखगघङचछजझञटठडढणतथदधनपफबभमयरलवशषसहक़ख़ग़ज़ड़ढ़फ़य़",
    "k kh g gh ng ch chh j jh ny t th d dh n t th d dh n p ph b bh m y r l v sh sh s h q kh gh z r rh f y".split(),
    strict=True,
))
VOWEL_SIGNS = dict(zip("ािीुूृेैोौ्", ("aa", "i", "i", "u", "oo", "ri", "e", "ai", "o", "au", ""), strict=True))
VOWELS = dict(zip("अआइईउऊऋएऐओऔ", ("a", "aa", "i", "i", "u", "oo", "ri", "e", "ai", "o", "au"), strict=True))
VOWELS.update({"ँ": "n", "ं": "n", "ः": "h"})
NUKTA = {"क": "क़", "ख": "ख़", "ग": "ग़", "ज": "ज़", "ड": "ड़", "ढ": "ढ़", "फ": "फ़"}
DEVANAGARI_WORD = re.compile(r"[\u0900-\u0963\u0966-\u097f]+")


def localized(language: Language, english: str, hindi: str) -> str:
    return hindi if language == "hi" else english


def supported_script(text: str) -> bool:
    return all(
        not character.isalpha() or unicodedata.name(character, "").startswith(("LATIN ", "DEVANAGARI "))
        for character in text
    )


def romanize_caption(text: str) -> str:
    """Render spoken Hindi captions in Latin letters while preserving English and amounts."""
    def word(match: re.Match) -> str:
        letters = match[0]
        parts = []
        index = 0
        while index < len(letters):
            character = letters[index]
            if character in CONSONANTS:
                if letters[index + 1:index + 2] == "़":
                    character = NUKTA.get(character, character)
                    index += 1
                following = letters[index + 1:index + 2]
                parts.append(CONSONANTS[character])
                if following in VOWEL_SIGNS:
                    parts.append(VOWEL_SIGNS[following])
                    index += 1
                elif following or index == 0:
                    parts.append("a")
            elif character.isdecimal():
                parts.append(str(unicodedata.decimal(character)))
            else:
                parts.append(VOWELS.get(character, character))
            index += 1
        return "".join(parts)

    return DEVANAGARI_WORD.sub(word, text).replace("।", ".").replace("॥", ".")
