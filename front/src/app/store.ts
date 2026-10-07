import { signal } from '@angular/core';
import { ReportService } from './services/report.service';
import { GithubAuth, OpenPr, ReportMeta } from './report.model';

export const gh = signal<GithubAuth | null>(null);
export const reports = signal<ReportMeta[]>([]);
export const openPrs = signal<OpenPr[]>([]);
export const reportsLoading = signal(false);
export const prsLoading = signal(false);
export const reportsError = signal('');
export const prsError = signal('');
export const analyzingPr = signal<string | null>(null);

let booted = false;

export function loadAll(service: ReportService): void {
  if (booted) {
    return;
  }
  booted = true;

  reportsLoading.set(true);
  service.listReports().subscribe({
    next: (list) => {
      reports.set(list);
      reportsLoading.set(false);
    },
    error: () => {
      reportsLoading.set(false);
    },
  });

  service.authStatus().subscribe({
    next: (status) => {
      gh.set(status);
      if (status.connected) {
        loadPrs(service);
      }
    },
    error: () => {
      gh.set(null);
    },
  });
}

export function loadPrs(service: ReportService): void {
  prsLoading.set(true);
  prsError.set('');
  service.listPrs().subscribe({
    next: (prs) => {
      openPrs.set(prs);
      prsLoading.set(false);
    },
    error: () => {
      prsLoading.set(false);
    },
  });
}

export function addReport(meta: ReportMeta): void {
  reports.update((list) => [
    meta,
    ...list.filter((r) => r.report_id !== meta.report_id),
  ]);
}

export function resetState(): void {
  gh.set(null);
  reports.set([]);
  openPrs.set([]);
  reportsLoading.set(false);
  prsLoading.set(false);
  reportsError.set('');
  prsError.set('');
  analyzingPr.set(null);
  booted = false;
}