<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api'
import { useAuth } from '../store'

const route = useRoute(), router = useRouter(), auth = useAuth()
const registration = computed(() => route.path === '/register')
const resetting = computed(() => route.path === '/forgot-password')
const pendingUser = ref(null), recoveryCode = ref(''), success = ref('')
const form = reactive({ username: '', display_name: '', password: '', confirmation: '', recovery_code: '' })
const busy = ref(false), error = ref('')
watch(() => route.path, () => { form.password = ''; form.confirmation = ''; form.recovery_code = '' })
async function submit() {
  error.value = ''; success.value = ''
  if ((registration.value || resetting.value) && form.password !== form.confirmation) { error.value = '两次输入的密码不一致。'; return }
  busy.value = true
  try {
    if (resetting.value) {
      const result = await api('/auth/password/reset', { method: 'POST', body: JSON.stringify({ username: form.username, recovery_code: form.recovery_code, new_password: form.password }) })
      form.password = ''; form.confirmation = ''; form.recovery_code = ''; success.value = result.message
      router.replace('/login'); return
    }
    const response = await api(registration.value ? '/auth/register' : '/auth/login', {
      method: 'POST', body: JSON.stringify({ username: form.username, password: form.password, display_name: form.display_name }),
    })
    const { recovery_code, ...user } = response
    form.password = ''; form.confirmation = ''
    if (registration.value && recovery_code) { pendingUser.value = user; recoveryCode.value = recovery_code; return }
    auth.setUser(user); router.replace('/agent/contract')
  } catch (e) { error.value = e.message }
  finally { busy.value = false }
}
function enterWorkspace() { auth.setUser(pendingUser.value); recoveryCode.value = ''; pendingUser.value = null; router.replace('/agent/contract') }
</script>

<template>
  <main class="auth-layout" :class="{ 'is-registration': registration || resetting }">
    <section class="auth-story"><div class="auth-brand"><span class="brand-mark">序</span>法序</div><div class="auth-story-main"><span class="auth-eyebrow">YOUR LEGAL KNOWLEDGE COMPANION</span><h1>把问题说清，<br>让依据看得见。</h1><p>从生活中的疑问出发，<br>找到有出处的法律信息与下一步方向。</p><div class="auth-seal">据</div></div><div class="auth-story-footer">专业领域咨询 · 原文出处可查 · 个人会话留存</div></section>
    <section class="auth-entry"><div class="auth-card"><div class="auth-card-heading"><span class="eyebrow">WELCOME TO FAXU</span><h2>{{ resetting ? '找回密码' : registration ? '创建你的账号' : '欢迎回来' }}</h2><p class="auth-subtitle">{{ resetting ? '使用保存的找回码重置密码。' : registration ? '保存你的咨询、资料与个人偏好。' : '登录后，继续你的法律咨询。' }}</p></div>
      <form @submit.prevent="submit">
        <div class="auth-fields">
        <label>账号<input v-model="form.username" autocomplete="username" required minlength="3" maxlength="32" pattern="[A-Za-z0-9_]{3,32}" placeholder="3–32 位字母、数字或下划线"></label>
        <label v-if="registration">昵称<input v-model="form.display_name" autocomplete="nickname" maxlength="40" placeholder="你希望我们如何称呼你"></label>
        <label v-if="resetting">找回码<input v-model="form.recovery_code" required maxlength="128" autocomplete="off" placeholder="注册或账号安全中保存的找回码"></label><label>{{ resetting ? '新密码' : '密码' }}<input v-model="form.password" type="password" :autocomplete="registration || resetting ? 'new-password' : 'current-password'" required :minlength="registration || resetting ? 8 : 1" maxlength="128" :placeholder="registration || resetting ? '至少 8 位密码' : '请输入密码'"></label>
        <label v-if="registration || resetting">确认密码<input v-model="form.confirmation" type="password" autocomplete="new-password" required minlength="8" maxlength="128" placeholder="再次输入密码"></label>
        </div>
        <div class="auth-form-status" aria-live="polite"><p v-if="error" class="auth-error" role="alert">{{ error }}</p><p v-else-if="success" class="auth-success" role="status">{{ success }}</p></div>
        <button class="primary-button auth-submit" :disabled="busy">{{ busy ? '正在处理…' : resetting ? '重置密码 →' : registration ? '注册并开始咨询 →' : '登录 →' }}</button>
      </form>
      <p v-if="!registration && !resetting" class="auth-forgot"><RouterLink to="/forgot-password" @click="error = ''; success = ''">忘记密码？</RouterLink></p><p v-if="resetting" class="auth-switch"><RouterLink to="/login" @click="error = ''">返回登录</RouterLink></p><p v-else class="auth-switch">{{ registration ? '已有账号？' : '还没有账号？' }} <RouterLink :to="registration ? '/login' : '/register'" @click="error = ''">{{ registration ? '返回登录' : '创建账号' }}</RouterLink></p>
      <p class="auth-note">法序提供法律信息检索与辅助。重大事项请咨询执业律师。</p>
    </div></section>
  </main>
  <el-dialog :model-value="!!recoveryCode" title="保存账号找回码" width="460px" :show-close="false" :close-on-click-modal="false" :close-on-press-escape="false"><p class="dialog-note">找回码仅显示一次，请复制保存到安全位置。知道账号和找回码即可重置密码，不要分享给他人。使用后作废，可登录后重新生成。</p><pre class="recovery-code">{{ recoveryCode }}</pre><button class="primary-button" @click="enterWorkspace">已保存，进入平台</button></el-dialog>
</template>
