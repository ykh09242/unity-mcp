import type { AnchorHTMLAttributes, ButtonHTMLAttributes } from 'react';

// Lucide dab294f313897bacaa968b725b3259fa37c17bd1; exact SVG geometry, see LICENSE.lucide.txt.
const glyphs = {
  first: (
    <>
      <path d="m11 17-5-5 5-5" />
      <path d="m18 17-5-5 5-5" />
    </>
  ),
  previous: (
    <>
      <path d="m15 18-6-6 6-6" />
    </>
  ),
  next: (
    <>
      <path d="m9 18 6-6-6-6" />
    </>
  ),
  last: (
    <>
      <path d="m6 17 5-5-5-5" />
      <path d="m13 17 5-5-5-5" />
    </>
  ),
  refresh: (
    <>
      <path d="M21 12a9 9 0 1 1-9-9c2.52 0 4.93 1 6.74 2.74L21 8" />
      <path d="M21 3v5h-5" />
    </>
  ),
  close: (
    <>
      <path d="M18 6 6 18" />
      <path d="m6 6 12 12" />
    </>
  ),
  back: (
    <>
      <path d="m12 19-7-7 7-7" />
      <path d="M19 12H5" />
    </>
  ),
  expand: (
    <>
      <path d="M8 3H5a2 2 0 0 0-2 2v3" />
      <path d="M21 8V5a2 2 0 0 0-2-2h-3" />
      <path d="M3 16v3a2 2 0 0 0 2 2h3" />
      <path d="M16 21h3a2 2 0 0 0 2-2v-3" />
    </>
  ),
  clear: (
    <>
      <path d="M12.531 3H3a1 1 0 0 0-.742 1.67l7.225 7.989A2 2 0 0 1 10 14v6a1 1 0 0 0 .553.895l2 1A1 1 0 0 0 14 21v-7a2 2 0 0 1 .517-1.341l.427-.473" />
      <path d="m16.5 3.5 5 5" />
      <path d="m21.5 3.5-5 5" />
    </>
  ),
  search: (
    <>
      <path d="m21 21-4.34-4.34" />
      <circle cx="11" cy="11" r="8" />
    </>
  ),
} as const;
export type IconName = keyof typeof glyphs;

export function Icon({ name }: { readonly name: IconName }) {
  return (
    <svg
      className="utility-icon"
      viewBox="0 0 24 24"
      aria-hidden="true"
      focusable="false"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      {glyphs[name]}
    </svg>
  );
}

type IconProps = { readonly icon: IconName; readonly label: string };
export function IconButton({
  icon,
  label,
  className = '',
  ...props
}: IconProps & ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      {...props}
      type={props.type || 'button'}
      className={`icon-control ${className}`}
      aria-label={label}
      data-tooltip={label}
    >
      <Icon name={icon} />
    </button>
  );
}
export function IconLink({
  icon,
  label,
  className = '',
  ...props
}: IconProps & AnchorHTMLAttributes<HTMLAnchorElement>) {
  return (
    <a {...props} className={`icon-control ${className}`} aria-label={label} data-tooltip={label}>
      <Icon name={icon} />
    </a>
  );
}
