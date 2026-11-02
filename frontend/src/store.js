import { defineStore } from 'pinia'

export const useAuth = defineStore('auth', {
  state: () => ({ user: null, ready: false }),
  actions: {
    setUser(user, expectedId) {
      if (expectedId && this.user?.id !== expectedId) return
      this.user = user
    },
    clear() { this.user = null },
  },
})
