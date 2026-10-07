import { Component, Injector, inject, runInInjectionContext, signal } from '@angular/core';
import { ReportMeta } from '../report.model';
import { ReportService } from '../services/report.service';
import {
  reports as reportsSignal,
  reportsError as reportsErrorSignal,
  reportsLoading as reportsLoadingSignal,
} from '../store';

@Component({
  selector: 'app-reports',
  imports: [],
  templateUrl: './reports.html',
  styleUrl: './reports.css',
})
export class Reports {
  reports = reportsSignal;
  loading = reportsLoadingSignal;
  error = reportsErrorSignal;
  private readonly service = inject(ReportService);
  private readonly injector = inject(Injector);
  prStates = signal<Record<string, { state: string; merged: boolean; merged_at: string }>>({});

  constructor() {
    runInInjectionContext(this.injector, () => {
      this.service.reportPrStates().subscribe({
        next: (states) => this.prStates.set(states),
        error: () => this.prStates.set({}),
      });
    });
  }

  mergeState(report: ReportMeta): { label: string; css: string } {
    if (!report.pr_number) {
      return { label: report.merge_readiness === 'ready' ? 'YES' : 'NO', css: 'ok' };
    }
    const key = `${report.repo}#${report.pr_number}`;
    const state = this.prStates()[key];
    if (state) {
      if (state.merged || state.state === 'closed') {
        return { label: 'MERGED', css: 'merged' };
      }
    }
    return { label: report.merge_readiness === 'ready' ? 'YES' : 'NO', css: 'ok' };
  }

  prUrl(report: ReportMeta): string {
    if (!report.pr_number) {
      return '';
    }
    return `https://github.com/${report.repo}/pull/${report.pr_number}`;
  }

  formatDate(iso: string): string {
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) {
      return iso;
    }
    return date.toLocaleString(undefined, {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
  }
}