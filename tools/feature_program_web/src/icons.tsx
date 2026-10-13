import type { AnchorHTMLAttributes, ButtonHTMLAttributes } from 'react';

const paths = {
  first: 'M11 6 5 12l6 6M19 6l-6 6 6 6',
  previous: 'm15 6-6 6 6 6',
  next: 'm9 6 6 6-6 6',
  last: 'm5 6 6 6-6 6m8-12 6 6-6 6',
  refresh: 'M20 7v5h-5M4 17v-5h5M6.1 7a7 7 0 0 1 11.6-1L20 9M4 15l2.3 3A7 7 0 0 0 17.9 17',
  close: 'm6 6 12 12M18 6 6 18',
  back: 'm10 5-7 7 7 7M3 12h18',
  expand: 'M9 3H3v6m12-6h6v6M3 15v6h6m12-6v6h-6',
  clear: 'M4 5h16l-6 7v5l-4 3v-8L4 5m13 12 4 4m0-4-4 4',
  search: 'M21 21l-5-5M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0',
} as const;
export type IconName = keyof typeof paths;

export function Icon({ name }: { readonly name: IconName }) {
  return (
    <svg className="utility-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <path
        d={paths[name]}
        fill="none"
        stroke="currentColor"
        strokeWidth="1.7"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
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
