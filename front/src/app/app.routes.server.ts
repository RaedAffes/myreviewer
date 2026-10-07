import { RenderMode, ServerRoute } from '@angular/ssr';

// SEO: only the public landing page is pre-rendered into crawlable HTML.
// /dashboard, /reports and /reports/:id are authenticated app pages — they
// render client-side (index.html fallback via nginx try_files), so Google
// only sees the landing content and we never fetch the API during the build.
export const serverRoutes: ServerRoute[] = [
  {
    path: '',
    renderMode: RenderMode.Prerender
  },
  {
    path: 'dashboard',
    renderMode: RenderMode.Client
  },
  {
    path: 'reports',
    renderMode: RenderMode.Client
  },
  {
    path: 'reports/:id',
    renderMode: RenderMode.Client
  },
  {
    path: '**',
    renderMode: RenderMode.Client
  }
];