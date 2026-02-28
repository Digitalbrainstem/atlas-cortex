import { createRouter, createWebHashHistory } from 'vue-router';
import { isAuthenticated } from '../api.js';

const routes = [
  {
    path: '/login',
    name: 'login',
    component: () => import('../views/LoginView.vue'),
    meta: { public: true },
  },
  {
    path: '/',
    name: 'dashboard',
    component: () => import('../views/DashboardView.vue'),
  },
  {
    path: '/users',
    name: 'users',
    component: () => import('../views/UsersView.vue'),
  },
  {
    path: '/users/:id',
    name: 'user-detail',
    component: () => import('../views/UserDetailView.vue'),
  },
  {
    path: '/parental',
    name: 'parental',
    component: () => import('../views/ParentalView.vue'),
  },
  {
    path: '/safety',
    name: 'safety',
    component: () => import('../views/SafetyView.vue'),
  },
  {
    path: '/voice',
    name: 'voice',
    component: () => import('../views/VoiceView.vue'),
  },
  {
    path: '/devices',
    name: 'devices',
    component: () => import('../views/DevicesView.vue'),
  },
  {
    path: '/evolution',
    name: 'evolution',
    component: () => import('../views/EvolutionView.vue'),
  },
  {
    path: '/system',
    name: 'system',
    component: () => import('../views/SystemView.vue'),
  },
];

const router = createRouter({
  history: createWebHashHistory('/admin/'),
  routes,
});

router.beforeEach((to, from, next) => {
  if (to.meta.public || isAuthenticated()) {
    next();
  } else {
    next({ name: 'login' });
  }
});

export default router;
