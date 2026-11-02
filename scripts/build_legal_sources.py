"""从官方公开页面生成可入库的法律法规文本。

设计约束：
1. 只做「重排空白 + 补条号分隔符」，不增删改任何一个字。
2. 每部法必须通过条号连续性校验（第1条..第N条 一条不缺、不重、不乱序），否则不写文件。
3. 记录来源 URL、发文字号、施行日期与 sha256，便于人工回溯核验。

用法（在项目根目录）：
    .venv/Scripts/python.exe scripts/build_legal_sources.py
"""
import hashlib
import html
import json
import re
import sys
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / 'data' / 'legal-sources'
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/120.0 Safari/537.36')

CN_DIGITS = '零一二三四五六七八九'
CN_CHARS = '一二三四五六七八九十百千零〇'
DIGIT = {c: i for i, c in enumerate(CN_DIGITS)}
DIGIT['〇'] = 0
UNIT = {'十': 10, '百': 100, '千': 1000}

LAWS = [
    {
        'file': '中华人民共和国民法典.txt',
        'name': '中华人民共和国民法典',
        'domain': 'general',
        'url': 'https://www.beijing.gov.cn/zhengce/zhengcefagui/202006/t20200602_1913330.html',
        'start': '第一条为了保护',
        'end': '同时废止。',
        'expect': 1260,
        'publisher': '全国人民代表大会',
        'doc_no': '中华人民共和国主席令第四十五号',
        'version': '2020年5月28日第十三届全国人民代表大会第三次会议通过',
        'effective_date': '2021-01-01',
        'note': '制定机关为全国人民代表大会（非全国人大常委会），迄今未作修正，无版本沿革问题。'
                '七编合一，故登记为通用资料，五个业务域均可检索。',
    },
    {
        'file': '中华人民共和国劳动合同法.txt',
        'name': '中华人民共和国劳动合同法',
        'domain': 'labor',
        'url': 'https://www.beijing.gov.cn/zhengce/zhengcefagui/qtwj/201307/t20130701_780700.html',
        'start': '第一条为了完善劳动合同制度',
        'end': '本法自2008年1月1日起施行。',
        'expect': 98,
        'publisher': '全国人民代表大会常务委员会',
        'doc_no': '中华人民共和国主席令第七十三号',
        'version': ('2007年6月29日第十届全国人民代表大会常务委员会第二十八次会议通过，'
                    '根据2012年12月28日第十一届全国人民代表大会常务委员会第三十次会议'
                    '《关于修改〈中华人民共和国劳动合同法〉的决定》修正'),
        'effective_date': '2013-07-01',
        'note': '附则第九十八条自述施行日为2008-01-01，那是2007年立法文本的施行日；'
                '现行文本以2012年修正决定自2013-07-01施行为准，两者不可混用。',
    },
    {
        'file': '中华人民共和国消费者权益保护法.txt',
        'name': '中华人民共和国消费者权益保护法',
        'domain': 'consumer',
        'url': 'https://www.beijing.gov.cn/zhengce/zhengcefagui/qtwj/201507/t20150706_780640.html',
        'start': '第一条为保护消费者的合法权益',
        'end': '本法自1994年1月1日起施行。',
        'expect': 63,
        'publisher': '全国人民代表大会常务委员会',
        'doc_no': '中华人民共和国主席令第七号',
        'version': ('1993年10月31日第八届全国人民代表大会常务委员会第四次会议通过，'
                    '根据2009年8月27日第十一届全国人民代表大会常务委员会第十次会议'
                    '《关于修改部分法律的决定》第一次修正，'
                    '根据2013年10月25日第十二届全国人民代表大会常务委员会第五次会议'
                    '《关于修改〈中华人民共和国消费者权益保护法〉的决定》第二次修正'),
        'effective_date': '2014-03-15',
        'note': '附则第六十三条自述施行日为1994-01-01（1993年立法文本）；'
                '2013年第二次修正决定自2014-03-15施行，现行文本以修正决定为准。',
    },
    {
        'file': '中华人民共和国民事诉讼法.txt',
        'name': '中华人民共和国民事诉讼法',
        'domain': 'procedure',
        'url': 'http://jlfy.e-court.gov.cn/law/detail/2023/09/id/151386.shtml',
        'verify_url': 'https://zy.wxfy.gov.cn/article/detail/2024/02/id/7817306.shtml',
        'start': '第一条中华人民共和国民事诉讼法以宪法为根据',
        'end': '同时废止。',
        'expect': 306,
        'publisher': '全国人民代表大会',
        'doc_no': '中华人民共和国主席令第十一号',
        'version': ('1991年4月9日第七届全国人民代表大会第四次会议通过，'
                    '历经2007、2012、2017、2021年四次修正，'
                    '根据2023年9月1日第十四届全国人民代表大会常务委员会第五次会议'
                    '《关于修改〈中华人民共和国民事诉讼法〉的决定》第五次修正'),
        'effective_date': '2024-01-01',
        'note': ('制定机关为全国人民代表大会，其后五次修正均由全国人大常委会决定，二者不可混称。'
                 '2023年修正大幅扩充涉外编，条数由284条增至306条。'
                 '已与无锡中院转载版逐条比对：306条中仅5条不同，且差异全部集中在条文内部交叉引用，'
                 '副源引用的条号统一比现行条号小4条（其文本仍停留在2023年修正前的编号）。'
                 '被引用条文的内容可反向验证主源正确：现行第210条是「申请再审」、第211条是「应当再审的13种情形」、'
                 '第253至255条是三种执行措施，正好对应各条所需引用。故采用主源，副源不作依据。'),
        'cross_check_basis': {
            215: ('现行第二百一十条是「当事人认为判决、裁定有错误可向上一级人民法院申请再审」；'
                  '副源所引第二百零六条是「司法确认调解协议」，与申请再审无关。'),
            216: ('现行第二百一十一条是「应当再审的13种情形」，本条正需据此判断再审事由；'
                  '副源所引第二百零七条是「申请实现担保物权」。'),
            219: ('抗诉须以第二百一十一条的再审事由为依据；副源所引第二百零七条是「申请实现担保物权」，与抗诉无关。'),
            222: ('同上，抗诉案件的再审事由援引第二百一十一条；副源所引第二百零七条与再审事由无关。'),
            265: ('现行第二百五十三、二百五十四、二百五十五条分别是查询存款债券、扣留提取收入、'
                  '查封扣押冻结拍卖变卖财产，即执行措施；副源所引第二百四十九至二百五十一条依次是'
                  '公证债权文书执行、申请执行期间、执行通知，均不属于执行措施。'),
        },
    },
]


def cn_to_int(text):
    if text.isdigit():
        return int(text)
    total = section = number = 0
    for ch in text:
        if ch in DIGIT:
            number = DIGIT[ch]
        elif ch in UNIT:
            unit = UNIT[ch]
            if number == 0 and unit == 10:
                number = 1
            section += number * unit
            number = 0
        else:
            return None
    return total + section + number


def fetch(url):
    # trust_env=False：不读系统代理，否则本机会因代理 TLS 失败（与 app/provider.py 一致）。
    with httpx.Client(timeout=40, follow_redirects=True, trust_env=False,
                      headers={'User-Agent': UA}) as client:
        response = client.get(url)
        response.raise_for_status()
        raw = response.content
    for encoding in ('utf-8', 'gb18030'):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise SystemExit(f'无法解码页面: {url}')


def flatten(page):
    """去掉标签与全部空白，只保留字符本身及其顺序。"""
    text = re.sub(r'<script.*?</script>', '', page, flags=re.S | re.I)
    text = re.sub(r'<style.*?</style>', '', text, flags=re.S | re.I)
    text = re.sub(r'<[^>]+>', '', text)
    return re.sub(r'\s+', '', html.unescape(text))


def paragraphs(page):
    """按 HTML 段落取正文，用于结构规整的页面（每条法条独立成段）。"""
    holder = []

    class Collector(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.buffer = []
            self.depth = 0
            self.skip = 0

        def handle_starttag(self, tag, attrs):
            if tag in ('script', 'style'):
                self.skip += 1
            elif tag == 'p':
                self.depth += 1
                if self.depth == 1:
                    self.buffer = []

        def handle_endtag(self, tag):
            if tag in ('script', 'style'):
                self.skip = max(0, self.skip - 1)
            elif tag == 'p' and self.depth:
                self.depth -= 1
                if self.depth == 0:
                    text = ''.join(self.buffer).strip()
                    if text:
                        holder.append(text)
                    self.buffer = []

        def handle_data(self, data):
            if self.depth and not self.skip:
                self.buffer.append(data)

    Collector().feed(page)
    return holder


HEADING = re.compile(r'^第[' + CN_CHARS + r'\d]+(?:分)?[编章节]|^附\s*则$|^目\s*录$')
NOISE = re.compile(r'版权所有|免责声明|责任编辑|未经协议授权|网站地图|联系我们|ICP备|Copyright|'
                   r'相关解读|相关政策|您访问的链接|分享：')
TAIL_NOISE = re.compile(r'(?:评价建议|分享：|相关解读|相关政策|责任编辑|版权所有|免责声明|'
                        r'您访问的链接).*$', re.S)


def split_by_paragraphs(page):
    """依赖网页段落结构：以「第X条」开头的段落是新条文，其余段落并入当前条文。"""
    articles = []
    for text in paragraphs(page):
        match = re.match(r'第([' + CN_CHARS + r']+)条', text)
        if match:
            number = cn_to_int(match.group(1))
            if number:
                articles.append([number, match.group(0), text])
                continue
        if HEADING.match(text) or NOISE.search(text):
            continue
        if articles:
            articles[-1][2] += text
    return [(n, h, TAIL_NOISE.sub('', t).strip()) for n, h, t in articles]


def split_articles(flat, start, end, expect):
    begin = flat.find(start)
    if begin < 0:
        raise SystemExit(f'  未找到起点标记「{start[:20]}」')
    stop = flat.find(end, begin)
    if stop < 0:
        raise SystemExit(f'  未找到终点标记「{end}」')
    body = flat[begin:stop + len(end)]

    starts, expected = [], 1
    for match in re.finditer(r'第([' + CN_CHARS + r']+)条', body):
        if cn_to_int(match.group(1)) != expected:
            continue
        # 条文内的引用（如「依据本法第三百零一条的规定」「第一百二十条至第一百二十五条」）
        # 条号也会递增，但其后紧跟「的/至/、」等连接字，真正的条文起点不会。
        # 注意不能要求前一个字符是句号：条文起点前也可能是章节标题。
        if body[match.end():match.end() + 1] in {'的', '至', '、', '和', '及', '所', '规'}:
            continue
        starts.append((match.start(), expected, match.group(0)))
        expected += 1

    articles = []
    for index, (pos, number, head) in enumerate(starts):
        tail = starts[index + 1][0] if index + 1 < len(starts) else len(body)
        articles.append((number, head, body[pos:tail].strip()))
    return articles


def audit(articles, expect):
    problems = []
    numbers = [n for n, _, _ in articles]
    if len(articles) != expect:
        problems.append(f'条数不符：期望 {expect}，实际 {len(articles)}')
    if numbers != list(range(1, len(numbers) + 1)):
        missing = sorted(set(range(1, max(numbers) + 1)) - set(numbers))
        problems.append(f'条号不连续，缺 {missing[:10]}')
    for number, head, text in articles:
        if not text.startswith(head):
            problems.append(f'{head} 起始异常')
        if len(text) <= len(head) + 6:
            problems.append(f'{head} 内容过短（{len(text)}字）')
        # 法条正文必以句末标点收尾；否则说明粘了章节标题、站点水印或串条。
        if not text.endswith(('。', '；')):
            problems.append(f'{head} 结尾异常：{text[-12:]!r}')
    return problems


def render(articles):
    """条号与正文之间补一个全角空格作为分隔，与公报排版一致，便于切块识别条边界。"""
    return '\n\n'.join(head + '　' + text[len(head):].strip()
                       for _, head, text in articles) + '\n'


CHAPTER = re.compile(r'第[' + CN_CHARS + r'\d]+(?:分)?[编章节]')


def canon(text):
    """比对用归一化：忽略空白、统一全角标点，并截掉条尾粘连的章节标题。
    有的转载页把「第二章 管辖 第一节 级别管辖」并进条文段落，
    法条正文本身不会出现「第X章/节/编」字样，因此截断不影响内容比对。"""
    text = re.sub(r'\s+', '', text)
    for half, full in (('(', '（'), (')', '）'), (',', '，'), (';', '；'), (':', '：')):
        text = text.replace(half, full)
    match = CHAPTER.search(text)
    return text[:match.start()] if match else text


def extract(page, law):
    """优先按段落结构提取；结构不可用时回退到文本模式，两者都必须通过同一套校验。"""
    try:
        candidate = split_by_paragraphs(page)
        if not audit(candidate, law['expect']):
            return candidate, '段落结构'
    except Exception:
        pass
    flat = flatten(page)
    return split_articles(flat, law['start'], law['end'], law['expect']), '文本模式'


CITE = re.compile(r'本法((?:第[' + CN_CHARS + r']+条[、和]?)+)')
ARTICLE_REF = re.compile(r'第[' + CN_CHARS + r']+条')


def cites(text):
    """取出「本法第X条」及其并列列举（如「本法第二百五十三条、第二百五十四条、第二百五十五条」）。"""
    found = []
    for match in CITE.finditer(text):
        found.extend(ARTICLE_REF.findall(match.group(1)))
    return list(dict.fromkeys(found))


def compare(articles, other, basis):
    """逐条比对两个来源。差异条目连同「各自引用了哪一条」「为什么采信主源」一起记录，
    否则只留条号列表，后人无法复核结论。"""
    mine = {n: t for n, _, t in articles}
    theirs = {n: t for n, _, t in other}
    diff = [n for n in sorted(mine) if canon(mine[n]) != canon(theirs.get(n, ''))]
    detail = []
    for number in diff:
        item = {'article': number,
                'main_cites': cites(mine[number]),
                'other_cites': cites(theirs.get(number, ''))}
        if number in basis:
            item['basis'] = basis[number]
        detail.append(item)
    return diff, detail, len(set(mine) & set(theirs))


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    manifest = {
        'generated_at': datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds'),
        'generator': 'scripts/build_legal_sources.py',
        'clean_rules': [
            '仅删除 HTML 标签与全部空白字符，不修改任何文字或标点。',
            '条文条号与正文之间补一个全角空格，作为切块识别条边界的排版分隔。',
            '不保留编、章、节标题，仅保留条文，确保每条以「第X条」开头。',
            '每条之间空一行，供后端 split_text 按条边界切块。',
        ],
        'sources': [],
    }
    failed = False

    for law in LAWS:
        print(f'[处理] {law["name"]}')
        page = fetch(law['url'])
        articles, method = extract(page, law)
        problems = audit(articles, law['expect'])
        if problems:
            failed = True
            print(f'  校验未通过，跳过写文件：{problems[:6]}')
            continue
        content = render(articles)
        target = OUT_DIR / law['file']
        target.write_text(content, encoding='utf-8', newline='\n')
        digest = hashlib.sha256(content.encode()).hexdigest()
        print(f'  提取方式：{method}；条文 {len(articles)} 条，{len(content)} 字符，sha256 {digest[:16]}…')

        verification = {'articles': len(articles), 'chars': len(content), 'sha256': digest,
                        'method': method,
                        'checks': '条号 1..N 连续，每条以原文条号开头且含完整句'}
        if law.get('verify_url'):
            print(f'  交叉验证：{law["verify_url"]}')
            other, _ = extract(fetch(law['verify_url']), law)
            diff, detail, shared = compare(articles, other, law.get('cross_check_basis', {}))
            verification['cross_check'] = {
                'url': law['verify_url'],
                'articles_compared': shared,
                'differing_articles': diff,
                'result': ('逐条一致（已归一化全角/半角标点与章节标题）' if not diff
                           else f'{len(diff)} 条差异，见 differences 逐条对照'),
                'differences': detail,
            }
            print(f'  交叉验证结果：{verification["cross_check"]["result"]}')
            for item in detail:
                print(f'    第{item["article"]}条 主源引 {item["main_cites"]} / 副源引 {item["other_cites"]}')

        manifest['sources'].append({
            'file': law['file'],
            'name': law['name'],
            'domain': law['domain'],
            'visibility': 'private',
            'legal_status': 'current',
            'version': law['version'],
            'effective_date': law['effective_date'],
            'source_url': law['url'],
            'publisher': law['publisher'],
            'document_no': law['doc_no'],
            'note': law['note'],
            'verification': verification,
        })

    if failed:
        print('\n存在校验失败的法律，manifest 未写出。')
        return 1

    path = OUT_DIR / 'manifest.json'
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n',
                    encoding='utf-8', newline='\n')
    print(f'\n已写出 {len(manifest["sources"])} 部法律与 manifest：{path}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
