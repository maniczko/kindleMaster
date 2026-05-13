import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";

import { cn } from "../../lib/utils";

const buttonVariants = cva("km-button", {
  variants: {
    variant: {
      default: "km-button-primary",
      secondary: "km-button-secondary",
      outline: "km-button-outline",
      ghost: "km-button-ghost",
      destructive: "km-button-destructive",
    },
    size: {
      default: "km-button-md",
      sm: "km-button-sm",
      icon: "km-button-icon",
    },
  },
  defaultVariants: {
    variant: "default",
    size: "default",
  },
});

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, ...props }, ref) => (
    <button ref={ref} className={cn(buttonVariants({ variant, size }), className)} {...props} />
  ),
);

Button.displayName = "Button";

