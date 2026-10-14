import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { TaskDescription, taskDescriptionParts } from './task-description';

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

describe('explicit completion-criteria boundary', () => {
  it.each(['Acceptance criteria:', '완료 기준:', '## Acceptance criteria'])(
    'separates only the standalone %s section without rewriting authored lists',
    (heading) => {
      const description = '- Keep `src/retain.ts`\n- Preserve authored method\n\n';
      const criteria = '\n3. First exact criterion\n7. Second exact criterion\n';
      const parts = taskDescriptionParts(description + heading + '\n' + criteria);
      expect(parts.description).toBe(description.slice(0, -1));
      expect(parts.criteria).toBe(criteria);
      expect(parts.criteriaItems).toEqual(['First exact criterion', 'Second exact criterion']);
    },
  );
  it.each([
    'Purpose mentions Acceptance criteria: in a sentence.\n\nUnique method.',
    'Inline `Acceptance criteria:` remains authored content.',
    '```text\nAcceptance criteria:\n1. Literal code criterion\n```\n\nUseful method.',
    '~~~text\n완료 기준:\n1. Literal code criterion\n~~~',
    '```text\nAcceptance criteria:\n1. Unclosed fenced literal',
    'Keep unique prose.\n\nAcceptance criteria:',
  ])(
    'keeps missing, inline, fenced and empty-boundary text entirely in the description: %s',
    (text) => {
      expect(taskDescriptionParts(text)).toEqual({
        description: text,
        criteria: undefined,
        criteriaItems: undefined,
      });
    },
  );
  it('ignores a fenced lookalike before the real outside boundary and retains free-prose criteria', () => {
    const description = '```text\nAcceptance criteria:\n- Literal record\n```\n\nUseful method.\n';
    const parts = taskDescriptionParts(
      description + '완료 기준:\n\nKeep this authored paragraph.\n\n3. Keep this ordered item.',
    );
    expect(parts.description).toBe(description.slice(0, -1));
    expect(parts.criteria).toBe('\nKeep this authored paragraph.\n\n3. Keep this ordered item.');
    expect(parts.criteriaItems).toBeUndefined();
  });
});
