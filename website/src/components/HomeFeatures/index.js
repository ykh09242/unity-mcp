import React from 'react';
import Link from '@docusaurus/Link';
import useDocusaurusContext from '@docusaurus/useDocusaurusContext';
import styles from './styles.module.css';

function getFeatures(toolCount, toolGroupCount) {
  return [
    {
      n: '01',
      kicker: 'CONTROL',
      title: 'Talk to the Editor.',
      body: `Drive scenes, GameObjects, scripts, assets, prefabs, and materials with natural language. ${toolCount} tools across ${toolGroupCount} groups expose Unity's editing surface to your MCP client.`,
      href: '/reference/tools',
      cta: 'Browse tools',
    },
    {
      n: '02',
      kicker: 'ROUTING',
      title: 'Multiple Editors, explicit targets.',
      body: 'Target an Editor on each request, including from sessionless clients. Optional active-instance defaults are available only on stateful legacy connections.',
      href: '/guides/multi-instance',
      cta: 'How routing works',
    },
    {
      n: '03',
      kicker: 'TRANSPORT',
      title: 'HTTP or stdio. Your call.',
      body: 'HTTP for multi-agent, remote-hosted, and shared workflows. Stdio for single-client setups like Claude Desktop. Auto-detected and auto-configured.',
      href: '/architecture/transports',
      cta: 'HTTP vs stdio',
    },
    {
      n: '04',
      kicker: 'VISIBILITY',
      title: 'Your tools, on demand.',
      body: 'Local Editor toggles and server defaults control discovery; legacy sessions can also select groups. Tool visibility does not grant Unity consent for high-impact operations.',
      href: '/guides/tool-groups',
      cta: 'Tool groups',
    },
    {
      n: '05',
      kicker: 'DOCS',
      title: 'Source-generated API reference.',
      body: 'Tool reference pages are generated from registered Python tools. Authored guides and preserved examples are maintained alongside them.',
      href: '/contributing/docs',
      cta: 'Docs workflow',
    },
    {
      n: '06',
      kicker: 'EXTEND',
      title: 'Plug in custom tools.',
      body: 'Write a C# attribute, register a new domain. The MCP client picks it up automatically. Project-scoped or global. Full reflection-based dispatch.',
      href: '/guides/custom-tools',
      cta: 'Custom tools',
    },
  ];
}

export default function HomeFeatures() {
  const { siteConfig } = useDocusaurusContext();
  const toolCount = siteConfig.customFields?.toolCount ?? 47;
  const toolGroupCount = siteConfig.customFields?.toolGroupCount ?? 10;
  const features = getFeatures(toolCount, toolGroupCount);

  return (
    <section className={styles.section}>
      <div className={styles.inner}>
        <div className={styles.header}>
          <span className={styles.eyebrow}>// CAPABILITIES</span>
          <h2 className={styles.title}>What you can do</h2>
        </div>
        <div className={styles.grid}>
          {features.map((f) => (
            <Link className={styles.card} to={f.href} key={f.n}>
              <div className={styles.cardHead}>
                <span className={styles.cardNum}>{f.n}</span>
                <span className={styles.cardKicker}>{f.kicker}</span>
              </div>
              <h3 className={styles.cardTitle}>{f.title}</h3>
              <p className={styles.cardBody}>{f.body}</p>
              <span className={styles.cardCta}>
                {f.cta}
                <span className={styles.cardArrow} aria-hidden="true">→</span>
              </span>
            </Link>
          ))}
        </div>
      </div>
    </section>
  );
}
