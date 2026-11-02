import json

import httpx

from .config import settings
from .errors import ServiceError


async def post(path, payload):
    s = settings()
    key = s.api_key()
    if not key:
        raise ServiceError('MODEL_NOT_CONFIGURED', '咨询服务暂时不可用，请稍后再试。', 503)
    try:
        async with httpx.AsyncClient(timeout=s.model_timeout_seconds, trust_env=False) as client:
            response = await client.post(s.dashscope_base_url.rstrip('/') + path,
                                         headers={'Authorization': f'Bearer {key}'}, json=payload)
        if response.is_error:
            try:
                error = response.json().get('error', {})
                code = str(error.get('code', 'PROVIDER_ERROR'))[:80]
            except (ValueError, AttributeError):
                code = 'PROVIDER_ERROR'
            # Never return provider raw messages, request bodies or credentials.
            message = ('咨询请求较多，请稍后重试。' if response.status_code == 429 else
                       '咨询服务暂时不可用，请稍后再试。')
            raise ServiceError(code, message, 502)
        return response.json()
    except httpx.TimeoutException:
        raise ServiceError('LLM_TIMEOUT', '模型调用超时，请重试。', 504)
    except httpx.RequestError:
        raise ServiceError('PROVIDER_UNREACHABLE', '无法连接模型服务。', 502)


async def embed(texts):
    s = settings()
    result = []
    for i in range(0, len(texts), s.embedding_batch_size):
        batch = texts[i:i + s.embedding_batch_size]
        data = await post('/embeddings', {'model': s.embedding_model_name, 'input': batch,
                                           'encoding_format': 'float'})
        rows = sorted(data.get('data', []), key=lambda x: x['index'])
        if len(rows) != len(batch) or [x['index'] for x in rows] != list(range(len(batch))):
            raise ServiceError('EMBEDDING_INVALID', '资料处理暂未完成，请稍后重试。', 502)
        vectors = [row['embedding'] for row in rows]
        if not vectors or not all(v and len(v) == len(vectors[0]) for v in vectors):
            raise ServiceError('EMBEDDING_INVALID', '资料处理暂未完成，请稍后重试。', 502)
        result.extend(vectors)
    return result


async def complete(model, system, user, max_tokens=1800):
    payload = {'model': model, 'messages': [{'role': 'system', 'content': system},
                                           {'role': 'user', 'content': user}],
               'max_tokens': max_tokens, 'response_format': {'type': 'json_object'}}
    # Historical plus snapshots don't need vendor thinking parameters.
    if model.startswith(('qwen3.', 'qwen3-')):
        payload['enable_thinking'] = settings().enable_thinking
    data = await post('/chat/completions', payload)
    try:
        value = json.loads(data['choices'][0]['message']['content'])
        if not isinstance(value, dict):
            raise ValueError()
        return value, data.get('usage', {})
    except (KeyError, IndexError, ValueError, TypeError):
        raise ServiceError('MODEL_OUTPUT_INVALID', '本次回答暂未完成，请稍后重试。', 502)
