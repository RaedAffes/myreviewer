import { Component, inject, OnInit, signal } from '@angular/core';
import { NavigationEnd, Router, RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { ReportService } from './services/report.service';
import { gh as ghSignal, loadAll, resetState } from './store';

function isAppUrl(path: string): boolean {
  const clean = path.split('?')[0].split('#')[0].replace(/\/+$/, '');
  return clean === '/dashboard' || clean === '/reports' || clean.startsWith('/reports/');
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
  sidebarClosed = signal(false);
  isLanding = signal(!isAppUrl(window.location.pathname));
  gh = ghSignal;

  constructor() {
    this.applyDark(localStorage.getItem('mode') === 'dark');
    this.sidebarClosed.set(localStorage.getItem('status') === 'close');
    this.router.events.subscribe((event) => {
      if (event instanceof NavigationEnd) {
        this.isLanding.set(!isAppUrl(event.urlAfterRedirects));
      }
    });
  }

  ngOnInit(): void {
    loadAll(this.service);
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
    document.body.classList.toggle('dark', dark);
  }
}