"""The plain-text clipboard format for one word and its definitions."""
import json

from .domain import RelatedWord, Sense, ValidationError, clean_word_content


FORMAT = "kkMemorizeVocabulary.word"
VERSION = 1


def encode(word: RelatedWord) -> str:
    word = clean_word_content(word)
    return json.dumps({
        "format": FORMAT,
        "version": VERSION,
        "text": word.text,
        "phonetic": word.phonetic,
        "senses": [{"pos": sense.pos, "translation": sense.translation} for sense in word.senses],
    }, ensure_ascii=False, separators=(",", ":"))


def decode(content: str) -> RelatedWord | None:
    """Return None for ordinary text and reject invalid recognized payloads."""
    try:
        document = json.loads(content)
    except (ValueError, RecursionError):
        return None
    if not isinstance(document, dict) or document.get("format") != FORMAT:
        return None
    error = "整词剪贴板内容无效，无法粘贴"
    if (set(document) != {"format", "version", "text", "phonetic", "senses"}
            or type(document["version"]) is not int or document["version"] != VERSION
            or not isinstance(document["text"], str) or not isinstance(document["phonetic"], str)
            or not isinstance(document["senses"], list)):
        raise ValidationError(error)
    senses = []
    for item in document["senses"]:
        if (not isinstance(item, dict) or set(item) != {"pos", "translation"}
                or not isinstance(item["pos"], str) or not item["pos"].strip()
                or not isinstance(item["translation"], str)):
            raise ValidationError(error)
        senses.append(Sense(item["pos"], item["translation"]))
    try:
        return clean_word_content(RelatedWord(document["text"], senses, document["phonetic"]))
    except ValidationError as cause:
        raise ValidationError(error) from cause
