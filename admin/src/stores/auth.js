import { defineStore } from 'pinia';
import { api, login as apiLogin, logout as apiLogout, getToken, isAuthenticated } from '../api.js';

export const useAuthStore = defineStore('auth', {
  state: () => ({
    user: null,
    token: getToken(),
  }),

  getters: {
    isAuthenticated: () => isAuthenticated(),
  },

  actions: {
    async login(username, password) {
      const data = await apiLogin(username, password);
      this.token = data.token;
      this.user = data.user || { id: data.id, username: data.username };
    },

    logout() {
      this.user = null;
      this.token = null;
      apiLogout();
    },

    async fetchMe() {
      try {
        const data = await api.get('/admin/auth/me');
        this.user = data;
      } catch {
        this.user = null;
      }
    },
  },
});
