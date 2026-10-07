import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';
import {
  GithubAuth,
  Health,
  ModelOption,
  OpenPr,
  Report,
  ReportMeta,
} from '../report.model';

@Injectable({ providedIn: 'root' })
export class ReportService {
  private readonly http = inject(HttpClient);
  private readonly base = '/api';

  health(): Observable<Health> {
    return this.http.get<Health>(`${this.base}/health`);
  }

  models(): Observable<ModelOption[]> {
    return this.http.get<ModelOption[]>(`${this.base}/models`);
  }

  analyze(
    owner: string,
    repo: string,
    prNumber: number,
    mock: boolean,
    model: string,
    effort: string,
    requirements: string,
    postComment: boolean = false,
  ): Observable<{ report_id: string; analyzed_at_iso: string; report: Report }> {
    return this.http.post<{ report_id: string; analyzed_at_iso: string; report: Report }>(
      `${this.base}/analyze`,
      { owner, repo, pr_number: prNumber, mock, model, effort, requirements, post_comment: postComment },
    );
  }

  listReports(): Observable<ReportMeta[]> {
    return this.http.get<ReportMeta[]>(`${this.base}/reports`);
  }

  reportPrStates(): Observable<Record<string, { state: string; merged: boolean; merged_at: string }>> {
    return this.http.get<Record<string, { state: string; merged: boolean; merged_at: string }>>(
      `${this.base}/reports/states`,
    );
  }

  getReport(reportId: string): Observable<Report> {
    return this.http.get<Report>(`${this.base}/reports/${encodeURIComponent(reportId)}`);
  }

  exportReport(reportId: string, format: 'html' | 'md' | 'pdf'): Observable<Blob> {
    return this.http.get(
      `${this.base}/reports/${encodeURIComponent(reportId)}/export?format=${format}`,
      { responseType: 'blob' },
    );
  }

  deleteReport(reportId: string): Observable<{ deleted: string }> {
    return this.http.delete<{ deleted: string }>(
      `${this.base}/reports/${encodeURIComponent(reportId)}`,
    );
  }

  authStatus(): Observable<GithubAuth> {
    return this.http.get<GithubAuth>(`${this.base}/auth/status`);
  }

  logout(): Observable<{ connected: boolean }> {
    return this.http.post<{ connected: boolean }>(`${this.base}/auth/logout`, {});
  }

  listPrs(): Observable<OpenPr[]> {
    return this.http.get<OpenPr[]>(`${this.base}/prs`);
  }

  connectUrl(): string {
    return `${this.base}/auth/login`;
  }
}