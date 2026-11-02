import asyncio
import json
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from .agents import DISCLAIMER, run_agent
from .db import Run, session_factory
from .conversations import load_context
from .errors import ServiceError
from .profiles import PROFILES, model_for
from .provider import complete


class State(TypedDict, total=False):
    query: str
    owner: str
    run_id: str
    tasks: list
    results: list
    result: dict
    conversation_id: str | None


def validate_plan(raw):
    tasks = raw.get('tasks', [])
    if not isinstance(tasks, list) or len(tasks) != 2:
        raise ServiceError('PLAN_INVALID', '请提出涉及两个业务领域的问题，或切换单业务智能体。')
    if any(not isinstance(t, dict) or t.get('agent_code') not in PROFILES or
           not isinstance(t.get('question'), str) or not 1 <= len(t['question']) <= 2000 for t in tasks):
        raise ServiceError('PLAN_INVALID', '协作计划包含未授权 Agent 或无效任务，已阻止执行。')
    if len({t['agent_code'] for t in tasks}) != 2:
        raise ServiceError('PLAN_INVALID', '协作需要两个不同业务智能体。')
    return [{'agent_code': t['agent_code'], 'question': t['question']} for t in tasks]


async def plan(state):
    context = load_context(state['owner'], 'general', state.get('conversation_id'))
    if context['history'] and context['history'][-1] == {'role': 'user', 'content': state['query']}:
        context['history'].pop()
    raw, _ = await complete(model_for('collaboration'),
                            '你是法律咨询任务分配器。只输出 JSON：{"tasks":[{"agent_code":"...","question":"..."}]}。'
                            '问题确实跨领域时选两个不同业务 Agent，保留原问题全部事实，不补造事实；'
                            '单领域或无意义问题返回空 tasks。可用职责：' + json.dumps(PROFILES, ensure_ascii=False),
                            json.dumps({'question': state['query'], 'context': context}, ensure_ascii=False), max_tokens=700)
    return {'tasks': validate_plan(raw)}


async def dispatch(state):
    async def one(task):
        try:
            result = await asyncio.wait_for(run_agent(task['agent_code'],
                                                      '原问题：' + state['query'] + '\n子任务：' + task['question'],
                                                      state['owner'], state['run_id']), timeout=40)
            return {**task, **result}
        except (ServiceError, TimeoutError) as error:
            return {**task, 'status': 'failed', 'error_code': getattr(error, 'code', 'CHILD_TIMEOUT'),
                    'message': getattr(error, 'message', '子任务超时。'), 'claims': [], 'citations': []}
        except Exception:
            return {**task, 'status': 'failed', 'error_code': 'CHILD_FAILED',
                    'message': '这一领域的回答暂未完成，请稍后重试。', 'claims': [], 'citations': []}
    results = await asyncio.gather(*(one(t) for t in state['tasks']))
    return {'results': results}


async def aggregate(state):
    results = state['results']
    success = sum(r['status'] == 'completed' for r in results)
    usable = sum(r['status'] in {'completed', 'partial'} for r in results)
    # Deterministic merge avoids inventing a third unsourced legal conclusion.
    citations = {c['id']: c for r in results for c in r.get('citations', [])}
    return {'result': {'run_id': state['run_id'], 'agent_code': 'collaboration',
                       'model': model_for('collaboration'),
                       'status': 'completed' if success == 2 else 'partial' if usable else 'failed',
                       'tasks': results, 'claims': [], 'citations': list(citations.values()),
                       'warnings': ['以下按领域列出说明。若事实前提不同或结论存在差异，请结合原文向执业律师核实。'],
                       'disclaimer': DISCLAIMER}}


def build_graph():
    graph = StateGraph(State)
    graph.add_node('plan', plan)
    graph.add_node('dispatch', dispatch)
    graph.add_node('aggregate', aggregate)
    graph.add_edge(START, 'plan')
    graph.add_edge('plan', 'dispatch')
    graph.add_edge('dispatch', 'aggregate')
    graph.add_edge('aggregate', END)
    return graph.compile()


GRAPH = build_graph()


async def run_collaboration(query, owner, conversation_id=None):
    with session_factory()() as db:
        run = Run(agent_code='collaboration', model=model_for('collaboration'), query=query, owner=owner)
        db.add(run)
        db.commit()
        run_id = run.id
    try:
        state = await GRAPH.ainvoke({'query': query, 'owner': owner, 'run_id': run_id, 'conversation_id': conversation_id})
        result = state['result']
        with session_factory()() as db:
            run = db.get(Run, run_id)
            run.status, run.result = result['status'], result
            db.commit()
        return result
    except BaseException as error:
        with session_factory()() as db:
            run = db.get(Run, run_id)
            run.status = 'cancelled' if isinstance(error, asyncio.CancelledError) else 'failed'
            run.result = {'error_code': getattr(error, 'code', run.status.upper())}
            db.commit()
        raise
