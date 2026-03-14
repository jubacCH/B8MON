'use client';

import { useToastStore } from '@/stores/toast';
import { motion, AnimatePresence } from 'framer-motion';
import { X, CheckCircle, AlertTriangle, AlertCircle, Info } from 'lucide-react';

const icons = {
  success: CheckCircle,
  error: AlertCircle,
  warning: AlertTriangle,
  info: Info,
};

const styles = {
  success: 'border-emerald-500/30 text-emerald-400',
  error: 'border-red-500/30 text-red-400',
  warning: 'border-amber-500/30 text-amber-400',
  info: 'border-sky-500/30 text-sky-400',
};

export function ToastContainer() {
  const { toasts, dismiss } = useToastStore();

  return (
    <div className="fixed bottom-4 right-4 z-[100] flex flex-col gap-2">
      <AnimatePresence>
        {toasts.map((t) => {
          const Icon = icons[t.type];
          return (
            <motion.div
              key={t.id}
              initial={{ opacity: 0, y: 20, scale: 0.95 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={{ opacity: 0, y: 20, scale: 0.95 }}
              className={`flex items-center gap-3 px-4 py-3 rounded-lg border backdrop-blur-xl shadow-xl ${styles[t.type]}`}
              style={{ background: 'var(--ng-surface)' }}
            >
              <Icon size={18} />
              <span className="text-sm text-slate-200">{t.message}</span>
              <button onClick={() => dismiss(t.id)} className="ml-2 text-slate-500 hover:text-slate-300">
                <X size={14} />
              </button>
            </motion.div>
          );
        })}
      </AnimatePresence>
    </div>
  );
}
