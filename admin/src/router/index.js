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
    path: '/public-chat',
    name: 'public-chat',
    component: () => import('../views/PublicChatView.vue'),
    meta: { public: true },
  },
  {
    path: '/',
    redirect: '/chat',
  },
  {
    path: '/dashboard',
    name: 'dashboard',
    component: () => import('../views/DashboardView.vue'),
  },
  {
    path: '/chat',
    name: 'chat',
    component: () => import('../views/ChatView.vue'),
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
    path: '/avatar',
    name: 'avatar',
    component: () => import('../views/AvatarView.vue'),
  },
  {
    path: '/devices',
    name: 'devices',
    component: () => import('../views/DevicesView.vue'),
  },
  {
    path: '/satellites',
    name: 'satellites',
    component: () => import('../views/SatellitesView.vue'),
  },
  {
    path: '/satellites/:id',
    name: 'satellite-detail',
    component: () => import('../views/SatelliteDetailView.vue'),
  },
  {
    path: '/plugins',
    name: 'plugins',
    component: () => import('../views/PluginsView.vue'),
  },
  {
    path: '/scheduling',
    name: 'scheduling',
    component: () => import('../views/SchedulingView.vue'),
  },
  {
    path: '/routines',
    name: 'routines',
    component: () => import('../views/RoutinesView.vue'),
  },
  {
    path: '/evolution',
    name: 'evolution',
    component: () => import('../views/EvolutionView.vue'),
  },
  {
    path: '/stories',
    name: 'stories',
    component: () => import('../views/StoriesView.vue'),
  },
  {
    path: '/system',
    name: 'system',
    component: () => import('../views/SystemView.vue'),
  },
  {
    path: '/learning',
    name: 'learning',
    component: () => import('../views/LearningView.vue'),
  },
  {
    path: '/proactive',
    name: 'proactive',
    component: () => import('../views/ProactiveView.vue'),
  },
  {
    path: '/media',
    name: 'media',
    component: () => import('../views/MediaView.vue'),
  },
  {
    path: '/intercom',
    name: 'intercom',
    component: () => import('../views/IntercomView.vue'),
  },
  {
    path: '/legacy',
    name: 'legacy',
    component: () => import('../views/LegacyView.vue'),
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
