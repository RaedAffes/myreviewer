export interface Concern {
  number: number;
  title: string;
  rationale: string;
  files: string[];
  change_type: string;
  is_mixed: boolean;
  mixed_note: string;
}

export interface Flag {
  severity: 'critical' | 'warning' | 'info' | string;
  category: string;
  message: string;
  files: string[];
}

export interface ChangeLogEntry {
  number: number;
  area: string;
  old_code: string;
  new_code: string;
  impact: string;
  files: string[];
  introduced?: string;
}

export interface ReviewFinding {
  number: number;
  severity: 'critical' | 'important' | 'minor' | 'nit' | string;
  title: string;
  body: string;
  files: string[];
  evidence?: string;
}

export interface BugFinding {
  number: number;
  severity: 'critical' | 'important' | 'minor' | 'nit' | string;
  type: string;
  title: string;
  location: string;
  detail: string;
  fix: string;
  introduced_by_pr?: string;
  reachable?: string;
  evidence?: string;
}

export type RequirementStatus = 'satisfied' | 'partial' | 'unmet' | 'unverified' | string;

export interface RequirementCheck {
  number: number;
  requirement: string;
  status: RequirementStatus;
  evidence: string;
}

export interface Report {
  report_id: string;
  title: string;
  summary: string;
  stats: string;
  verdict: string;
  concerns: Concern[];
  flags: Flag[];
  change_log: ChangeLogEntry[];
  post_review: ReviewFinding[];
  post_review_verdict: string;
  bug_findings: BugFinding[];
  requirements_checks: RequirementCheck[];
  requirements_verdict: string;
  merge_readiness: string;
  effort: string;
  severity_counts?: {
    critical?: number;
    important?: number;
    minor?: number;
    nit?: number;
  };
  recommendation?: string;
  commit_messages: string[];
  model: string;
  plan_markdown: string;
  repo: string;
  pr_number: number | null;
  analyzed_at_iso: string;
  requested_mock?: boolean;
}

export interface ModelOption {
  id: string;
  label: string;
  verified: boolean;
  notes: string;
}

export interface ReportMeta {
  report_id: string;
  title: string;
  repo: string;
  pr_number: number | null;
  verdict: string;
  concerns: number;
  flags: number;
  merge_readiness: string;
  model: string;
  analyzed_at_iso: string;
}

export interface Health {
  status: string;
  model: string;
  default_model: string;
  models: ModelOption[];
  has_api_key: boolean;
  github_configured: boolean;
  reports: number;
}

export interface GithubAuth {
  connected: boolean;
  username: string;
  display_name: string;
  avatar_url: string;
  oauth_configured: boolean;
  oauth_dev_configured?: boolean;
}

export interface OpenPr {
  owner: string;
  repo: string;
  pr_number: number;
  title: string;
  repo_full: string;
  updated_at: string;
  additions: number | null;
  deletions: number | null;
  changed_files: number | null;
  draft: boolean;
}

export const SEVERITY_ORDER: Record<string, number> = {
  critical: 0,
  warning: 1,
  info: 2,
};

export const REQUIREMENT_STATUS_ORDER: Record<string, number> = {
  unmet: 0,
  partial: 1,
  unverified: 2,
  satisfied: 3,
};

export const READINESS_LABELS: Record<string, string | undefined> = {
  ready: 'ready',
  'fix-before-merge': 'fix before merge',
  rework: 'rework',
};