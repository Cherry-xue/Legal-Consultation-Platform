import { createApp } from 'vue'
import { createPinia } from 'pinia'
import { createRouter, createWebHashHistory } from 'vue-router'
import { ElSelect, ElOption, ElInput, ElInputNumber, ElTable, ElTableColumn, ElDialog, ElDrawer, ElLoading } from 'element-plus'
import 'element-plus/dist/index.css'
import App from './App.vue'
import './style.css'
import './account.css'
import './auth.css'
import './workspace.css'

const router = createRouter({ history: createWebHashHistory(), routes: [
  { path: '/', redirect: '/agent/contract' },
  { path: '/login', component: {} },
  { path: '/register', component: {} },
  { path: '/forgot-password', component: {} },
  { path: '/agent/:code', component: {} },
  { path: '/rag/chunks', redirect: to => ({ path: '/rag/knowledge', query: to.query }) },
  { path: '/rag/:page', component: {} },
  { path: '/:pathMatch(.*)*', redirect: '/agent/contract' },
] })
const app = createApp(App).use(createPinia()).use(router)
for (const component of [ElSelect, ElOption, ElInput, ElInputNumber, ElTable, ElTableColumn, ElDialog, ElDrawer]) app.component(component.name, component)
app.directive('loading', ElLoading.directive)
app.mount('#app')
