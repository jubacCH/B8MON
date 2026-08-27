'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { AlertTriangle, Check, Circle, Loader2, RefreshCw } from 'lucide-react';
import { get } from '@/lib/api';
import {
  bannerFor,
  isRestartGap,
  isTerminal,
  stepLabel,
  type UpdateRunState,
  type UpdateStep,
} from '@/lib/updateSteps';

const POLL_INTERVAL_MS = 2000;

function StepIcon({ status }: { status: UpdateStep['status'] }) {
  if (status === 'ok') return <Check size={14} className="text-emerald-400 shrink-0" />;
  if (status === 'failed') return <AlertTriangle size={14} className="text-rose-400 shrink-0" />;
  if (status === 'running') return <Loader2 size={14} className="text-sky-400 shrink-0 animate-spin" />;
  return <Circle size={14} className="text-slate-600 shrink-0" />;
}

const BANNER_STYLES: Record<string, string> = {
  running: 'border-sky-500/30 bg-sky-500/10 text-sky-200',
  restarting: 'border-amber-500/30 bg-amber-500/10 text-amber-200',
  success: 'border-emerald-500/30 bg-emerald-500/10 text-emerald-200',
  error: 'border-rose-500/30 bg-rose-500/10 text-rose-200',
};

export function UpdateProgress({
  active,
  onFinished,
}: {
  active: boolean;
  onFinished: (state: UpdateRunState) => void;
}) {
  const [state, setState] = useState<UpdateRunState | null>(null);
  const [pollFailed, setPollFailed] = useState(false);
  const stoppedRef = useRef(false);

  const poll = useCallback(async (): Promise<boolean> => {
    try {
      const next = await get<UpdateRunState>('/api/update/status');
      if (!next || !Array.isArray(next.steps)) return false;
      setPollFailed(false);
      setState(next);
      if (isTerminal(next)) {
        onFinished(next);
        return true;
      }
    } catch {
      // Expected while the backend restarts — bannerFor renders that as a gap.
      setPollFailed(true);
    }
    return false;
  }, [onFinished]);

  useEffect(() => {
    if (!active) return;
    stoppedRef.current = false;

    const id = setInterval(async () => {
      if (stoppedRef.current) return;
      const done = await poll();
      if (done) {
        stoppedRef.current = true;
        clearInterval(id);
      }
    }, POLL_INTERVAL_MS);

    void poll();
    return () => {
      stoppedRef.current = true;
      clearInterval(id);
    };
  }, [active, poll]);

  if (!state || state.steps.length === 0) return null;

  const banner = bannerFor(state, pollFailed);
  const restarting = isRestartGap(state, pollFailed);

  return (
    <div className="mt-3 space-y-3">
      {banner && (
        <div className={`rounded border px-3 py-2 text-xs ${BANNER_STYLES[banner.kind]}`}>
          <div className="flex items-center gap-2 font-medium">
            {banner.kind === 'restarting' && <RefreshCw size={13} className="animate-spin" />}
            {banner.title}
          </div>
          {banner.detail && <p className="mt-1 opacity-80 break-words">{banner.detail}</p>}
        </div>
      )}

      <ol className="space-y-1.5">
        {state.steps.map((step) => (
          <li key={step.name} className="flex items-start gap-2 text-xs">
            <span className="mt-0.5">
              <StepIcon status={restarting && step.name === 'restart' ? 'running' : step.status} />
            </span>
            <span className="min-w-0">
              <span
                className={
                  step.status === 'pending'
                    ? 'text-slate-500'
                    : step.status === 'failed'
                      ? 'text-rose-300'
                      : 'text-slate-300'
                }
              >
                {stepLabel(step.name)}
              </span>
              {step.detail && (
                <span className="block text-slate-500 break-words">{step.detail}</span>
              )}
            </span>
          </li>
        ))}
      </ol>
    </div>
  );
}
