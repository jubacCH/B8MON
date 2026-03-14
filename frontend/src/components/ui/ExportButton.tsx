'use client';

import { useState, useRef, useEffect } from 'react';
import { Download } from 'lucide-react';
import { Button } from './Button';

interface ExportButtonProps {
  data: Record<string, unknown>[];
  filename: string;
  columns?: { key: string; label: string }[];
}

function toCSV(data: Record<string, unknown>[], columns?: { key: string; label: string }[]): string {
  if (data.length === 0) return '';
  const cols = columns ?? Object.keys(data[0]).map((k) => ({ key: k, label: k }));
  const header = cols.map((c) => `"${c.label}"`).join(',');
  const rows = data.map((row) =>
    cols.map((c) => {
      const val = row[c.key];
      if (val === null || val === undefined) return '';
      const str = String(val).replace(/"/g, '""');
      return `"${str}"`;
    }).join(',')
  );
  return [header, ...rows].join('\n');
}

function download(content: string, filename: string, type: string) {
  const blob = new Blob([content], { type });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

export function ExportButton({ data, filename, columns }: ExportButtonProps) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [open]);

  function exportCSV() {
    download(toCSV(data, columns), `${filename}.csv`, 'text/csv');
    setOpen(false);
  }

  function exportJSON() {
    download(JSON.stringify(data, null, 2), `${filename}.json`, 'application/json');
    setOpen(false);
  }

  return (
    <div className="relative" ref={ref}>
      <Button size="sm" variant="ghost" onClick={() => setOpen(!open)}>
        <Download size={16} />
        Export
      </Button>
      {open && (
        <div className="absolute right-0 mt-1 z-50 w-32 rounded-md border shadow-xl overflow-hidden" style={{ background: 'var(--ng-surface)', borderColor: 'var(--ng-glass-border)' }}>
          <button
            onClick={exportCSV}
            className="w-full text-left px-3 py-2 text-xs text-slate-300 hover:bg-white/[0.06] transition-colors"
          >
            CSV
          </button>
          <button
            onClick={exportJSON}
            className="w-full text-left px-3 py-2 text-xs text-slate-300 hover:bg-white/[0.06] transition-colors"
          >
            JSON
          </button>
        </div>
      )}
    </div>
  );
}
