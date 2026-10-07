import { Routes } from '@angular/router';
import { Dashboard } from './dashboard/dashboard.component';
import { Landing } from './landing/landing.component';
import { ReportDetail } from './report-detail/report-detail.component';
import { Reports } from './reports/reports.component';

export const routes: Routes = [
  { path: '', component: Landing },
  { path: 'dashboard', component: Dashboard },
  { path: 'reports', component: Reports },
  { path: 'reports/:id', component: ReportDetail },
  { path: '**', redirectTo: '' },
];