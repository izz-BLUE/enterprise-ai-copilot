from app.retrieval.chunk_store import get_chunks
from app.retrieval.text_tokenizer import unique_character_ngrams


def _score_chunk(content: str, keywords: list[str]) -> int:
    score = 0.0
    for keyword in keywords:
        count = content.count(keyword)
        if count > 0:
            score += count * (1 + len(keyword) * 0.1)
    return int(score)


def retrieve(query: str, top_k: int = 3) -> list[dict]:
    chunks = get_chunks()
    if not chunks:
        return []

    keywords = unique_character_ngrams(query)
    scored: list[tuple[int, int, dict]] = []
    for index, chunk in enumerate(chunks):
        score = _score_chunk(chunk['content'], keywords)
        if score > 0:
            scored.append((score, index, chunk))

    scored.sort(key=lambda item: (-item[0], item[1]))
    return [chunk for _score, _index, chunk in scored[:top_k]]
