import { defineStore } from 'pinia'
import { ref } from 'vue'
import { authApi } from '@/api/auth.ts'
import { getToken, setToken, clearToken } from '@/api/client.ts'

export const useAuthStore = defineStore('auth', () => {
  const authEnabled = ref(false)
  const token = ref(getToken())
  const checking = ref(false)
  const initialized = ref(false)

  async function checkAuth(): Promise<boolean> {
    checking.value = true
    try {
      const res = await authApi.checkAuth()
      authEnabled.value = res.auth_enabled
      if (!res.auth_enabled) {
        return true
      }

      if (!token.value) {
        return false
      }

      try {
        await authApi.verifyToken(token.value)
        return true
      } catch {
        clearToken()
        token.value = ''
        return false
      }
    } catch {
      return false
    } finally {
      checking.value = false
      initialized.value = true
    }
  }

  async function login(newToken: string): Promise<boolean> {
    const trimmed = newToken.trim()
    if (!trimmed) return false

    try {
      await authApi.verifyToken(trimmed)
      setToken(trimmed)
      token.value = trimmed
      return true
    } catch {
      return false
    }
  }

  function logout() {
    clearToken()
    token.value = ''
  }

  function updateToken(newToken: string) {
    const trimmed = newToken.trim()
    setToken(trimmed)
    token.value = trimmed
  }

  return {
    authEnabled,
    token,
    checking,
    initialized,
    checkAuth,
    login,
    logout,
    updateToken,
  }
})
