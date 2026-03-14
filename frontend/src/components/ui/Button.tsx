'use client';

import { forwardRef, type ButtonHTMLAttributes } from 'react';
import { cn } from '@/lib/utils';

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: 'primary' | 'secondary' | 'ghost' | 'danger';
  size?: 'sm' | 'md' | 'lg';
}

const variants = {
  primary: 'bg-sky-500 text-white border-sky-500 hover:bg-sky-600 hover:border-sky-600 shadow-sm shadow-sky-500/25',
  secondary: 'bg-white/[0.08] text-slate-200 border-white/[0.12] hover:bg-white/[0.14] hover:text-white',
  ghost: 'bg-transparent text-slate-300 border-transparent hover:bg-white/[0.08] hover:text-white',
  danger: 'bg-red-500 text-white border-red-500 hover:bg-red-600 hover:border-red-600 shadow-sm shadow-red-500/25',
};

const sizes = {
  sm: 'px-3 py-1.5 text-xs',
  md: 'px-4 py-2 text-sm',
  lg: 'px-6 py-2.5 text-sm',
};

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant = 'primary', size = 'md', disabled, children, ...props }, ref) => (
    <button
      ref={ref}
      className={cn(
        'inline-flex items-center justify-center gap-2 rounded-lg border font-medium transition-all',
        'focus:outline-none focus:ring-2 focus:ring-sky-500/40 focus:ring-offset-1 focus:ring-offset-[#0B0E14]',
        'disabled:opacity-40 disabled:pointer-events-none disabled:shadow-none',
        variants[variant],
        sizes[size],
        className,
      )}
      disabled={disabled}
      {...props}
    >
      {children}
    </button>
  ),
);
Button.displayName = 'Button';
