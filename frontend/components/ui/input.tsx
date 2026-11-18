// shadcn/ui Input, matching the existing search field.
import * as React from "react";
import { cn } from "@/lib/utils";

export const Input = React.forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(({ className, ...props }, ref) => (
  <input
    ref={ref}
    className={cn("rounded-lg border border-slate-300 bg-white px-3 py-2 text-xs text-slate-800 outline-none placeholder:text-slate-400 focus:border-indigo-400", className)}
    {...props}
  />
));
Input.displayName = "Input";
