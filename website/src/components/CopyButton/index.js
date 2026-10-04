import React, { useState, useRef, useEffect, useTransition } from 'react';
import IconCopy from '@theme/Icon/Copy';
import IconSuccess from '@theme/Icon/Success';
import { copyText } from './copyText.mjs';
import styles from './styles.module.css';

/**
 * Minimal "copy to clipboard" button. Shows a 1.5s confirmation state
 * after a successful copy. Legacy clipboard access restores keyboard focus;
 * denied access leaves selectable text and an explicit failure state.
 */
export default function CopyButton({ text, label = 'text', className }) {
  const [status, setStatus] = useState('idle');
  const copied = status === 'copied';
  const [isPending, startTransition] = useTransition();
  // Timer ref so rapid repeated clicks don't stack pending resets and
  // an unmount mid-cooldown doesn't fire setCopied on a dead component.
  const timerRef = useRef(null);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, []);

  const onClick = () => startTransition(async () => {
    if (timerRef.current) clearTimeout(timerRef.current);
    setStatus('idle');
    try {
      await copyText(text, { navigator, document });
      if (!mountedRef.current) return;
      startTransition(() => setStatus('copied'));
      timerRef.current = setTimeout(() => setStatus('idle'), 1500);
    } catch {
      if (mountedRef.current) startTransition(() => setStatus('failed'));
    }
  });

  const feedback = isPending ? 'Copying' : copied ? 'Copied' : status === 'failed' ? 'Copy failed. Select the text to copy manually.' : `Copy ${label} to clipboard`;

  return (
    <button
      type="button"
      className={`${styles.copy} ${copied ? styles.copied : ''} ${status === 'failed' ? styles.failed : ''} ${className ?? ''}`.trim()}
      onClick={onClick}
      disabled={isPending}
      aria-busy={isPending}
      aria-label={feedback}
      title={feedback}
    >
      <span className={styles.icon} aria-hidden="true">
        {copied ? <IconSuccess width="16" height="16" /> : <IconCopy width="16" height="16" />}
      </span>
      <span className={styles.label} aria-hidden="true">{isPending ? 'Copying' : copied ? 'Copied' : status === 'failed' ? 'Failed' : 'Copy'}</span>
      <span className={styles.feedback} role="status" aria-live="polite">{feedback}</span>
    </button>
  );
}
