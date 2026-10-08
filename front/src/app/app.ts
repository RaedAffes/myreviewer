import { Component, inject, OnInit, signal } from '@angular/core';
import { NavigationEnd, Router, RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { ReportService } from './services/report.service';
import { SeoService } from './services/seo.service';
import { gh as ghSignal, loadAll, resetState } from './store';

function isAppUrl(path: string): boolean {
  const clean = path.split('?')[0].split('#')[0].replace(/\/+$/, '');
  return clean === '/dashboard' || clean === '/reports' || clean.startsWith('/reports/');
}

function canUseBrowser(): boolean {
  return typeof window !== 'undefined' && typeof localStorage !== 'undefined';
}

@Component({
  selector: 'app-root',
  imports: [RouterOutlet, RouterLink, RouterLinkActive],
  templateUrl: './app.html',
  styleUrl: './app.css'
})
export class App implements OnInit {
  private readonly router = inject(Router);
  private readonly service = inject(ReportService);
  private readonly seo = inject(SeoService);
  sidebarClosed = signal(false);
  isLanding = signal(!isAppUrl(canUseBrowser() ? window.location.pathname : '/'));
  gh = ghSignal;

  constructor() {
    if (canUseBrowser()) {
      this.applyDark(localStorage.getItem('mode') === 'dark');
      this.sidebarClosed.set(localStorage.getItem('status') === 'close');
    }
    this.router.events.subscribe((event) => {
      if (event instanceof NavigationEnd) {
        this.isLanding.set(!isAppUrl(event.urlAfterRedirects));
        this.applySeo(event.urlAfterRedirects);
      }
    });
  }

  ngOnInit(): void {
    if (canUseBrowser()) {
      loadAll(this.service);
    }
  }

  private applySeo(url: string): void {
    const clean = url.split('?')[0].split('#')[0];
    if (clean === '/' || clean === '') {
      this.seo.apply({
        title: 'MyReviewer — AI PR Assistant for GitHub Pull Requests',
        description:
          'AI PR assistant for GitHub. MyReviewer reviews pull requests against your full codebase with automated, line-level code review to catch issues before you merge.',
        canonical: '/',
      });
      return;
    }
    if (clean.startsWith('/reports/')) {
      this.seo.apply({
        title: 'Code Review Report — MyReviewer',
        noindex: true,
      });
      return;
    }
    if (clean.startsWith('/reports')) {
      this.seo.apply({
        title: 'Reports — MyReviewer',
        noindex: true,
      });
      return;
    }
    if (clean.startsWith('/dashboard')) {
      this.seo.apply({
        title: 'Dashboard — MyReviewer',
        noindex: true,
      });
      return;
    }
    this.seo.apply({
      title: 'MyReviewer — AI PR Assistant for GitHub Pull Requests',
      canonical: '/',
    });
  }

  toggleSidebar(): void {
    this.sidebarClosed.update((value) => !value);
    localStorage.setItem('status', this.sidebarClosed() ? 'close' : 'open');
  }

  toggleMode(): void {
    const dark = !document.body.classList.contains('dark');
    this.applyDark(dark);
    localStorage.setItem('mode', dark ? 'dark' : 'light');
  }

  signIn(): void {
    window.location.href = '/api/auth/login';
  }

  sidebarLogout(): void {
    this.service.logout().subscribe({
      next: () => {
        resetState();
        window.location.href = '/';
      },
      error: () => {
        resetState();
        window.location.href = '/';
      },
    });
  }

  private applyDark(dark: boolean): void {
    if (canUseBrowser()) {
      document.body.classList.toggle('dark', dark);
    }
  }
}