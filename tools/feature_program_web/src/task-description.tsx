import type { ReactNode } from 'react';

function Inline({ text }: { readonly text: string }) {
  const nodes: ReactNode[] = [];
  const pattern = /`([^`\n]+)`/gu;
  let start = 0;
  for (const match of text.matchAll(pattern)) {
    nodes.push(text.slice(start, match.index));
    nodes.push(<code key={match.index}>{match[1]}</code>);
    start = match.index + match[0].length;
  }
  nodes.push(text.slice(start));
  return <>{nodes}</>;
}

/** Render authored task prose without executing HTML or automatically creating links. */
export function TaskDescription({ text }: { readonly text: string }) {
  const lines = text.split(/\r?\n/u);
  const headingPattern = /^(#{1,6})\s+(.+)$/u;
  const bulletPattern = /^(\s*)([-*+] |(\d+)\. )(.+)$/u;
  const blocks: ReactNode[] = [];
  let index = 0;
  while (index < lines.length) {
    const line = lines[index] ?? '';
    if (!line.trim()) {
      index++;
      continue;
    }
    const heading = headingPattern.exec(line);
    if (heading) {
      blocks.push(
        <h4 key={index}>
          <Inline text={heading[2] ?? ''} />
        </h4>,
      );
      index++;
      continue;
    }
    const bullet = bulletPattern.exec(line);
    if (bullet) {
      const ordered = bullet[3] !== undefined;
      const start = index;
      const entries: ReactNode[] = [];
      while (index < lines.length) {
        const item = bulletPattern.exec(lines[index] ?? '');
        if (!item || (item[3] !== undefined) !== ordered) break;
        entries.push(
          <li key={index} value={ordered ? Number(item[3]) : undefined}>
            <Inline text={(item[1] ?? '') + (item[4] ?? '')} />
          </li>,
        );
        index++;
      }
      blocks.push(
        ordered ? (
          <ol key={start} start={Number(bullet[3])}>
            {entries}
          </ol>
        ) : (
          <ul key={start}>{entries}</ul>
        ),
      );
      continue;
    }
    const start = index;
    const paragraph: string[] = [];
    while (index < lines.length) {
      const next = lines[index] ?? '';
      if (!next.trim() || headingPattern.test(next) || bulletPattern.test(next)) break;
      paragraph.push(next);
      index++;
    }
    blocks.push(
      <p key={start}>
        <Inline text={paragraph.join('\n')} />
      </p>,
    );
  }
  return <div className="task-description">{blocks}</div>;
}
