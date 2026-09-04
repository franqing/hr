import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  { path: '/', redirect: '/search' },
  { path: '/settings', name: 'settings', component: () => import('./views/Settings.vue') },
  { path: '/search', name: 'search', component: () => import('./views/SearchConsole.vue') },
  { path: '/profiles', name: 'profiles', component: () => import('./views/ProfileEditor.vue') },
  { path: '/runs', name: 'runs', component: () => import('./views/RunHistory.vue') },
  { path: '/candidates/:id', name: 'candidate', component: () => import('./views/CandidateDetail.vue') },
  { path: '/invites', name: 'invites', component: () => import('./views/InvitePanel.vue') },
]

export default createRouter({
  history: createWebHistory(),
  routes,
})
