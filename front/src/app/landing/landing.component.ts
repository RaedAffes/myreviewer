import { Component, inject, OnInit, signal } from '@angular/core';
import { ActivatedRoute, Router } from '@angular/router';

@Component({
  selector: 'app-landing',
  imports: [],
  templateUrl: './landing.html',
  styleUrl: './landing.css',
})
export class Landing implements OnInit {
  private readonly router = inject(Router);
  private readonly route = inject(ActivatedRoute);

  menuOpen = signal(false);
  frameReady = signal(false);
  ghError = signal(false);

  ngOnInit(): void {
    const gh = this.route.snapshot.queryParamMap.get('gh');
    if (gh === 'connected') {
      this.router.navigate(['/dashboard'], { replaceUrl: true });
    } else if (gh === 'error') {
      this.ghError.set(true);
    }
  }

  toggleMenu(): void {
    this.menuOpen.update((value) => !value);
  }

  closeMenu(): void {
    this.menuOpen.set(false);
  }

  connect(): void {
    window.location.href = '/api/auth/login';
  }
}