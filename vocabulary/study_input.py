"""Transient review input; no persistence or widget ownership."""
from dataclasses import dataclass


STUDY_TRANSLATION_HEADER = (
    "翻译（输入词性查看释义  1|n：名词，2|v：动词，3|a：形容词，"
    "4：副词，5|p：介词，6：代词，7|c：连词，8|i：感叹词，9|e：其他）"
)
POS_KEYS = {
    "1": "n.", "n": "n.", "2": "v.", "v": "v.",
    "3": "adj.", "a": "adj.", "4": "adv.", "5": "prep.", "p": "prep.",
    "6": "pron.", "7": "conj.", "c": "conj.", "8": "interj.", "i": "interj.",
    "9": "else", "e": "else",
}


def matches_pos(key, senses):
    code = POS_KEYS.get(key.lower())
    return code is not None and any(sense.pos == code for sense in senses)


@dataclass
class StudyInput:
    entry_id: int | None = None
    spelling: str = ""
    position: int = 0

    def reset(self):
        self.entry_id = None
        self.spelling = ""
        self.position = 0

    def feed(self, entry_id, spelling, character):
        if (entry_id, spelling) != (self.entry_id, self.spelling):
            self.reset()
            self.entry_id, self.spelling = entry_id, spelling
        if self.position == 0 and character == " ":
            self.reset()
            return "hide"
        if not spelling or character != spelling[self.position]:
            self.reset()
            return None
        self.position += 1
        if self.position == len(spelling):
            self.reset()
            return "complete"
        return None
