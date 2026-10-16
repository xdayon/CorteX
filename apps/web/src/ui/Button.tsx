import type { ButtonHTMLAttributes, ReactNode } from "react";
import { Icon, type IconName } from "./Icon";

type Variant = "primary" | "secondary" | "ghost" | "danger";
type Size = "sm" | "md" | "lg";

export function buttonClass(variant: Variant = "secondary", size: Size = "md", extra = "") {
  return ["btn", variant, size === "md" ? "" : size, extra].filter(Boolean).join(" ");
}

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; size?: Size; icon?: IconName; children?: ReactNode };

export function Button({ variant = "secondary", size = "md", icon, className, children, type = "button", ...rest }: ButtonProps) {
  return <button type={type} className={buttonClass(variant, size, className)} {...rest}>
    {icon && <Icon name={icon} small={size !== "lg"} />}{children}
  </button>;
}

type IconButtonProps = Omit<ButtonHTMLAttributes<HTMLButtonElement>, "aria-label"> & { "aria-label": string; icon: IconName; size?: "sm" | "md" };

export function IconButton({ icon, size = "md", className, type = "button", ...rest }: IconButtonProps) {
  return <button type={type} className={["ibtn", size === "sm" ? "sm" : "", className].filter(Boolean).join(" ")} {...rest}><Icon name={icon} /></button>;
}
