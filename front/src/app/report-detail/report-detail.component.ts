import { Component, inject, signal } from '@angular/core';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { ReportService } from '../services/report.service';
import { renderMarkdown } from '../services/markdown';
import {
  BugFinding,
  Concern,
  Flag,
  READINESS_LABELS,
  Report,
  RequirementCheck,
  ReviewFinding,
  SEVERITY_ORDER,
} from '../report.model';

const FINDING_ORDER: Record<string, number> = {
  critical: 0,
  important: 1,
  minor: 2,
  nit: 3,
};

const STATUS_ORDER: Record<string, number> = {
  unmet: 0,
  partial: 1,
  unverified: 2,
  satisfied: 3,
};

@Component({
  selector: 'app-report-detail',
  imports: [RouterLink],
  templateUrl: './report-detail.html',
  styleUrl: './report-detail.css',
})
export class ReportDetail {
  private readonly service = inject(ReportService);
  private readonly route = inject(ActivatedRoute);

  report = signal<Report | null>(null);
  error = signal('');
  downloading = signal<'' | 'html' | 'md' | 'pdf'>('');
  planOpen = signal(false);
  reviewOpen = signal(false);

  readonly readinessLabels = READINESS_LABELS;

  constructor() {
    const id = this.route.snapshot.paramMap.get('id');
    if (!id) {
      this.error.set('missing report id');
      return;
    }
    this.service.getReport(id).subscribe({
      next: (report) => this.report.set(report),
      error: (err) => this.error.set(String(err)),
    });
  }

  download(format: 'html' | 'md' | 'pdf'): void {
    const report = this.report();
    if (!report || this.downloading()) {
      return;
    }
    this.downloading.set(format);
    this.service.exportReport(report.report_id, format).subscribe({
      next: (blob) => {
        this.saveBlob(blob, `${report.report_id}.${format}`);
        this.downloading.set('');
      },
      error: (err) => {
        this.error.set(`download ${format.toUpperCase()} failed: ${err}`);
        this.downloading.set('');
      },
    });
  }

  private saveBlob(blob: Blob, filename: string): void {
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = filename;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(url);
  }

  sortFlags(flags: Flag[]): Flag[] {
    return [...flags].sort(
      (a, b) => (SEVERITY_ORDER[a.severity] ?? 9) - (SEVERITY_ORDER[b.severity] ?? 9),
    );
  }

  mixedConcerns(concerns: Concern[]): Concern[] {
    return concerns.filter((c) => c.is_mixed);
  }

  sortFindings(findings: ReviewFinding[]): ReviewFinding[] {
    return [...findings].sort(
      (a, b) => (FINDING_ORDER[a.severity] ?? 9) - (FINDING_ORDER[b.severity] ?? 9),
    );
  }

  sortBugFindings(findings: BugFinding[]): BugFinding[] {
    return [...findings].sort(
      (a, b) => (FINDING_ORDER[a.severity] ?? 9) - (FINDING_ORDER[b.severity] ?? 9),
    );
  }

  sortChecks(checks: RequirementCheck[]): RequirementCheck[] {
    return [...checks].sort(
      (a, b) => (STATUS_ORDER[a.status] ?? 9) - (STATUS_ORDER[b.status] ?? 9),
    );
  }

  togglePlan(): void {
    this.planOpen.update((value) => !value);
  }

  recommendationClass(recommendation: string): string {
    switch (recommendation) {
      case 'BLOCK':
        return 'block';
      case 'REQUEST CHANGES':
        return 'request-changes';
      case 'APPROVE WITH MINOR SUGGESTIONS':
        return 'minor-suggestions';
      default:
        return 'approve';
    }
  }

  toggleReview(): void {
    this.reviewOpen.update((value) => !value);
  }

  planHtml(report: Report): string {
    return renderMarkdown(report.plan_markdown ?? '');
  }
}