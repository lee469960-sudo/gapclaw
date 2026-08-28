import { createRouter, createWebHistory } from 'vue-router'
import Login from './views/Login.vue'
import Layout from './views/Layout.vue'
import { fetchShell, getShell, getLoginExpiry, clearShell } from './session'

const routes = [
  { path: '/login', name: 'login', component: Login, meta: { public: true } },
  {
    path: '/agents/:id/chat',
    name: 'agent-chat',
    component: () => import('./views/AgentChat.vue'),
    meta: { route: '/agents', standalone: true },
  },
  {
    path: '/groups/:id/chat',
    name: 'group-chat',
    component: () => import('./views/GroupChat.vue'),
    meta: { route: '/groups', standalone: true },
  },
  {
    path: '/',
    component: Layout,
    redirect: '/agents',
    children: [
      { path: 'groups', name: 'groups', component: () => import('./views/Groups.vue'), meta: { route: '/groups' } },
      { path: 'agents', name: 'agents', component: () => import('./views/Agents.vue'), meta: { route: '/agents', keepAlive: true } },
      { path: 'code-projects', name: 'code-projects', component: () => import('./views/CodeProjects.vue'), meta: { route: '/code-projects' } },
      { path: 'sandboxes', name: 'sandboxes', component: () => import('./views/Sandboxes.vue'), meta: { route: '/sandboxes', keepAlive: true } },
      { path: 'skills', name: 'skills', component: () => import('./views/Skills.vue'), meta: { route: '/skills' } },
      { path: 'mcps', name: 'mcps', component: () => import('./views/Mcps.vue'), meta: { route: '/mcps' } },
      { path: 'llms', name: 'llms', component: () => import('./views/Llms.vue'), meta: { route: '/llms' } },
      { path: 'files', name: 'files', component: () => import('./views/Files.vue'), meta: { route: '/files' } },
      { path: 'rag', name: 'rag', component: () => import('./views/Rag.vue'), meta: { route: '/rag' } },
      { path: 'httpmcp', name: 'httpmcp', component: () => import('./views/HttpMcp.vue'), meta: { route: '/httpmcp' } },
      { path: 'channels', name: 'channels', component: () => import('./views/Channels.vue'), meta: { route: '/channels' } },
      { path: 'sql', name: 'sql', component: () => import('./views/Sql.vue'), meta: { route: '/sql' } },
      { path: 'terminals', name: 'terminals', component: () => import('./views/Terminals.vue'), meta: { route: '/terminals' } },
      { path: 'docker', name: 'docker', component: () => import('./views/Docker.vue'), meta: { route: '/docker' } },
      { path: 'monitor', name: 'monitor', component: () => import('./views/Monitor.vue'), meta: { route: '/monitor' } },
      { path: 'site', name: 'site', component: () => import('./views/Site.vue'), meta: { route: '/site' } },
      { path: 'me', name: 'me', component: () => import('./views/Me.vue'), meta: { route: '/me' } },
      { path: 'users', name: 'users', component: () => import('./views/Users.vue'), meta: { route: '/users' } },
      { path: 'roles', name: 'roles', component: () => import('./views/Roles.vue'), meta: { route: '/roles' } },
    ],
  },
]

const router = createRouter({ history: createWebHistory(), routes })

router.beforeEach(async (to, from, next) => {
  if (to.meta.public) return next()

  const expiry = getLoginExpiry()
  if (expiry != null && Date.now() >= expiry) {
    clearShell()
    return next('/login')
  }

  let data = getShell()
  if (!data) {
    try {
      data = await fetchShell()
    } catch {
      return next('/login')
    }
  }

  const roles = data.roles || []
  if (roles.includes('master') || roles.includes('admin')) return next()
  const allowed = (data.menus || []).map((m) => m.route)
  const check = to.meta.route || to.path
  if (allowed.includes(check) || check === '/me') return next()
  next('/me')
})

export default router
