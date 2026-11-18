// shadcn/ui Badge with variants for governance statuses.
import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const badgeVariants = cva("inline-flex items-center whitespace-nowrap rounded-full border px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide", {
  variants: {
    tone: {
      neutral: "border-slate-200 bg-slate-50 text-slate-600",
      success: "border-emerald-200 bg-emerald-50 text-emerald-700",
      info: "border-indigo-200 bg-indigo-50 text-indigo-700",
      warning: "border-amber-200 bg-amber-50 text-amber-800",
      danger: "border-rose-200 bg-rose-50 text-rose-700",
    },
  },
  defaultVariants: { tone: "neutral" },
});

export interface BadgeProps extends React.HTMLAttributes<HTMLSpanElement>, VariantProps<typeof badgeVariants> {}

export function Badge({ className, tone, ...props }: BadgeProps) {
  return <span className={cn(badgeVariants({ tone }), className)} {...props} />;
}
