/** View-model for the update orchestrator run state (System → Update). */

export type StepStatus = 'pending' | 'running' | 'ok' | 'failed';
export type RunStatus = 'idle' | 'running' | 'done' | 'failed' | 'unavailable';

export interface UpdateStep {
  name: string;
  status: StepStatus;
  detail: string | null;
}

export interface UpdateRunState {
  run_id: string | null;
  status: RunStatus;
  step: string | null;
  steps: UpdateStep[];
  started_at: string | null;
  finished_at: string | null;
  error: string | null;
}

export const STEP_LABELS: Record<string, string> = {
  preflight: 'Pre-flight checks',
  backup: 'Database backup',
  pull: 'Fetch new code',
  build: 'Build images',
  migrate: 'Apply migrations',
  restart: 'Restart services',
};

export function stepLabel(name: string): string {
  return STEP_LABELS[name] ?? name;
}

/** A run is terminal once nothing more will change without a new /apply. */
export function isTerminal(state: UpdateRunState | null): boolean {
  if (!state) return false;
  return state.status === 'done' || state.status === 'failed' || state.status === 'idle';
}

/**
 * During the restart step the backend itself goes away, so a failed poll is
 * expected rather than an error worth showing.
 */
export function isRestartGap(state: UpdateRunState | null, pollFailed: boolean): boolean {
  return Boolean(pollFailed && state && state.step === 'restart');
}

function stepByName(state: UpdateRunState | null, name: string): UpdateStep | null {
  return state?.steps.find((s) => s.name === name) ?? null;
}

/** The dump written by the backup step, if it produced one. */
export function backupName(state: UpdateRunState | null): string | null {
  const detail = stepByName(state, 'backup')?.detail;
  if (!detail) return null;
  const match = detail.match(/pre-update-\S+?\.dump\.gz/);
  return match ? match[0] : null;
}

export interface Banner {
  kind: 'running' | 'restarting' | 'success' | 'error';
  title: string;
  detail: string | null;
}

export function bannerFor(state: UpdateRunState | null, pollFailed: boolean): Banner | null {
  if (!state) return null;

  if (isRestartGap(state, pollFailed)) {
    return {
      kind: 'restarting',
      title: 'Backend restarting…',
      detail: 'The new containers are coming up. This page will reconnect automatically.',
    };
  }

  if (state.status === 'failed') {
    const failed = state.steps.find((s) => s.status === 'failed');
    const backup = backupName(state);
    const parts = [state.error ?? 'Update failed'];
    if (backup) parts.push(`Database backup: ${backup}`);
    return {
      kind: 'error',
      title: `${stepLabel(failed?.name ?? state.step ?? 'update')} failed`,
      detail: parts.join(' — '),
    };
  }

  if (state.status === 'done') {
    return {
      kind: 'success',
      title: 'Update complete',
      detail: stepByName(state, 'pull')?.detail ?? null,
    };
  }

  if (state.status === 'running' && state.step) {
    return {
      kind: 'running',
      title: stepLabel(state.step),
      detail: stepByName(state, state.step)?.detail ?? null,
    };
  }

  return null;
}
