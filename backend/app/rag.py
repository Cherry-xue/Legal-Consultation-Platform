import hashlib
import io
import math
import re
import zipfile
from functools import lru_cache

from sqlalchemy import select

from .config import settings
from .db import Chunk, Document, RetrievalLog
from .errors import ServiceError
from .provider import embed
from .search_rank import rank_candidates

ARTICLE = re.compile(r'^[ \t]*(?:#{1,6}[ \t]+)?(?P<article>第[一二三四五六七八九十百千零〇\d]+条(?:之[一二三四五六七八九十\d]+)?)(?=\s|[　、，。：:])', re.M)


def parse_file(name, data):
    suffix = name.rsplit('.', 1)[-1].lower() if '.' in name else ''
    try:
        if suffix in {'txt', 'md'}:
            try:
                text = data.decode('utf-8-sig')
            except UnicodeDecodeError:
                text = data.decode('gb18030')
        elif suffix == 'pdf':
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(data))
            if reader.is_encrypted or len(reader.pages) > 300:
                raise ServiceError('INVALID_FILE', 'PDF 加密或超过 300 页限制。')
            text = '\n\n'.join(f'【第 {i + 1} 页】\n' + (p.extract_text() or '')
                               for i, p in enumerate(reader.pages))
            if sum(len(p.extract_text() or '') for p in reader.pages) < 10:
                raise ServiceError('OCR_REQUIRED', '未提取到正文，扫描件需先进行 OCR。')
        elif suffix == 'docx':
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                if sum(f.file_size for f in archive.infolist()) > 50 * 1024 * 1024:
                    raise ServiceError('INVALID_FILE', 'DOCX 解压后体积过大。')
            from docx import Document as WordDocument
            doc = WordDocument(io.BytesIO(data))
            # Keep document order, including tables, via the XML body.
            from docx.oxml.ns import qn
            text = '\n'.join(''.join(t.text or '' for t in n.iter(qn('w:t')))
                             for n in doc.element.body)
        else:
            raise ServiceError('INVALID_FILE', '仅支持 TXT、Markdown、PDF 和 DOCX。')
    except ImportError:
        raise ServiceError('PARSER_NOT_INSTALLED', '资料解析暂时不可用，请稍后再试。', 503)
    except ServiceError:
        raise
    except Exception:
        raise ServiceError('PARSE_FAILED', '无法解析文件，请检查格式、编码或文件完整性。')
    text = text.replace('\r\n', '\n').replace('\r', '\n').replace('\x00', '')
    if not text.strip():
        raise ServiceError('EMPTY_TEXT', '文档中没有可用文本。')
    if len(text) > 2_000_000:
        raise ServiceError('INVALID_FILE', '文本超过 200 万字符限制。')
    return text


def split_text(text, max_chars=800, overlap=100):
    """Exact source offsets; no fabricated paragraph numbers; article subchunks stay linked."""
    if not 200 <= max_chars <= 2000 or not 0 <= overlap < max_chars // 2:
        raise ServiceError('INVALID_CHUNK_CONFIG', '块长须为 200–2000，重叠须小于块长的一半。')
    matches = list(ARTICLE.finditer(text))
    boundaries = [0] + [m.start() for m in matches if m.start() > 0] + [len(text)]
    result = []
    for left, right in zip(boundaries, boundaries[1:]):
        heading = ARTICLE.match(text, left)
        article = heading.group('article') if heading else ''
        start = left
        while start < right:
            end = min(start + max_chars, right)
            if end < right:
                # Prefer actual newline; numbered items are not relabelled as paragraphs.
                split = text.rfind('\n', start + max_chars // 2, end)
                if split > start:
                    end = split + 1
            if text[start:end].strip():
                result.append({'text': text[start:end], 'start': start, 'end': end,
                               'article': article, 'ordinal': len(result)})
            if end == right:
                break
            start = max(start + 1, end - overlap)
    if len(result) > 2000:
        raise ServiceError('TOO_MANY_CHUNKS', '单次最多 2000 块，请拆分文件。')
    return result


@lru_cache
def collection():
    import chromadb
    s = settings()
    client = chromadb.HttpClient(host=s.chroma_host, port=s.chroma_port)
    # Changing embedding model creates a separate vector space.
    name = 'legal_' + hashlib.sha256(s.embedding_model_name.encode()).hexdigest()[:16]
    return client.get_or_create_collection(name, metadata={'hnsw:space': 'cosine'})


async def retrieve(db, query, domain, top_k=5, run_id=None, *, owner):
    import asyncio
    docs = db.scalars(select(Document).where(
        Document.deleted.is_(False), Document.status == 'indexed',
        Document.domain.in_([domain, 'general']),
        (Document.visibility == 'public') | (Document.owner == owner))).all()
    allowed = {d.active_preview_id: d for d in docs if d.active_preview_id}
    if not allowed:
        raise ServiceError('KB_EMPTY', '当前业务范围尚无已入库资料，请先上传、切块并确认入库。')
    vector_unavailable = False
    try:
        vector = (await embed([query]))[0]
        result = await asyncio.to_thread(collection().query, query_embeddings=[vector],
                                         n_results=max(top_k, settings().rag_candidate_limit) if settings().rag_hybrid_enabled else top_k, where={'preview_id': {'$in': list(allowed)}})
        distances = {cid: float(distance) for cid, distance in zip(result['ids'][0], result['distances'][0])
                     if math.isfinite(float(distance)) and 0 <= float(distance) <= 2}
    except Exception:
        if not settings().rag_hybrid_enabled:
            raise ServiceError('VECTOR_UNAVAILABLE', '资料检索暂时不可用，请稍后再试。', 503)
        vector_unavailable, distances = True, {}
    if settings().rag_hybrid_enabled:
        candidates = list(db.scalars(select(Chunk).where(Chunk.preview_id.in_(list(allowed)), Chunk.document_id.in_([d.id for d in docs]))))
        candidates = [c for c in candidates if allowed[c.preview_id].id == c.document_id]
        ranked = rank_candidates(query, candidates, {d.id: d.name for d in docs}, distances,
                                 candidate_limit=settings().rag_candidate_limit,
                                 max_distance=settings().rag_max_vector_distance,
                                 min_coverage=settings().rag_min_lexical_coverage,
                                 min_score=settings().rag_min_rerank_score)
    else:
        ranked = [dict(id=cid, distance=distance) for cid, distance in distances.items()]
    rows = []
    for ranking in ranked:
        cid, distance = ranking['id'], ranking['distance']
        chunk = db.get(Chunk, cid)
        if not chunk or chunk.preview_id not in allowed or allowed[chunk.preview_id].id != chunk.document_id:
            continue
        doc = allowed[chunk.preview_id]
        # Revalidate authorization/availability against primary data after external query.
        db.refresh(doc)
        if doc.deleted or doc.status != 'indexed' or doc.active_preview_id != chunk.preview_id:
            continue
        rows.append({'id': chunk.id, 'document_id': doc.id, 'document_name': doc.name,
                     'text': chunk.text, 'article': chunk.article, 'distance': distance,
                     'score_type': 'hybrid_rerank_higher_is_better' if settings().rag_hybrid_enabled else 'cosine_distance_lower_is_better',
                     'ranking': ranking, 'retrieval_degraded': vector_unavailable, 'start': chunk.start,
                     'end': chunk.end, 'version': doc.version, 'legal_status': doc.legal_status,
                     'source_url': doc.source_url, 'verified_at': doc.verified_at})
        if len(rows) >= top_k:
            break
    if vector_unavailable and not rows:
        raise ServiceError('VECTOR_UNAVAILABLE', '向量检索暂时不可用，关键词检索也未找到可靠结果，请稍后重试。', 503)
    db.add(RetrievalLog(run_id=run_id or owner, query=query, domain=domain,
                        chunk_ids=[r['id'] for r in rows], embedding_model=settings().embedding_model_name))
    db.commit()
    return rows
