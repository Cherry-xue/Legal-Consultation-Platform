<script setup>
import { reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '../api'
import { useAuth } from '../store'
import AvatarCrop from './AvatarCrop.vue'

const props = defineProps({ modelValue: Boolean, agents: Array })
const emit = defineEmits(['update:modelValue', 'logout'])
const auth = useAuth(), section = ref('profile'), displayName = ref(''), busy = ref(false), error = ref('')
const avatarFile = ref(null), cropVisible = ref(false), fileInput = ref(null)
const memories = ref([]), note = reactive({ content: '', agent_code: 'general' })
const password = reactive({ old_password: '', new_password: '', confirm: '' })
const recoveryPassword = ref(''), recoveryCode = ref('')
const deletion = reactive({ password: '', confirmation: '' })
function clearSecrets() {
  password.old_password = ''; password.new_password = ''; password.confirm = ''
  recoveryPassword.value = ''; recoveryCode.value = ''; deletion.password = ''; deletion.confirmation = ''
}
watch(() => auth.user?.id, clearSecrets)
watch(() => props.modelValue, async value => {
  if (!value) { clearSecrets(); return }
  section.value = 'profile'; displayName.value = auth.user?.display_name || ''; error.value = ''
  try { memories.value = await api('/memories') } catch (e) { error.value = e.message }
})
async function saveProfile() {
  busy.value = true; error.value = ''
  const userId = auth.user?.id
  try { auth.setUser(await api('/auth/me', { method: 'PATCH', body: JSON.stringify({ display_name: displayName.value }) }), userId); if (auth.user?.id === userId) ElMessage.success('个人资料已更新') }
  catch (e) { error.value = e.message } finally { busy.value = false }
}
function selectAvatar(event) {
  const file = event.target.files[0]
  event.target.value = ''
  if (file) { avatarFile.value = file; cropVisible.value = true }
}
async function changePassword() {
  error.value = ''
  if (password.new_password !== password.confirm) { error.value = '两次新密码不一致。'; return }
  busy.value = true
  try {
    await api('/auth/password', { method: 'POST', body: JSON.stringify(password) })
    password.old_password = ''; password.new_password = ''; password.confirm = ''
    ElMessage.success('密码已更新，其他设备的登录已失效')
  } catch (e) { error.value = e.message } finally { busy.value = false }
}
async function addMemory() {
  if (!note.content.trim()) return
  busy.value = true; error.value = ''
  try { memories.value.unshift(await api('/memories', { method: 'POST', body: JSON.stringify(note) })); note.content = '' }
  catch (e) { error.value = e.message } finally { busy.value = false }
}
async function removeMemory(id) {
  try { await api('/memories/' + id, { method: 'DELETE' }); memories.value = memories.value.filter(m => m.id !== id) }
  catch (e) { error.value = e.message }
}
async function logout() {
  busy.value = true; error.value = ''
  try { await api('/auth/logout', { method: 'POST' }); auth.clear(); emit('update:modelValue', false); emit('logout') }
  catch (e) { error.value = e.message } finally { busy.value = false }
}
async function generateRecovery() {
  busy.value = true; error.value = ''; recoveryCode.value = ''
  const userId = auth.user?.id
  try {
    const result = await api('/auth/recovery-code', { method: 'POST', body: JSON.stringify({ password: recoveryPassword.value }) })
    if (props.modelValue && auth.user?.id === userId) recoveryCode.value = result.recovery_code
    recoveryPassword.value = ''
  } catch (e) { error.value = e.message } finally { busy.value = false }
}
async function deleteAccount() {
  if (busy.value) return
  busy.value = true
  const userId = auth.user?.id
  try {
    await ElMessageBox.confirm('注销后无法恢复账号、私人资料、咨询记录和记忆，所有登录将失效。文件与向量在后台清理。确定注销？', '确认注销账号', { confirmButtonText: '确认注销', cancelButtonText: '取消', type: 'warning' })
  } catch { busy.value = false; return }
  if (auth.user?.id !== userId) { busy.value = false; return }
  busy.value = true; error.value = ''
  try {
    const result = await api('/auth/delete-account', { method: 'POST', body: JSON.stringify(deletion) })
    if (auth.user?.id === userId) { auth.clear(); clearSecrets(); emit('update:modelValue', false); emit('logout'); ElMessage.success(result.message) }
  } catch (e) { error.value = e.message } finally { busy.value = false }
}
</script>

<template>
  <el-dialog :model-value="modelValue" title="个人中心" width="560px" @update:model-value="emit('update:modelValue', $event)">
    <div class="profile-tabs"><button v-for="tab in [{ code:'profile', name:'个人资料' },{code:'memories',name:'我的记忆'},{code:'security',name:'账号安全'}]" :key="tab.code" :class="{active:section === tab.code}" @click="section = tab.code; error = ''">{{ tab.name }}</button></div>
    <p v-if="error" class="auth-error" role="alert">{{ error }}</p>
    <div v-if="section === 'profile'" class="profile-section">
      <div class="profile-avatar-row"><div class="large-avatar"><img v-if="auth.user?.avatar_url" :src="auth.user.avatar_url" alt="我的头像"><span v-else>{{ auth.user?.display_name?.slice(0,1) }}</span></div><div><button class="secondary-button" @click="fileInput.click()">上传并裁剪头像</button><p class="dialog-note">JPG / PNG / WebP，最大 5 MB</p></div><input ref="fileInput" type="file" hidden accept="image/jpeg,image/png,image/webp" @change="selectAvatar"></div>
      <form class="profile-form" @submit.prevent="saveProfile"><label>账号<input :value="auth.user?.username" readonly></label><label>昵称<input v-model="displayName" required maxlength="40" autocomplete="nickname"></label><button class="primary-button" :disabled="busy">保存资料</button></form>
    </div>
    <div v-if="section === 'memories'" class="profile-section"><p class="dialog-note">你可以主动保存咨询背景或回答偏好。它们仅用于你自己的咨询，可随时删除；不作为法律依据。</p><form class="profile-form" @submit.prevent="addMemory"><label>记忆内容<textarea v-model="note.content" maxlength="1000" rows="3" placeholder="例如：请先列结论，再用简洁语言说明依据。"></textarea></label><label>适用范围<el-select v-model="note.agent_code"><el-option label="我的所有咨询" value="general"/><el-option v-for="a in agents?.filter(a => !a.extra)" :key="a.code" :label="a.name" :value="a.code"/></el-select></label><button class="primary-button" :disabled="busy || !note.content.trim()">保存记忆</button></form><div v-if="!memories.length" class="memory-empty">还没有保存记忆。</div><article v-for="m in memories" :key="m.id" class="memory-item"><p>{{ m.content }}</p><span>{{ m.agent_code === 'general' ? '所有咨询' : agents?.find(a => a.code === m.agent_code)?.name }}</span><button class="text-button danger" @click="removeMemory(m.id)">删除</button></article></div>
    <div v-if="section === 'security'" class="profile-section"><details class="security-extra"><summary>更改密码</summary><form class="profile-form" @submit.prevent="changePassword"><label>原密码<input v-model="password.old_password" type="password" autocomplete="current-password" required maxlength="128"></label><label>新密码<input v-model="password.new_password" type="password" autocomplete="new-password" required minlength="8" maxlength="128" placeholder="至少 8 位"></label><label>确认新密码<input v-model="password.confirm" type="password" autocomplete="new-password" required minlength="8" maxlength="128"></label><button class="primary-button" :disabled="busy">更新密码</button></form></details><details class="security-extra"><summary>密码找回码</summary><p class="dialog-note">没有绑定邮箱或手机号时，可凭账号和找回码重置密码。生成后仅显示一次，旧码立即失效；请妥善保存，不要分享。</p><form class="profile-form" @submit.prevent="generateRecovery"><label>当前密码<input v-model="recoveryPassword" type="password" autocomplete="current-password" required maxlength="128"></label><button class="secondary-button" :disabled="busy">生成新找回码</button></form><pre v-if="recoveryCode" class="recovery-code">{{ recoveryCode }}</pre><p v-if="recoveryCode" class="dialog-note">请现在复制保存，关闭个人中心后无法再次查看。</p></details>
      <details class="security-extra"><summary>注销账号</summary><p v-if="auth.user?.is_admin" class="dialog-note">管理员账号不支持自助注销，以保留公共资料的管理权限。</p><template v-else><p class="dialog-note">注销后无法恢复。请先等待正在处理的资料和咨询任务结束，再输入密码与当前账号名确认。</p><form class="profile-form" @submit.prevent="deleteAccount"><label>当前密码<input v-model="deletion.password" type="password" autocomplete="current-password" required maxlength="128"></label><label>输入账号名确认<input v-model="deletion.confirmation" required placeholder="请手动输入当前账号名" autocomplete="off" name="account-deletion-confirmation" maxlength="32"></label><button class="delete-account-button" :disabled="busy || deletion.confirmation !== auth.user?.username">注销我的账号</button></form></template></details><button class="logout-button" :disabled="busy" @click="logout">退出登录</button></div>
  </el-dialog>
  <AvatarCrop v-model="cropVisible" :file="avatarFile" @saved="auth.setUser($event); ElMessage.success('头像已更新')"/>
</template>
