"""Explainable upload routing hints; these do not verify legal authority."""
import re

LABELS = {'general': '通用法规', 'contract': '合同审查', 'labor': '劳动争议',
          'family': '婚姻家事', 'consumer': '消费维权', 'procedure': '诉讼程序'}
KEYWORDS = {
    'contract': ['合同审查', '采购合同', '买卖合同', '租赁合同', '订单条款', '合同编', '违约金', '履行义务', '验收', '付款节点', '质量保证金'],
    'labor': ['劳动争议', '劳动合同', '劳动法', '工资', '加班', '考勤', '调休', '经济补偿', '离职', '用人单位', '绩效'],
    'family': ['婚姻家事', '婚姻家庭', '家庭财产', '子女照护', '抚养', '离婚', '继承', '遗嘱', '夫妻', '赠与'],
    'consumer': ['消费维权', '消费者权益', '消费投诉', '投诉记录', '网购', '退换货', '退款', '商家客服', '虚假宣传', '七天无理由'],
    'procedure': ['诉讼程序', '民事诉讼法', '刑事诉讼法', '行政诉讼法', '证据规定', '起诉状', '立案', '举证', '管辖', '财产保全', '交接清单', '证据目录'],
}


def recommend_domain(name, text):
    # Restrict title detection to actual title lines; a citation inside the body
    # must not turn a specific contract into a general statute.
    heading_lines = [line.strip() for line in text.splitlines()[:12] if line.strip()][:3]
    title = name + '\n' + '\n'.join(heading_lines)
    broad_title = any(word in title for word in ['民法典', '中华人民共和国宪法', '通用法规', '综合法律汇编'])
    specific_excerpt = any(word in title for word in ['合同编', '婚姻家庭编', '继承编', '节选', '摘录'])
    scores, matched = {}, {}
    for domain, words in KEYWORDS.items():
        found = [word for word in words if word in title or word in text]
        matched[domain] = found
        scores[domain] = sum(6 * title.count(word) + min(text.count(word), 3) for word in found)
    # Explicit subject labels in structured material are stronger than incidental mentions.
    label = re.search(r'业务范围\s*[:：]\s*([^\n]+)', text[:2000])
    if label:
        for domain, display in LABELS.items():
            if display in label.group(1):
                if domain == 'general':
                    broad_title = True
                else:
                    scores[domain] += 40
    ranked = sorted(scores, key=lambda domain: scores[domain], reverse=True)
    top = ranked[0]
    if broad_title and not specific_excerpt:
        domain, confidence = 'general', 'high'
        reason = '标题显示为综合性法规，建议放入通用范围，供各业务智能体共同检索。'
    elif scores[top] == 0:
        domain, confidence = 'general', 'low'
        reason = '未识别到明确的专业领域，建议暂存通用范围，请结合文件内容确认。'
    elif scores[ranked[1]] >= 12 and scores[ranked[1]] >= scores[top] * .8:
        domain, confidence = 'general', 'medium'
        reason = f'内容同时涉及{LABELS[top]}与{LABELS[ranked[1]]}，建议使用通用范围，或按章节拆分后分别上传。'
    else:
        domain = top
        confidence = 'high' if scores[top] >= 15 else 'medium'
        reason = '文件名或正文包含“' + '、'.join(matched[top][:4]) + '”，与该业务范围较相关。'
    candidates = [dict(domain=d, label=LABELS[d]) for d in ranked if scores[d] > 0][:3]
    return dict(domain=domain, label=LABELS[domain], confidence=confidence, reason=reason,
                candidates=candidates, method='keywords',
                note='推荐仅用于资料归类，不代表法规真实性、效力或适用范围已核验；通用资料仍仅供当前账号使用。')
