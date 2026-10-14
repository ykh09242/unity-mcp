import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { TaskDescription } from './task-description';

describe('authored task description', () => {
  it('renders readable headings, paragraphs, lists and inline code in authored order', () => {
    const text =
      'Purpose paragraph.\nContinued purpose.\n\n## Method\n- Keep `src/retain.ts` intact\n- Preserve every criterion\n\n3. First numbered step\n7. Authored nonsequential step\n\nFinal limitation.';
    const { container } = render(<TaskDescription text={text} />);
    expect(screen.getByRole('heading', { name: 'Method' })).toBeTruthy();
    expect(container.querySelector('code')?.textContent).toBe('src/retain.ts');
    expect(container.querySelector('ul')?.children).toHaveLength(2);
    expect(
      Array.from(container.querySelectorAll('ol li'), (node) => node.getAttribute('value')),
    ).toEqual(['3', '7']);
    expect(container.textContent?.indexOf('Purpose paragraph.')).toBeLessThan(
      container.textContent?.indexOf('Method') ?? -1,
    );
    expect(container.textContent?.indexOf('Authored nonsequential step')).toBeLessThan(
      container.textContent?.indexOf('Final limitation.') ?? -1,
    );
    expect(container.querySelector('p')?.textContent).toBe(
      'Purpose paragraph.\nContinued purpose.',
    );
  });
  it('always advances and preserves literal empty or malformed markup markers', () => {
    const text = '# \n- \n1. \n`unclosed\n*literal*\n\n## Valid heading';
    const { container } = render(<TaskDescription text={text} />);
    expect(container.querySelector('p')?.textContent).toBe('# \n- \n1. \n`unclosed\n*literal*');
    expect(screen.getByRole('heading', { name: 'Valid heading' })).toBeTruthy();
    expect(container.querySelector('ul, ol, code')).toBeNull();
  });
  it('keeps HTML, source terminology and executable-looking URLs as escaped authored text', () => {
    const text =
      'Keep original source sha256=authored and <img src=x onerror=alert(1)> safely.\n\n- `javascript:alert(1)` is an authored example\n- https://example.com is plain text';
    const { container } = render(<TaskDescription text={text} />);
    expect(container.textContent).toContain('original source sha256=authored');
    expect(container.textContent).toContain('<img src=x onerror=alert(1)>');
    expect(container.querySelector('img, a, script')).toBeNull();
    expect(container.querySelector('code')?.textContent).toBe('javascript:alert(1)');
  });
});
