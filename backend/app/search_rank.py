"""Chinese n-gram BM25, rank fusion and deterministic feature reranking."""
from collections import Counter
from functools import lru_cache
import math
import re

STOP = {'如何', '什么', '是否', '可以', '需要', '哪些', '根据', '规定', '说明', '法律', '资料', '请问', '有关', '一个', '我们', '应当', '问题', '情况下'}
ARTICLE_REF = re.compile(r'第([一二三四五六七八九十百千零〇\d]+)条(?:之([一二三四五六七八九十\d]+))?')
LAW_NAMES = ('民法典', '劳动合同法', '消费者权益保护法', '民事诉讼法')


def number(value):
    if value.isdigit():
        return int(value)
    digits = dict(zip('零〇一二三四五六七八九', [0, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9]))
    units = {'十': 10, '百': 100, '千': 1000}
    total = current = 0
    for char in value:
        if char in digits:
            current = digits[char]
        else:
            total += (current or 1) * units[char]
            current = 0
    return total + current


def article_refs(value):
    return {(number(a), number(b) if b else None) for a, b in ARTICLE_REF.findall(value)}


@lru_cache(maxsize=10000)
def terms(value):
    tokens = []
    for part in re.findall(r'[a-zA-Z0-9][a-zA-Z0-9_-]*|[\u4e00-\u9fff]+', value.lower()):
        if re.fullmatch(r'[\u4e00-\u9fff]+', part):
            for size in (2, 3):
                tokens.extend(part[i:i+size] for i in range(len(part)-size+1) if part[i:i+size] not in STOP)
        else:
            tokens.append(part)
    return Counter(tokens)


def lexical_scores(query, corpus):
    """corpus is already ACL-filtered; IDF never uses another user's documents."""
    q = set(terms(query))
    counts = {key: terms(text) for key, text in corpus.items()}
    lengths = {key: sum(value.values()) for key, value in counts.items()}
    average = sum(lengths.values()) / max(len(counts), 1) or 1
    df = Counter(term for value in counts.values() for term in q if term in value)
    result = {}
    for key, value in counts.items():
        score = 0.0
        for term in q.intersection(value):
            idf = math.log(1 + (len(counts)-df[term]+.5)/(df[term]+.5))
            frequency = value[term]
            score += idf * frequency * 2.2 / (frequency + 1.2 * (.25 + .75*lengths[key]/average))
        result[key] = (score, len(q.intersection(value)) / max(len(q), 1), len(q.intersection(value)))
    return result


def rank_candidates(query, chunks, names, vector_distances, *, candidate_limit=30,
                    max_distance=.5, min_coverage=.28, min_score=.20):
    corpus = {c.id: names[c.document_id] + '\n' + c.article + '\n' + c.text for c in chunks}
    lexical = lexical_scores(query, corpus)
    lex_order = sorted((key for key in corpus if lexical[key][0] > 0), key=lambda k: (-lexical[k][0], k))[:candidate_limit]
    vec_order = sorted((key for key in vector_distances if key in corpus and math.isfinite(vector_distances[key])),
                       key=lambda k: (vector_distances[k], k))[:candidate_limit]
    fusion = Counter()
    for order in (lex_order, vec_order):
        for position, key in enumerate(order, 1):
            fusion[key] += 1 / (60 + position)
    refs = article_refs(query)
    requested_laws = [name for name in LAW_NAMES if name in query]
    explicit_ids = set()
    for chunk in chunks:
        if refs.intersection(article_refs(chunk.article)) and (not requested_laws or any(name in names[chunk.document_id] for name in requested_laws)):
            explicit_ids.add(chunk.id)
            fusion[chunk.id] += 1 / 61
    lookup = {c.id: c for c in chunks}
    ranked = []
    for key, rrf in fusion.items():
        chunk = lookup[key]
        exact_article = bool(refs.intersection(article_refs(chunk.article)))
        law_match = bool(requested_laws) and any(name in names[chunk.document_id] for name in requested_laws)
        # An explicit citation query cannot be satisfied with a different numbered
        # article (including references inside its body) or a different named law.
        if refs and not exact_article:
            continue
        if requested_laws and not law_match:
            continue
        bm25, coverage, overlap = lexical[key]
        distance = vector_distances.get(key)
        semantic = max(0.0, min(1.0, 1-distance)) if distance is not None and math.isfinite(distance) else 0.0
        strong_lexical = bm25 > 0 and coverage >= min_coverage and overlap >= min(2, len(terms(query)))
        vector_ok = distance is not None and math.isfinite(distance) and 0 <= distance <= max_distance
        if not (vector_ok or strong_lexical or (exact_article and (law_match or not requested_laws))):
            continue
        # Semantic evidence remains dominant: lexical matches supplement it,
        # rather than allowing repeated generic terms to overwhelm paraphrases.
        score = .8*semantic + .08*(bm25/(bm25+8)) + .08*coverage + .04*min(1, rrf*30.5)
        if strong_lexical:
            # BM25's absolute magnitude varies with corpus size. A one-file
            # knowledge base must still support genuine keyword-only matches.
            score = max(score, .25*coverage + .2*(bm25/(bm25+1)) + .05*min(1, rrf*30.5))
        score += .35*exact_article + .1*law_match
        if score < min_score:
            continue
        ranked.append(dict(id=key, score=score, distance=distance, bm25=bm25, lexical_coverage=coverage,
                           rrf=rrf, exact_article=exact_article, law_match=law_match,
                           channels=[channel for channel, order in [('keyword', lex_order), ('vector', vec_order), ('article', explicit_ids)] if key in order]))
    return sorted(ranked, key=lambda row: (-row['score'], row['id']))
