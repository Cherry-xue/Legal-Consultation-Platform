from .config import settings

PROFILES = {
    'contract': {'name': '合同审查', 'hint': '解释商事合同条款，识别风险，说明修改方向和待确认事实。', 'icon': '▤'},
    'labor': {'name': '劳动争议', 'hint': '说明工资、加班、解除、补偿和劳动程序的条件、时效及材料。', 'icon': '▦'},
    'family': {'name': '婚姻家事', 'hint': '说明夫妻财产、抚养、继承规则及所需事实，不预设财产归属。', 'icon': '◇'},
    'consumer': {'name': '消费维权', 'hint': '说明退换货、商品质量、平台责任及救济路径和证据条件。', 'icon': '◈'},
    'procedure': {'name': '诉讼程序', 'hint': '说明管辖、起诉、举证、期限、执行及其事实前提。', 'icon': '§'},
}


def model_for(code):
    if code not in {*PROFILES, 'collaboration'}:
        raise ValueError('Unknown agent')
    return getattr(settings(), f'agent_{code}_model')


def profiles():
    return [{'code': code, **item, 'model': model_for(code)} for code, item in PROFILES.items()] + [
        {'code': 'collaboration', 'name': '协作咨询', 'hint': '两个业务智能体协同检索，汇总条件与依据。',
         'icon': '◎', 'model': model_for('collaboration'), 'extra': True}]
