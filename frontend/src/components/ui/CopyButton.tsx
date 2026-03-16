'use client';

import { useState } from 'react';
import { Check, Copy } from 'lucide-react';

interface CopyButtonProps {
  text: string;
  className?: string;
  size?: number;
}

function fallbackCopy(text: string) {
  const ta = document.createElement('textarea');
  ta.value = text;
  ta.style.position = 'fixed';
  ta.style.opacity = '0';
  document.body.appendChild(ta);
  ta.select();
  document.execCommand('copy');
  document.body.removeChild(ta);
}

export function CopyButton({ text, className = '', size = 14 }: CopyButtonProps) {
  const [copied, setCopied] = useState(false);

  function handleCopy() {
    if (navigator.clipboard?.writeText) {
      navigator.clipboard.writeText(text).catch(() => fallbackCopy(text));
    } else {
      fallbackCopy(text);
    }
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  }

  return (
    <button
      onClick={(e) => { e.preventDefault(); e.stopPropagation(); handleCopy(); }}
      className={`inline-flex items-center justify-center p-1 rounded text-slate-500 hover:text-slate-300 hover:bg-white/[0.06] transition-colors ${className}`}
      title="Copy to clipboard"
    >
      {copied ? <Check size={size} className="text-emerald-400" /> : <Copy size={size} />}
    </button>
  );
}
