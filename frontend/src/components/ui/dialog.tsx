import * as React from "react";
import { X } from "lucide-react";

import { Button } from "./button";
import { cn } from "../../lib/utils";

interface DialogContextValue {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

const DialogContext = React.createContext<DialogContextValue | null>(null);

export interface DialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  children: React.ReactNode;
}

export function Dialog({ open, onOpenChange, children }: DialogProps) {
  React.useEffect(() => {
    if (!open) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onOpenChange(false);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [open, onOpenChange]);

  return <DialogContext.Provider value={{ open, onOpenChange }}>{children}</DialogContext.Provider>;
}

export function DialogTrigger({ children }: { children: React.ReactElement<{ onClick?: React.MouseEventHandler }> }) {
  const context = React.useContext(DialogContext);
  return React.cloneElement(children, {
    onClick: (event: React.MouseEvent) => {
      children.props.onClick?.(event);
      context?.onOpenChange(true);
    },
  });
}

export function DialogContent({ className, children, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  const context = React.useContext(DialogContext);
  if (!context?.open) return null;
  return (
    <div className="km-dialog-overlay" role="presentation" onMouseDown={() => context.onOpenChange(false)}>
      <div
        className={cn("km-dialog-content", className)}
        role="dialog"
        aria-modal="true"
        onMouseDown={(event) => event.stopPropagation()}
        {...props}
      >
        <Button
          className="km-dialog-close"
          type="button"
          variant="ghost"
          size="icon"
          aria-label="Zamknij"
          onClick={() => context.onOpenChange(false)}
        >
          <X aria-hidden="true" />
        </Button>
        {children}
      </div>
    </div>
  );
}

export function DialogHeader({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("km-dialog-header", className)} {...props} />;
}

export function DialogTitle({ className, ...props }: React.HTMLAttributes<HTMLHeadingElement>) {
  return <h2 className={cn("km-dialog-title", className)} {...props} />;
}

export function DialogDescription({ className, ...props }: React.HTMLAttributes<HTMLParagraphElement>) {
  return <p className={cn("km-dialog-description", className)} {...props} />;
}

