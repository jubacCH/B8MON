import { describe, expect, it } from 'vitest';
import {
  backupName,
  bannerFor,
  isRestartGap,
  isTerminal,
  stepLabel,
  type UpdateRunState,
} from './updateSteps';

function state(overrides: Partial<UpdateRunState> = {}): UpdateRunState {
  return {
    run_id: '2026-06-10T14-02-11',
    status: 'running',
    step: 'build',
    steps: [
      { name: 'preflight', status: 'ok', detail: '12.4 GB free' },
      { name: 'backup', status: 'ok', detail: 'pre-update-2026-06-10T14-02-11.dump.gz (4.2 MB), pruned 5 kept' },
      { name: 'pull', status: 'ok', detail: '2174518 → a1b2c3d (3 commits)' },
      { name: 'build', status: 'running', detail: null },
      { name: 'migrate', status: 'pending', detail: null },
      { name: 'restart', status: 'pending', detail: null },
    ],
    started_at: '2026-06-10T14:02:11',
    finished_at: null,
    error: null,
    ...overrides,
  };
}

describe('stepLabel', () => {
  it('maps known step names to human labels', () => {
    expect(stepLabel('preflight')).toBe('Pre-flight checks');
    expect(stepLabel('migrate')).toBe('Apply migrations');
  });

  it('falls back to the raw name for unknown steps', () => {
    expect(stepLabel('mystery')).toBe('mystery');
  });
});

describe('isTerminal', () => {
  it('is false while running and for a missing state', () => {
    expect(isTerminal(state())).toBe(false);
    expect(isTerminal(null)).toBe(false);
  });

  it('is true once the run finished or failed', () => {
    expect(isTerminal(state({ status: 'done' }))).toBe(true);
    expect(isTerminal(state({ status: 'failed' }))).toBe(true);
  });

  it('treats idle as terminal so polling stops', () => {
    expect(isTerminal(state({ status: 'idle' }))).toBe(true);
  });
});

describe('isRestartGap', () => {
  it('is true when polling fails during the restart step', () => {
    expect(isRestartGap(state({ step: 'restart' }), true)).toBe(true);
  });

  it('is false when polling fails during an earlier step', () => {
    expect(isRestartGap(state({ step: 'build' }), true)).toBe(false);
  });

  it('is false while polling succeeds', () => {
    expect(isRestartGap(state({ step: 'restart' }), false)).toBe(false);
  });
});

describe('backupName', () => {
  it('extracts the dump filename from the backup step detail', () => {
    expect(backupName(state())).toBe('pre-update-2026-06-10T14-02-11.dump.gz');
  });

  it('is null when the backup step produced no file', () => {
    expect(backupName(state({
      steps: [{ name: 'backup', status: 'running', detail: null }],
    }))).toBeNull();
  });
});

describe('bannerFor', () => {
  it('reports the running step', () => {
    expect(bannerFor(state(), false)).toEqual({
      kind: 'running',
      title: 'Build images',
      detail: null,
    });
  });

  it('reports the restart gap instead of an error', () => {
    expect(bannerFor(state({ step: 'restart' }), true)?.kind).toBe('restarting');
  });

  it('reports success with the new commit', () => {
    const banner = bannerFor(state({ status: 'done', step: null }), false);
    expect(banner?.kind).toBe('success');
    expect(banner?.detail).toContain('a1b2c3d');
  });

  it('reports failure with the step error and the backup filename', () => {
    const banner = bannerFor(state({
      status: 'failed',
      step: 'migrate',
      steps: [
        { name: 'backup', status: 'ok', detail: 'pre-update-2026-06-10T14-02-11.dump.gz (4.2 MB)' },
        { name: 'migrate', status: 'failed', detail: null },
      ],
      error: 'migrate: alembic upgrade failed: DuplicateColumn',
    }), false);
    expect(banner?.kind).toBe('error');
    expect(banner?.title).toContain('Apply migrations');
    expect(banner?.detail).toContain('DuplicateColumn');
    expect(banner?.detail).toContain('pre-update-2026-06-10T14-02-11.dump.gz');
  });

  it('has no banner without a state', () => {
    expect(bannerFor(null, false)).toBeNull();
  });
});
