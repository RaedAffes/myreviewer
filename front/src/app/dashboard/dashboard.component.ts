import { Component, computed, inject, signal } from '@angular/core';
import { ReportService } from '../services/report.service';
import { OpenPr, Report, ReportMeta } from '../report.model';
import {
  addReport,
  analyzingPr as analyzingPrSignal,
  gh as ghSignal,
  openPrs as prsSignal,
  prsError as prsErrorSignal,
  prsLoading as prsLoadingSignal,
  reports as reportsSignal,
} from '../store';

@Component({
  selector: 'app-dashboard',
  imports: [],
  templateUrl: './dashboard.html',
  styleUrl: './dashboard.css',
})
export class Dashboard {
  private readonly service = inject(ReportService);

  gh = ghSignal;
  reports = reportsSignal;
  openPrs = prsSignal;
  prsLoading = prsLoadingSignal;
  prsError = prsErrorSignal;
  analyzingPr = analyzingPrSignal;

  success = signal('');
  error = signal('');
  effort = signal('deep');

  totals = computed(() => {
    const list = this.reports();
    const prs = this.openPrs();
    return {
      open: prs.length,
      reviewed: list.length,
      errors: list.reduce((sum, r) => sum + (r.flags ?? 0), 0),
      ready: list.filter((r) => r.merge_readiness === 'ready').length,
    };
  });

  reviewedKeys = computed(() => {
    const keys = new Set<string>();
    for (const r of this.reports()) {
      if (r.pr_number != null && r.repo) {
        keys.add(`${r.repo}#${r.pr_number}`);
      }
    }
    return keys;
  });

  canConnect = computed(() => {
    const github = this.gh();
    if (!github) {
      return false;
    }
    const local = ['localhost', '127.0.0.1', '::1'].includes(window.location.hostname);
    return local ? !!github.oauth_dev_configured : !!github.oauth_configured;
  });

  connectGithub(): void {
    window.location.href = this.service.connectUrl();
  }

  analyzePr(pr: OpenPr): void {
    const resource = `${pr.repo}#${pr.pr_number}`;
    this.analyzingPr.set(resource);
    this.error.set('');
    this.success.set('');
    this.service
      .analyze(pr.owner, pr.repo, pr.pr_number, false, '', this.effort(), '', true)
      .subscribe({
        next: (result: { report_id: string; report: Report }) => {
          this.analyzingPr.set(null);
          const report = result.report;
          const mode = report.requested_mock
            ? ' (mock)'
            : ` (${report.model})`;
          this.success.set(
            `Analysis complete${mode} — verdict: ${report.verdict}`,
          );
          addReport({
            report_id: result.report_id,
            title: report.title,
            repo: report.repo,
            pr_number: report.pr_number,
            verdict: report.verdict,
            concerns: report.concerns.length,
            flags: report.flags.length,
            merge_readiness: report.merge_readiness,
            model: report.model,
            analyzed_at_iso: report.analyzed_at_iso,
          } satisfies ReportMeta);
        },
        error: (err) => {
          this.analyzingPr.set(null);
          this.error.set(apiError(err));
        },
      });
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

function apiError(err: unknown): string {
  const e = err as { error?: { detail?: string }; status?: number; message?: string };
  return e?.error?.detail ?? e?.message ?? String(err);
}