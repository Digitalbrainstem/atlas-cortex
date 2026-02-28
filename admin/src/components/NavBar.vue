<script setup>
import { ref, computed } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { useAuthStore } from '../stores/auth.js';

const auth = useAuthStore();
const route = useRoute();
const router = useRouter();
const collapsed = ref(false);
const mobileOpen = ref(false);

const navItems = [
  { name: 'Dashboard', route: 'dashboard', icon: '📊' },
  { name: 'Users', route: 'users', icon: '👥' },
  { name: 'Parental Controls', route: 'parental', icon: '👨‍👩‍👧' },
  { name: 'Safety', route: 'safety', icon: '🛡️' },
  { name: 'Voice', route: 'voice', icon: '🎙️' },
  { name: 'Devices', route: 'devices', icon: '📱' },
  { name: 'Evolution', route: 'evolution', icon: '🧬' },
  { name: 'System', route: 'system', icon: '⚙️' },
];

function isActive(routeName) {
  return route.name === routeName;
}

function navigate(routeName) {
  router.push({ name: routeName });
  mobileOpen.value = false;
}

function handleLogout() {
  auth.logout();
}
</script>

<template>
  <div class="mobile-toggle" @click="mobileOpen = !mobileOpen">
    <span>☰</span>
  </div>

  <div class="sidebar-overlay" :class="{ active: mobileOpen }" @click="mobileOpen = false"></div>

  <nav class="sidebar" :class="{ collapsed, 'mobile-open': mobileOpen }">
    <div class="sidebar-header">
      <div class="logo" @click="navigate('dashboard')">
        <span class="logo-icon">🧠</span>
        <span v-if="!collapsed" class="logo-text">Atlas Cortex</span>
      </div>
      <button class="collapse-btn" @click="collapsed = !collapsed">
        {{ collapsed ? '→' : '←' }}
      </button>
    </div>

    <div class="sidebar-nav">
      <a
        v-for="item in navItems"
        :key="item.route"
        class="nav-item"
        :class="{ active: isActive(item.route) }"
        @click="navigate(item.route)"
      >
        <span class="nav-icon">{{ item.icon }}</span>
        <span v-if="!collapsed" class="nav-label">{{ item.name }}</span>
      </a>
    </div>

    <div class="sidebar-footer">
      <a class="nav-item logout" @click="handleLogout">
        <span class="nav-icon">🚪</span>
        <span v-if="!collapsed" class="nav-label">Logout</span>
      </a>
    </div>
  </nav>
</template>

<style scoped>
.sidebar {
  display: flex;
  flex-direction: column;
  width: 240px;
  min-height: 100vh;
  background-color: #111827;
  border-right: 1px solid var(--border);
  transition: width 0.2s;
  overflow: hidden;
  flex-shrink: 0;
}

.sidebar.collapsed { width: 64px; }

.sidebar-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 1rem;
  border-bottom: 1px solid var(--border);
}

.logo {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  cursor: pointer;
}

.logo-icon { font-size: 1.5rem; }

.logo-text {
  font-size: 1rem;
  font-weight: 700;
  color: var(--text-primary);
  white-space: nowrap;
}

.collapse-btn {
  background: none;
  border: none;
  color: var(--text-muted);
  cursor: pointer;
  font-size: 0.85rem;
  padding: 0.25rem;
}

.collapse-btn:hover { color: var(--text-primary); }

.sidebar-nav {
  flex: 1;
  padding: 0.5rem;
  overflow-y: auto;
}

.nav-item {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  padding: 0.6rem 0.75rem;
  margin-bottom: 2px;
  border-radius: var(--radius);
  color: var(--text-secondary);
  cursor: pointer;
  transition: background-color 0.15s, color 0.15s;
  white-space: nowrap;
  text-decoration: none;
}

.nav-item:hover {
  background-color: rgba(255, 255, 255, 0.05);
  color: var(--text-primary);
}

.nav-item.active {
  background-color: rgba(59, 130, 246, 0.15);
  color: var(--accent);
}

.nav-icon { font-size: 1.1rem; flex-shrink: 0; }
.nav-label { font-size: 0.875rem; }

.sidebar-footer {
  padding: 0.5rem;
  border-top: 1px solid var(--border);
}

.nav-item.logout:hover {
  background-color: rgba(239, 68, 68, 0.1);
  color: var(--danger);
}

.mobile-toggle {
  display: none;
  position: fixed;
  top: 0.75rem;
  left: 0.75rem;
  z-index: 1001;
  width: 40px;
  height: 40px;
  align-items: center;
  justify-content: center;
  background: var(--bg-secondary);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  cursor: pointer;
  font-size: 1.2rem;
}

.sidebar-overlay {
  display: none;
}

@media (max-width: 768px) {
  .mobile-toggle { display: flex; }

  .sidebar {
    position: fixed;
    left: -260px;
    top: 0;
    z-index: 1000;
    transition: left 0.25s;
    box-shadow: var(--shadow-lg);
  }

  .sidebar.mobile-open { left: 0; }
  .sidebar.collapsed { width: 240px; }

  .collapse-btn { display: none; }

  .sidebar-overlay.active {
    display: block;
    position: fixed;
    inset: 0;
    background: rgba(0, 0, 0, 0.5);
    z-index: 999;
  }
}
</style>
