<script setup>
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api, chatStream } from './api'
import { useAuth } from './store'
import AuthScreen from './components/AuthScreen.vue'
import ProfileDialog from './components/ProfileDialog.vue'
import AgentIcon from './components/AgentIcon.vue'
import { createRefreshScheduler } from './refreshScheduler.js'
import { createAgentTasks } from './agentTasks.js'
import { useJobNotices } from './jobNotices.js'

const route = useRoute(), router = useRouter(), auth = useAuth()
const defaultAgents = [
  { code: 'contract', name: '合同审查', icon: '▤', hint: '读懂条款，找到风险与依据。' },
  { code: 'labor', name: '劳动争议', icon: '▦', hint: '梳理劳动权益、适用条件与材料。' },
  { code: 'family', name: '婚姻家事', icon: '◇', hint: '理解财产、抚养与继承的法律规则。' },
  { code: 'consumer', name: '消费维权', icon: '◈', hint: '找到消费纠纷的责任与救济路径。' },
  { code: 'procedure', name: '诉讼程序', icon: '§', hint: '理清管辖、材料、期限与流程。' },
  { code: 'collaboration', name: '协作咨询', icon: '◎', hint: '让两个专业智能体一起梳理复杂问题。', extra: true },
]
const agents = ref(defaultAgents), docs = ref([]), jobs = ref([]), error = ref('')
const activity = reactive({ upload: false, preview: false, commit: false, retrieve: false })
const busy = computed(() => page.value === 'knowledge' ? activity.upload : page.value === 'chunks' ? activity.preview || activity.commit : activity.retrieve)
const profileVisible = ref(false), conversations = ref([])
const activeConversations = reactive({})
const viewportWidth = ref(window.innerWidth)
const compact = computed(() => viewportWidth.value <= 900)
const consultTab = ref('chat'), materialTab = ref('intake'), jobsVisible = ref(false)
const notices = useJobNotices(jobs, computed(() => auth.user?.id), jobsVisible, typeof localStorage === 'undefined' ? undefined : localStorage)
const jobCounts = notices.counts
function measureViewport() { viewportWidth.value = window.innerWidth }
const sourceVisible = ref(false), source = ref(null), sourceLoading = ref(false)
const isAgent = computed(() => route.path.startsWith('/agent/'))
const agent = computed(() => agents.value.find(a => a.code === route.params.code) || agents.value[0])
const page = computed(() => route.params.page || '')
const title = computed(() => isAgent.value ? agent.value.name : ({ knowledge: '资料入库与切分', chunks: '资料入库与切分', retrieval: '资料检索' }[page.value] || '我的资料'))
const tasks = createAgentTasks(), controllers = tasks.controllers
const sessions = reactive({}), drafts = reactive({}), stages = reactive({})
const question = computed({ get: () => drafts[agent.value.code] || '', set: value => { drafts[agent.value.code] = value } })
const sending = computed(() => Boolean(controllers[agent.value.code]))
const stage = computed(() => stages[agent.value.code] || '')
const current = computed(() => sessions[agent.value.code] || [])
const citations = computed(() => [...current.value].reverse().find(m => m.result)?.result?.citations || [])
let refreshController
const refreshing = ref(false), refreshedAt = ref(''), refreshError = ref(false)
const syncLabel = computed(() => refreshError.value ? '更新失败，正在重试' : refreshing.value ? '正在更新…' : refreshedAt.value ? `已更新 ${refreshedAt.value}` : '等待更新')
const samples = {
  contract: ['这份合同中的单方解除条款有哪些风险？', '违约金约定过高时，可以依据什么规则处理？'],
  labor: ['公司让我加班没给加班费，离职后还能主张吗？', '解除劳动合同，经济补偿需要满足哪些条件？'],
  family: ['父母为婚后买房出资，需要确认哪些事实？', '遗嘱形式和继承顺序有哪些规定？'],
  consumer: ['网购商品与宣传不符，应准备哪些证据？', '七天无理由退货有哪些适用条件？'],
  procedure: ['准备起诉前，应如何确认管辖和材料？', '判断诉讼时效，需要先确认哪些时间点？'],
  collaboration: ['网购商品涉嫌虚假宣传，商家拒绝退款，我该准备什么材料、向哪个法院起诉？'],
}
const upload = reactive({ domain: 'general', version: '', source_url: '', visibility: 'private', legal_status: 'unknown', verified_at: '', effective_date: '' })
const metadataVisible = ref(false), metadataSaving = ref(false), metadataError = ref(''), editingDocument = ref(null)
const metadataForm = reactive({ version: '', source_url: '', effective_date: '', verified_at: '', legal_status: 'unknown' })
function editMetadata(doc) {
  editingDocument.value = doc; metadataError.value = ''
  Object.keys(metadataForm).forEach(key => { metadataForm[key] = doc[key] || (key === 'legal_status' ? 'unknown' : '') })
  metadataVisible.value = true
}
async function saveMetadata() {
  if (!editingDocument.value || metadataSaving.value) return
  const userId = auth.user?.id, docId = editingDocument.value.id
  metadataSaving.value = true; metadataError.value = ''
  try {
    await api(`/rag/documents/${docId}/metadata`, { method: 'PATCH', body: JSON.stringify(metadataForm) })
    if (auth.user?.id !== userId) return
    metadataVisible.value = false; await refresh(); ElMessage.success('资料来源与版本已更新，无需重新入库')
  } catch (e) { if (auth.user?.id === userId) metadataError.value = e.message }
  finally { metadataSaving.value = false }
}
const selectedFile = ref(null), fileInput = ref(null)
const recommendation = ref(null), recommending = ref(false), recommendationError = ref('')
let recommendationController
const chunkForm = reactive({ document_id: '', max_chars: 800, overlap: 100 })
const preview = ref(null), committed = ref(false)
const search = reactive({ query: '', domain: 'contract', top_k: 5 }), hits = ref([]), searched = ref(false)
const statuses = { parsed: '已解析', indexing: '入库中', indexed: '已入库', queued: '排队中', running: '处理中', completed: '已完成', failed: '失败', partial: '部分完成', no_basis: '依据不足', cancelled: '已停止' }
const legalLabels = { current: '登记为现行', unknown: '尚未核验', repealed: '已废止', pending_effective: '尚未施行' }
const domains = [{ code: 'general', name: '通用法规' }, ...defaultAgents.filter(a => !a.extra)]
function clearRecommendation() {
  recommendationController?.abort(); recommendationController = null
  recommendation.value = null; recommendationError.value = ''; recommending.value = false
}
async function chooseFile(file) {
  clearRecommendation(); selectedFile.value = file || null
  if (!file || !auth.user) return
  const controller = new AbortController(), userId = auth.user.id
  recommendationController = controller; recommending.value = true
  const timeout = setTimeout(() => controller.abort(), 30000)
  try {
    const body = new FormData(); body.append('file', file)
    const result = await api('/rag/recommend-domain', { method: 'POST', body, signal: controller.signal })
    if (recommendationController === controller && auth.user?.id === userId) recommendation.value = result
  } catch (e) {
    if (recommendationController === controller && auth.user?.id === userId) recommendationError.value = e.name === 'AbortError' ? '分析超时，可手动选择业务范围或重新分析。' : '暂未获得归类建议，可手动选择业务范围。'
  } finally {
    clearTimeout(timeout)
    if (recommendationController === controller) { recommending.value = false; recommendationController = null }
  }
}
function applyRecommendation() {
  if (recommendation.value && domains.some(domain => domain.code === recommendation.value.domain)) upload.domain = recommendation.value.domain
}
function notifyError(e) { error.value = e.message || String(e) }
async function loadWorkspace() {
  if (!auth.user) return
  const userId = auth.user.id
  const request = new AbortController()
  refreshController = request
  const timeout = setTimeout(() => request.abort(), 10000)
  refreshing.value = true
  try {
    const [d, j] = await Promise.all([api('/rag/documents', { signal: request.signal }), api('/rag/jobs', { signal: request.signal })])
    if (auth.user?.id !== userId) return
    docs.value = d.filter(item => item.visibility === 'private' || auth.user?.is_admin); jobs.value = j
    refreshedAt.value = new Date().toLocaleTimeString('zh-CN', { hour12: false })
    refreshError.value = false
  } catch {
    if (auth.user?.id === userId) refreshError.value = true
  } finally {
    clearTimeout(timeout)
    if (refreshController === request) { refreshing.value = false; refreshController = null }
  }
}
const statusSync = createRefreshScheduler({ load: loadWorkspace, isActive: () => Boolean(auth.user) })
function refresh() { return statusSync.refresh() }
function resumeRefresh() { if (!document.hidden) statusSync.wake() }
function stopCurrent() { tasks.stop(agent.value.code) }
function stopAll() { tasks.stopAll() }
async function loadHistory() {
  if (!auth.user || !isAgent.value) return
  const code = agent.value.code, userId = auth.user.id
  try {
    const rows = await api('/conversations?agent_code=' + code)
    if (auth.user?.id === userId && agent.value.code === code) conversations.value = rows
  } catch (e) { notifyError(e) }
}
async function selectConversation(item) {
  stopCurrent()
  const code = agent.value.code, userId = auth.user?.id
  try {
    const rows = await api(`/conversations/${item.id}/messages`)
    if (auth.user?.id !== userId || agent.value.code !== code) return
    activeConversations[code] = item.id
    sessions[code] = rows.map(m => ({ ...m, error: m.role === 'assistant' && !m.result ? m.text : '' }))
    consultTab.value = 'chat'
  } catch (e) { notifyError(e) }
}
function newConversation() {
  stopCurrent(); delete activeConversations[agent.value.code]
  sessions[agent.value.code] = []; question.value = ''; stages[agent.value.code] = ''
}
async function deleteConversation(item) {
  try { await ElMessageBox.confirm('删除后，这段咨询记录将从你的账号中移除。', '删除会话', { type: 'warning' }) } catch { return }
  try {
    await api('/conversations/' + item.id, { method: 'DELETE' })
    if (activeConversations[agent.value.code] === item.id) newConversation()
    await loadHistory()
  } catch (e) { notifyError(e) }
}
async function send() {
  if (!question.value.trim() || sending.value) return
  if (!auth.user) { router.replace('/login'); return }
  const code = agent.value.code, query = question.value.trim()
  const userId = auth.user.id
  const controller = tasks.start(code)
  if (!sessions[code]) sessions[code] = []
  sessions[code].push({ role: 'user', text: query })
  const message = reactive({ role: 'assistant', result: null, error: '', stopped: false })
  sessions[code].push(message)
  question.value = ''; error.value = ''; stages[code] = '正在启动…'
  try {
    if (!activeConversations[code]) {
      const conversation = await api('/conversations', { method: 'POST', signal: controller.signal, body: JSON.stringify({ agent_code: code }) })
      if (auth.user?.id !== userId || controller.signal.aborted) return
      activeConversations[code] = conversation.id
    }
    await chatStream(code, query, controller.signal, (type, data) => {
      if (type === 'stage') stages[code] = data.detail
      if (type === 'result') message.result = data
      if (type === 'error') message.error = data.message
      if (type === 'done') stages[code] = statuses[data.status] || data.status
    }, activeConversations[code])
    if (auth.user?.id === userId && agent.value.code === code && isAgent.value) await loadHistory()
  } catch (e) {
    if (e.name === 'AbortError') message.stopped = true
    else message.error = e.message
  } finally { tasks.finish(code, controller) }
}
async function showSource(id) {
  const userId = auth.user?.id
  sourceVisible.value = true; sourceLoading.value = true; source.value = null
  try { const result = await api(`/rag/sources/${id}`); if (auth.user?.id === userId) source.value = result }
  catch (e) { notifyError(e) }
  finally { sourceLoading.value = false }
}
async function uploadDocument() {
  if (!selectedFile.value) { ElMessage.warning('请先选择文件'); return }
  activity.upload = true; error.value = ''
  const userId = auth.user?.id, startingRoute = route.fullPath
  try {
    const form = new FormData()
    form.append('file', selectedFile.value)
    Object.entries(upload).forEach(([k, v]) => form.append(k, v || (k === 'version' ? '未核验' : '')))
    const doc = await api('/rag/documents', { method: 'POST', body: form })
    if (auth.user?.id !== userId) return
    selectedFile.value = null; clearRecommendation(); if (fileInput.value) fileInput.value.value = ''
    await refresh(); ElMessage.success('解析完成，可以预览切块')
    if (route.fullPath === startingRoute) selectDocument(doc)
  } catch (e) { notifyError(e) }
  finally { activity.upload = false }
}
function selectDocument(doc) { chunkForm.document_id = doc.id; materialTab.value = 'split' }
async function deleteDocument(doc) {
  try { await ElMessageBox.confirm(`删除“${doc.name}”后将退出检索，原文及派生数据由后台清理。`, '删除资料', { type: 'warning' }) }
  catch { return }
  try { await api(`/rag/documents/${doc.id}`, { method: 'DELETE' }); await refresh(); ElMessage.success('已退出检索，后台正在清理') }
  catch (e) { notifyError(e) }
}
async function makePreview() {
  activity.preview = true; error.value = ''; preview.value = null; committed.value = false
  const userId = auth.user?.id, requestedConfig = JSON.stringify(chunkForm)
  try { const result = await api('/rag/chunks/preview', { method: 'POST', body: requestedConfig }); if (auth.user?.id === userId && JSON.stringify(chunkForm) === requestedConfig) { preview.value = result; materialTab.value = 'split' } }
  catch (e) { notifyError(e) }
  finally { activity.preview = false }
}
async function commitPreview() {
  if (!preview.value || committed.value) return
  activity.commit = true
  const submittedPreview = preview.value.preview_id, userId = auth.user?.id
  try {
    const result = await api('/rag/chunks/commit', { method: 'POST', body: JSON.stringify({ preview_id: submittedPreview, idempotency_key: submittedPreview }) })
    if (auth.user?.id !== userId) return
    if (preview.value?.preview_id === submittedPreview) committed.value = true
    ElMessage.success('资料已提交处理，请稍候'); await refresh()
  } catch (e) { notifyError(e) }
  finally { activity.commit = false }
}
async function retry(job) {
  notices.forget(job.id)
  try { await api(`/rag/jobs/${job.id}/retry`, { method: 'POST' }); await refresh(); ElMessage.success('任务已重新排队') }
  catch (e) { notifyError(e) }
}
async function retrieve() {
  activity.retrieve = true; error.value = ''; hits.value = []; searched.value = false
  const userId = auth.user?.id
  try { const result = await api('/rag/retrieve', { method: 'POST', body: JSON.stringify(search) }); if (auth.user?.id === userId) { hits.value = result.items; searched.value = true } }
  catch (e) { notifyError(e) }
  finally { activity.retrieve = false }
}
watch(() => route.fullPath, async () => {
  error.value = ''
  if (route.query.document) { chunkForm.document_id = String(route.query.document); materialTab.value = 'split' }
  if (!isAgent.value) await refresh()
  else await loadHistory()
})
watch(() => [chunkForm.document_id, chunkForm.max_chars, chunkForm.overlap], () => { preview.value = null; committed.value = false })
watch(() => auth.user?.id, async (userId, previous) => {
  if (userId === previous) return
  stopAll(); refreshController?.abort(); docs.value = []; jobs.value = []; hits.value = []; source.value = null
  refreshedAt.value = ''; refreshError.value = false
  Object.keys(drafts).forEach(key => delete drafts[key]); Object.keys(stages).forEach(key => delete stages[key])
  sourceVisible.value = false; jobsVisible.value = false; metadataVisible.value = false; editingDocument.value = null; error.value = ''
  selectedFile.value = null; clearRecommendation(); preview.value = null; committed.value = false; chunkForm.document_id = ''
  search.query = ''; searched.value = false
  Object.assign(upload, { domain: 'general', version: '', source_url: '', visibility: 'private', legal_status: 'unknown', verified_at: '', effective_date: '' })
  conversations.value = []; profileVisible.value = false
  Object.keys(sessions).forEach(key => delete sessions[key])
  Object.keys(activeConversations).forEach(key => delete activeConversations[key])
  if (!userId) { router.replace('/login'); return }
  if (auth.user?.is_admin) upload.visibility = 'public'
  if (['/login', '/register', '/forgot-password'].includes(route.path)) router.replace('/agent/contract')
  statusSync.wake()
  await loadHistory()
})
onMounted(async () => {
  window.addEventListener('resize', measureViewport)
  window.addEventListener('focus', resumeRefresh)
  window.addEventListener('online', resumeRefresh)
  document.addEventListener('visibilitychange', resumeRefresh)
  statusSync.start()
  sessionStorage.removeItem('legal-token')
  try {
    agents.value = await api('/agents')
    auth.setUser(await api('/auth/me'))
  } catch { auth.clear() }
  finally { auth.ready = true }
  if (!auth.user && !['/login', '/register', '/forgot-password'].includes(route.path)) router.replace('/login')
  if (route.query.document) { chunkForm.document_id = String(route.query.document); materialTab.value = 'split' }
  if (auth.user) { if (!isAgent.value) await refresh(); else await loadHistory() }
})
onUnmounted(() => {
  statusSync.stop(); refreshController?.abort(); clearRecommendation(); stopAll()
  window.removeEventListener('resize', measureViewport)
  window.removeEventListener('focus', resumeRefresh)
  window.removeEventListener('online', resumeRefresh)
  document.removeEventListener('visibilitychange', resumeRefresh)
})
</script>

<template>
  <div v-if="!auth.ready" class="app-loading">法序 · 正在载入…</div>
  <AuthScreen v-else-if="!auth.user" />
  <div v-else class="shell">
    <aside class="sidebar">
      <a class="brand" href="#/agent/contract"><span class="brand-mark">序</span><span>法序<small>LEGAL INTELLIGENCE</small></span></a>
      <div class="nav-label">专业智能体 <span>05</span></div>
      <nav><RouterLink v-for="a in agents.filter(x => !x.extra)" :key="a.code" :to="`/agent/${a.code}`" class="nav-item"><span class="nav-icon"><AgentIcon :code="a.code"/></span>{{ a.name }}<span class="nav-arrow">↗</span></RouterLink></nav>
      <div class="nav-label">多领域协作</div>
      <RouterLink to="/agent/collaboration" class="nav-item"><span class="nav-icon">◎</span>协作咨询<span class="extra-badge">协同</span></RouterLink>
      <div class="nav-label">我的资料空间</div>
      <nav><RouterLink to="/rag/knowledge" class="nav-item"><span class="nav-icon">▣</span>资料入库与切分</RouterLink><RouterLink to="/rag/retrieval" class="nav-item"><span class="nav-icon" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"><circle cx="10.5" cy="10.5" r="6.5"/><path d="m15.5 15.5 4.5 4.5"/></svg></span>资料检索</RouterLink></nav>
      <div class="sidebar-bottom"><button class="connection-button user-menu" aria-label="个人中心" @click="profileVisible = true"><img v-if="auth.user.avatar_url" :src="auth.user.avatar_url" alt="我的头像"><span v-else class="user-initial">{{ auth.user.display_name.slice(0,1) }}</span><span>{{ auth.user.display_name }}</span><span>个人中心 ↗</span></button></div>
    </aside>

    <main class="main">
      <div class="page-content">
        <div v-if="error" class="error-banner" role="alert"><span>{{ error }}</span><button @click="error = ''">×</button></div>


        <div class="workspace-content" :class="['page-' + page, { 'consult-workspace': isAgent }]">
        <template v-if="isAgent">
          <div class="page-heading"><div><div class="eyebrow">{{ agent.extra ? 'COLLABORATIVE CONSULTATION' : 'SPECIALIST WORKSPACE' }}</div><h1>{{ title }}<span class="heading-symbol"><AgentIcon :code="agent.code"/></span></h1><p>{{ agent.hint }}</p></div></div>
          <div v-if="compact" class="workspace-tabs"><button :class="{active:consultTab === 'chat'}" @click="consultTab = 'chat'">咨询</button><button :class="{active:consultTab === 'history'}" @click="consultTab = 'history'">咨询记录</button><button :class="{active:consultTab === 'sources'}" @click="consultTab = 'sources'">参考依据 · {{ citations.length }}</button></div>
          <div class="consult-layout">
            <aside class="consult-rail">
              <section class="surface consult-history" :class="{'consult-panel-hidden': compact && consultTab !== 'history'}">
                <div class="new-consultation"><button class="primary-button" @click="newConversation(); consultTab = 'chat'">新的咨询 +</button></div>
                <div class="panel-heading"><span>咨询记录</span><span class="count-badge">{{ conversations.length }}</span></div>
                <div class="consult-history-list"><div v-if="!conversations.length" class="empty-state">还没有咨询记录。</div><article v-for="item in conversations" :key="item.id" :class="['consult-history-item', {active:activeConversations[agent.code] === item.id}]"><button class="history-select" @click="selectConversation(item)"><span>{{ item.title }}</span><time>{{ item.updated_at?.slice(0,10) }}</time></button><button class="text-button danger history-delete" :aria-label="`删除咨询：${item.title}`" @click="deleteConversation(item)">删除</button></article></div>
              </section>
            <aside class="evidence-panel" :class="{'consult-panel-hidden': compact && consultTab !== 'sources'}"><div class="panel-heading"><span>参考依据</span><span class="count-badge">{{ citations.length.toString().padStart(2, '0') }}</span></div><div class="evidence-content"><div v-if="!citations.length" class="empty-evidence"><div class="outline-book">▤</div><h3>让每个依据都有出处</h3><p>回答生成后，这里将展示实际引用的<br>原文、资料版本与效力登记信息。</p><div class="evidence-steps"><span>01  检索相关资料</span><span>02  核对引用出处</span><span>03  展开原文复核</span></div></div><button v-for="(c, i) in citations" :key="c.id" class="citation-card" @click="showSource(c.id)"><span class="citation-index">{{ String(i + 1).padStart(2, '0') }}</span><b>{{ c.document_name }}</b><span class="citation-version">{{ c.version }} · {{ legalLabels[c.legal_status] || '尚未核验' }}</span><p>{{ c.text.slice(0, 140) }}…</p><span class="text-button">展开原文 ↗</span></button></div><div class="evidence-footnote">点击引用可查看原文，具体适用请结合实际情况核实。</div></aside>
            </aside>
            <section class="chat-panel" :class="{'consult-panel-hidden': compact && consultTab !== 'chat'}">
              <div class="panel-heading"><span><span class="dot"></span> {{ agent.extra ? '协作咨询' : '法律咨询' }}</span><span class="muted">仅保存于你的账号</span></div>
              <div class="conversation">
                <div v-if="!current.length" class="welcome">
                  <div class="welcome-emblem"><AgentIcon :code="agent.code"/></div><h2>把问题说清，让依据看得见。</h2><p>描述你的情形，系统将检索本域资料，<br>并给出带有原文出处的说明。</p>
                  <div class="question-label">可以从这些问题开始</div><button v-for="q in samples[agent.code]" :key="q" class="sample-question" @click="question = q">{{ q }}<span>↗</span></button>
                </div>
                <article v-for="(m, index) in current" :key="index" :class="['message', m.role]">
                  <div class="message-label">{{ m.role === 'user' ? '你提出的问题' : '法序 · ' + title }}</div>
                  <p v-if="m.role === 'user'">{{ m.text }}</p>
                  <template v-else>
                    <p v-if="m.error" class="inline-error">{{ m.error }}</p><p v-else-if="m.stopped" class="muted">已停止，本次回答未完成。</p>
                    <div v-else-if="!m.result" class="running"><span class="loading-dot"></span>{{ stage || '正在检索与核对依据…' }}</div>
                    <template v-if="m.result">
                      <span class="result-status">{{ statuses[m.result.status] || m.result.status }}</span>
                      <div v-for="(claim, i) in m.result.claims" :key="i" class="claim"><p>{{ claim.text }}</p><button class="text-button" @click="showSource(claim.evidence_id)">[{{ i + 1 }}] 查看依据 ↗</button></div>
                      <p v-if="m.result.notice">{{ m.result.notice }}</p><p v-if="m.result.status === 'no_basis' && !m.result.notice">当前资料不足以支持明确说明，请补充资料或事实。</p>
                      <div v-for="task in m.result.tasks" :key="task.agent_code" class="task-result"><h3>{{ agents.find(a => a.code === task.agent_code)?.name }} <span class="result-status">{{ statuses[task.status] }}</span></h3><p class="muted">{{ task.question }}</p><p v-if="task.message" class="inline-error">{{ task.message }}</p><div v-for="(claim, i) in task.claims" :key="i" class="claim"><p>{{ claim.text }}</p><button class="text-button" @click="showSource(claim.evidence_id)">查看原文 ↗</button></div><p v-for="fact in task.missing_facts" :key="fact">待补充：{{ fact }}</p></div>
                      <div v-if="m.result.missing_facts?.length" class="facts-box"><b>需要进一步确认</b><p v-for="fact in m.result.missing_facts" :key="fact">· {{ fact }}</p></div>
                      <p v-for="warning in m.result.warnings" :key="warning" class="warning">{{ warning }}</p>
                      <p class="answer-disclaimer">{{ m.result.disclaimer }}</p>
                    </template>
                  </template>
                </article>
              </div>
              <form class="composer" @submit.prevent="send"><textarea v-model="question" aria-label="咨询问题" placeholder="描述你的问题，可补充发生时间、地区与已有材料…" maxlength="4000" @keydown.ctrl.enter.prevent="send"></textarea><div class="composer-bottom"><span>同一会话可继续追问 · Ctrl + Enter 发送</span><button v-if="sending" type="button" class="secondary-button" @click="stopCurrent">停止</button><button v-else class="primary-button" :disabled="!question.trim()">检索并咨询 <span>↑</span></button></div></form>
            </section>

          </div>
        </template>

        <template v-else>
          <div class="page-heading"><div><div class="eyebrow">YOUR PRIVATE MATERIALS</div><h1>{{ title }}</h1><p>{{ page === 'retrieval' ? '从你的资料中查找相关内容，并查看原文出处。' : '上传资料，预览切分内容，确认后入库。' }}</p></div><div class="material-actions"><button class="secondary-button jobs-toggle" aria-label="查看资料处理记录" :aria-expanded="jobsVisible" @click="jobsVisible = !jobsVisible"><span>处理记录</span><span v-if="jobCounts.completed" class="job-counter completed" :aria-label="`${jobCounts.completed} 项完成未读`">{{ jobCounts.completed }}</span><span v-if="jobCounts.failed" class="job-counter failed" :aria-label="`${jobCounts.failed} 项失败未读`">{{ jobCounts.failed }}</span><span v-if="jobCounts.pending" class="job-counter pending" :aria-label="`${jobCounts.pending} 项排队或处理中`">{{ jobCounts.pending }}</span></button><button class="secondary-button" :disabled="refreshing" @click="refresh">{{ refreshing ? '正在更新…' : '刷新状态 ↻' }}</button></div></div>

          <template v-if="page !== 'retrieval'">
            <div v-if="compact" class="workspace-tabs"><button :class="{active:materialTab === 'intake'}" @click="materialTab = 'intake'">资料入库</button><button :class="{active:materialTab === 'split'}" @click="materialTab = 'split'">资料切分</button></div>
            <div class="material-workspace">
              <div v-show="!compact || materialTab === 'intake'" class="material-column intake-column">
                <section class="surface intake-upload"><div class="panel-heading"><span>{{ auth.user.is_admin ? '上传资料' : '上传我的资料' }}</span><span class="muted">TXT / MD / PDF / DOCX · ≤10 MB</span></div><div class="upload-body"><label class="file-zone"><span class="upload-icon">↥</span><b>{{ selectedFile?.name || '选择要入库的文件' }}</b><span>上传后预览切分内容</span><input ref="fileInput" type="file" accept=".txt,.md,.pdf,.docx" @change="chooseFile($event.target.files[0])"></label><div class="upload-form"><label v-if="auth.user.is_admin" class="wide">使用范围<el-select v-model="upload.visibility" aria-label="资料使用范围"><el-option label="公共资料 · 所有账号可检索" value="public"/><el-option label="个人资料 · 仅自己可检索" value="private"/></el-select></label><label>业务范围<el-select v-model="upload.domain" aria-label="资料业务范围"><el-option v-for="d in domains" :key="d.code" :label="d.name" :value="d.code" /></el-select></label><label>资料说明<el-input v-model="upload.version" placeholder="例如：合同草稿" /></label><label class="wide">来源链接（可选）<el-input v-model="upload.source_url" placeholder="https://…" /></label><button class="primary-button wide" :disabled="activity.upload || !selectedFile" @click="uploadDocument">{{ activity.upload ? '正在解析…' : '上传并解析 →' }}</button></div></div><div class="upload-routing"><p class="general-scope-note">{{ upload.domain === 'general' ? (upload.visibility === 'public' ? '公共通用法规入库后，可供所有账号的智能体检索。' : '通用法规及跨领域资料可供所有智能体检索，仅限你的账号。') : (upload.visibility === 'public' ? '已选择' + domains.find(d => d.code === upload.domain)?.name + '，入库后可供所有账号检索。' : '已选择' + domains.find(d => d.code === upload.domain)?.name + '，资料仅限你的账号检索。') }}</p><p v-if="recommending" role="status">正在分析文件，推荐业务范围…</p><div v-else-if="recommendation" class="routing-suggestion"><div><b>建议：{{ recommendation.label }}</b><span v-if="recommendation.confidence === 'low'" class="routing-uncertain">请核对</span><button class="text-button" :disabled="upload.domain === recommendation.domain" @click="applyRecommendation">{{ upload.domain === recommendation.domain ? '已选择' : '采用推荐' }}</button></div><p>{{ recommendation.reason }}</p><small>根据文件名与正文关键词推荐，可手动改选；不代表法规效力已核验。</small></div><p v-else-if="recommendationError" class="routing-error">{{ recommendationError }}<button class="text-button" @click="chooseFile(selectedFile)">重新分析</button></p></div></section>
                <section class="surface material-documents"><div class="panel-heading"><span>资料列表</span><span class="muted">{{ docs.length }} 份</span></div><el-table :data="docs" height="100%" empty-text="上传第一份资料开始。"><el-table-column prop="name" label="资料名称" min-width="145" show-overflow-tooltip/><el-table-column v-if="auth.user.is_admin" label="可见性" width="75"><template #default="{ row }">{{ row.visibility === 'public' ? '公共' : '个人' }}</template></el-table-column><el-table-column label="范围" width="90"><template #default="{ row }">{{ domains.find(d => d.code === row.domain)?.name }}</template></el-table-column><el-table-column label="状态" width="80"><template #default="{ row }"><span class="result-status">{{ statuses[row.status] }}</span></template></el-table-column><el-table-column label="操作" fixed="right" align="center" header-align="center" :width="auth.user.is_admin ? 180 : 125"><template #default="{ row }"><div v-if="row.can_manage !== false" class="document-actions"><button class="document-action" @click="selectDocument(row)">切分</button><button v-if="auth.user.is_admin" class="document-action" @click="editMetadata(row)">编辑</button><button class="document-action document-delete" @click="deleteDocument(row)">删除</button></div><span v-else class="muted">只读</span></template></el-table-column></el-table></section>
              </div>
              <div v-show="!compact || materialTab === 'split'" class="material-column split-column">
                <section class="surface split-settings"><div class="panel-heading"><span>切分设置</span><span class="muted">调整参数后重新预览</span></div><div class="chunk-controls"><label>选择资料<el-select v-model="chunkForm.document_id" placeholder="请选择已解析资料"><el-option v-for="d in docs.filter(item => item.can_manage !== false)" :key="d.id" :label="d.name" :value="d.id" /></el-select></label><label>分段最大字符<el-input-number v-model="chunkForm.max_chars" :min="200" :max="2000" :step="100"/></label><label>重叠字符<el-input-number v-model="chunkForm.overlap" :min="0" :max="900" :step="25"/></label><div class="split-buttons"><button class="secondary-button" :disabled="activity.preview || activity.commit || !chunkForm.document_id" @click="makePreview">{{ activity.preview ? '正在切分…' : '生成预览' }}</button><button class="primary-button" :disabled="activity.preview || activity.commit || !preview || committed" @click="commitPreview">{{ activity.commit ? '正在提交…' : committed ? '已提交入库' : '确认入库 →' }}</button></div></div></section>
                <section class="surface material-preview"><div class="panel-heading"><span>切分预览</span><span class="muted">{{ preview ? `${preview.count} 块` : '等待预览' }}</span></div><div v-if="!preview" class="empty-state">选择左侧资料，生成预览后确认入库。</div><div v-else class="chunk-grid"><article v-for="c in preview.chunks" :key="c.ordinal" class="chunk-card"><div><b>{{ c.article || `文本块 ${c.ordinal + 1}` }}</b><span>{{ c.start }}–{{ c.end }}</span></div><pre>{{ c.text }}</pre></article></div></section>
              </div>
            </div>
          </template>

          <template v-if="page === 'retrieval'">
            <section class="surface search-surface"><form @submit.prevent="retrieve"><el-input v-model="search.query" placeholder="输入法律问题、关键词或条文内容…" size="large" maxlength="4000"/><div class="search-settings"><label>业务范围<el-select v-model="search.domain"><el-option v-for="d in domains" :key="d.code" :label="d.name" :value="d.code"/></el-select></label><label>结果数量<el-input-number v-model="search.top_k" :min="1" :max="10"/></label><button class="primary-button" :disabled="busy || search.query.trim().length < 2">{{ busy ? '检索中…' : '查找资料 →' }}</button></div></form></section><section class="surface retrieval-panel"><div class="panel-heading"><span>命中结果</span><span class="muted">{{ hits.length }} 条相关内容</span></div><div class="search-results"><p v-if="hits.some(hit => hit.retrieval_degraded)" class="retrieval-notice">向量检索暂时不可用，当前显示关键词匹配结果。</p><div v-if="!hits.length" class="empty-state">{{ searched ? '没有匹配结果，请调整问题或补充资料。' : '选择业务范围，查找相关资料。' }}</div><article v-for="(hit, i) in hits" :key="hit.id" class="search-hit"><div><span class="citation-index">{{ String(i + 1).padStart(2, '0') }}</span><b>{{ hit.document_name }} {{ hit.article }}</b></div><p>{{ hit.text }}</p><button class="text-button" @click="showSource(hit.id)">查看原文与上下文 ↗</button><span class="run-meta">{{ hit.version }} · {{ legalLabels[hit.legal_status] || '尚未核验' }}</span></article></div></section>
          </template>


        </template>
        </div>
        <footer class="page-footer"><span>法序 · 让法律信息有据可循</span><span>仅供信息参考，重大事项请咨询执业律师。</span></footer>
      </div>
    </main>

    <el-drawer v-model="jobsVisible" title="资料处理记录" size="min(560px, 100vw)"><div class="job-drawer-heading"><span class="muted" role="status" aria-live="polite">{{ syncLabel }}</span><span class="muted">{{ jobs.length }} 条记录</span></div><div v-if="!jobs.length" class="empty-state">暂无处理记录。</div><article v-for="job in jobs" :key="job.id" class="job-record"><div><b>{{ docs.find(doc => doc.id === job.document_id)?.name || '已删除的资料' }}</b><span :class="['job-record-status', job.status]">{{ statuses[job.status] }}</span></div><p>{{ job.kind === 'delete' ? '删除清理' : '资料入库' }}</p><div v-if="job.status === 'failed'" class="job-status"><span class="job-hint">处理未完成，可稍后重试</span><button class="text-button" @click="retry(job)">重试</button></div></article></el-drawer>
    <el-dialog v-model="metadataVisible" title="维护来源与版本" width="540px"><p class="dialog-note">{{ editingDocument?.name }} · 仅更新登记信息，不改动原文和向量索引。</p><form class="profile-form" @submit.prevent="saveMetadata"><label>版本说明<input v-model="metadataForm.version" required maxlength="100" placeholder="例如：2023年第五次修正"></label><label>来源链接<input v-model="metadataForm.source_url" type="url" maxlength="2000" placeholder="https://…"></label><label>本版施行日期<input v-model="metadataForm.effective_date" type="date"></label><label>效力状态<el-select v-model="metadataForm.legal_status"><el-option v-for="(label, code) in legalLabels" :key="code" :value="code" :label="label"/></el-select></label><label>核验人及时间<input v-model="metadataForm.verified_at" maxlength="40" placeholder="例如：管理员，2026-10-28"></label><p class="dialog-note">效力状态由管理员核实后登记，系统不自动认定；登记非“尚未核验”状态时须填写来源、版本和核验记录。</p><p v-if="metadataError" class="inline-error" role="alert">{{ metadataError }}</p><button class="primary-button" :disabled="metadataSaving">{{ metadataSaving ? '正在保存…' : '保存登记信息' }}</button></form></el-dialog>
    <ProfileDialog v-model="profileVisible" :agents="agents" />

    <el-drawer v-model="sourceVisible" title="原文与来源" size="min(650px, 100vw)"><div v-loading="sourceLoading"><template v-if="source"><h2>{{ source.document.name }}</h2><p class="muted">{{ source.document.version }} · {{ legalLabels[source.document.legal_status] || '尚未核验' }}</p><p v-if="source.document.source_url"><a :href="source.document.source_url" target="_blank" rel="noopener noreferrer">打开登记来源 ↗</a></p><p class="dialog-note">{{ source.document.verified_at || '尚未登记核验信息' }} · 原文区间 {{ source.original_start }}–{{ source.original_end }}</p><pre class="source-text">{{ source.text.slice(0, source.highlight_start) }}<mark>{{ source.text.slice(source.highlight_start, source.highlight_end) }}</mark>{{ source.text.slice(source.highlight_end) }}</pre></template></div></el-drawer>
  </div>
</template>
