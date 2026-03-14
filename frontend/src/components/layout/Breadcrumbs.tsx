'use client';

import Link from 'next/link';
import { ChevronRight } from 'lucide-react';

export interface Crumb {
  label: string;
  href?: string;
}

interface BreadcrumbsProps {
  items: Crumb[];
}

export function Breadcrumbs({ items }: BreadcrumbsProps) {
  return (
    <nav className="flex items-center gap-1.5 text-xs text-slate-500 mb-4">
      {items.map((item, i) => {
        const isLast = i === items.length - 1;
        return (
          <span key={i} className="flex items-center gap-1.5">
            {i > 0 && <ChevronRight size={12} className="text-slate-600" />}
            {item.href && !isLast ? (
              <Link href={item.href} className="hover:text-slate-300 transition-colors">
                {item.label}
              </Link>
            ) : (
              <span className={isLast ? 'text-slate-300' : ''}>{item.label}</span>
            )}
          </span>
        );
      })}
    </nav>
  );
}
