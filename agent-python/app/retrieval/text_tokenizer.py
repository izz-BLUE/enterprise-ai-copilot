import re

STOP_WORDS = frozenset({
    '的', '了', '在', '是', '我', '有', '和', '就', '不', '人', '都', '一',
    '一个', '可以', '我们', '你们', '他们', '它们', '这个', '那个', '什么',
    '怎么', '如何', '哪', '哪儿', '哪里', '谁', '为什么', '哪些', '多少',
    '请', '问', '请问', '帮', '帮忙', '想', '要', '需要', '应该', '能',
    '能够', '会', '可能', '好', '吗', '吧', '呢', '啊', '哦', '嗯',
    '对', '对于', '关于', '把', '被', '让', '给', '跟', '与', '以',
    '从', '到', '去', '来', '上', '下', '大', '小', '多', '少', '很',
    '太', '非常', '比较', '也', '还', '又', '再', '才', '刚', '已经',
    '正在', '着', '过',
})

_SPLIT_PATTERN = re.compile(r'[，。！？、；："“”\'（）【】《》\s,\.!?;:()\[\]{}<>/\\|]+')


def character_ngrams(text: str) -> list[str]:
    """中文友好的 2/3-gram，供 Keyword 与 BM25 共用。"""
    grams: list[str] = []
    for raw in _SPLIT_PATTERN.split(text):
        token = raw.strip()
        if len(token) < 2 or token in STOP_WORDS:
            continue
        if len(token) >= 4:
            grams.append(token)
        for size in (2, 3):
            for index in range(len(token) - size + 1):
                gram = token[index:index + size]
                if gram not in STOP_WORDS:
                    grams.append(gram)
    return grams


def unique_character_ngrams(text: str) -> list[str]:
    return list(dict.fromkeys(character_ngrams(text)))
