import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";

import { cn } from "../../lib/utils";

const badgeVariants = cva("km-badge", {
  variants: {
    variant: {
      default: "km-badge-default",
      secondary: "km-badge-secondary",
      success: "km-badge-success",
      warning: "km-badge-warning",
      destructive: "km-badge-destructive",
      outline: "km-badge-outline",
    },
  },
  defaultVariants: {
    variant: "default",
  },
});

export interface BadgeProps
  extends React.HTMLAttributes<HTMLSpanElement>,
    VariantProps<typeof badgeVariants> {}

export function Badge({ className, variant, ...props }: BadgeProps) {
  return <span className={cn(badgeVariants({ variant }), className)} {...props} />;
}

