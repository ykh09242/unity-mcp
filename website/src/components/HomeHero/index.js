import React from 'react';
import Link from '@docusaurus/Link';
import useDocusaurusContext from '@docusaurus/useDocusaurusContext';
import useBaseUrl from '@docusaurus/useBaseUrl';
import CopyButton from '@site/src/components/CopyButton';
import styles from './styles.module.css';

export default function HomeHero() {
  const { siteConfig } = useDocusaurusContext();
  const { forkVersion, releaseUpmUrl, upstreamBaselineVersion } = siteConfig.customFields;
  const imageBaseUrl = useBaseUrl('/img/');

  return (
    <header className={styles.hero}>
      <div className={styles.gridBackdrop} aria-hidden="true" />

      <div className={styles.inner}>
        <div className={styles.statusBar}>
          <span className={styles.statusDot} aria-hidden="true" />
          <span className={styles.statusKey}>RELEASE</span>
          <span className={styles.statusValue}>v{forkVersion}</span>
        </div>

        <h1 className={styles.headline}>
          Run the Unity Editor<br />
          with your <em>AI&nbsp;assistant</em>.
        </h1>

        <p className={styles.tagline}>
          Unity MCP (ykh09242) bridges AI assistants — Claude, Codex, VS Code,
          local LLMs, and more — with the Unity Editor via the Model
          Context Protocol. Manage assets, control scenes, edit scripts,
          run tests, automate workflows.{' '}
          <small>Upstream baseline: {upstreamBaselineVersion}.</small>
        </p>

        <div className={styles.ctaRow}>
          <Link className={styles.ctaPrimary} to="/getting-started/install">
            Get started
            <span className={styles.ctaArrow} aria-hidden="true">↗</span>
          </Link>
          <Link className={styles.ctaSecondary} to="/reference/tools">
            Browse the reference
            <span className={styles.linkArrow} aria-hidden="true">→</span>
          </Link>
        </div>

        <div className={styles.install}>
          <div className={styles.installHeader}>
            <span className={styles.installLabel}>// INSTALL · Unity Package Manager</span>
            <span className={styles.installHint}>
              Window → Package Manager → + → Add package from git URL
            </span>
          </div>

          <div className={styles.installLine}>
            <span className={`${styles.installChannel} ${styles.installChannelBeta}`}>RELEASE</span>
            <code className={styles.installUrl}>{releaseUpmUrl}</code>
            <CopyButton text={releaseUpmUrl} label="fork release URL" className={styles.installCopy} />
          </div>
        </div>

        <figure className={styles.demo}>
          <figcaption className={styles.demoCaption}>
            <span className={styles.demoTag}>// LIVE</span>
            <span>an MCP client building a scene, end-to-end</span>
          </figcaption>
          <div className={styles.demoFrame}>
            <video
              autoPlay
              loop
              muted
              playsInline
              preload="metadata"
              poster={`${imageBaseUrl}logo.png`}
              aria-label="An LLM building a Unity scene through MCP for Unity"
              width="640"
              height="416"
            >
              <source src={`${imageBaseUrl}building_scene.webm`} type="video/webm" />
              <source src={`${imageBaseUrl}building_scene.mp4`} type="video/mp4" />
              {/* GIF fallback retained for ancient browsers */}
              <img
                src={`${imageBaseUrl}building_scene.gif`}
                alt="An LLM building a Unity scene through MCP for Unity"
                width="640"
                height="416"
                loading="lazy"
              />
            </video>
          </div>
        </figure>
      </div>
    </header>
  );
}
