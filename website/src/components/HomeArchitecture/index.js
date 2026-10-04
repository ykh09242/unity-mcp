import React from 'react';
import styles from './styles.module.css';

export default function HomeArchitecture() {
  return (
    <section className={styles.section}>
      <div className={styles.inner}>
        <div className={styles.header}>
          <span className={styles.eyebrow}>// ARCHITECTURE</span>
          <h2 className={styles.title}>How it works</h2>
          <p className={styles.lede}>
            Your MCP client connects to the Python server over stdio or HTTP.
            The server routes each request to the selected Unity Editor
            through the C# plugin.
          </p>
        </div>

        <div
          className={styles.diagram}
          role="img"
          aria-label="MCP for Unity architecture diagram: MCP client connects to the Python server over stdio or HTTP, which talks to the Unity Editor plugin over WebSocket."
        >
          <Stage
            kicker="LAYER 01"
            title="MCP Client"
            sub="Claude · Codex · VS Code · Cursor · local LLMs"
            body="Speaks the Model Context Protocol. Discovers tools and resources, sends prompts, renders results."
            tone="filled"
          />
          <Edge label="stdio  ·  HTTP /mcp" />
          <Stage
            kicker="LAYER 02"
            title="Python Server"
            sub="FastMCP + WebSocket hub"
            body="Registers Python tools and routes request-scoped Editor targets. Optional session defaults require a stateful legacy connection."
            tone="filled"
          />
          <Edge label="WebSocket  ·  /hub/plugin" />
          <Stage
            kicker="LAYER 03"
            title="Unity Editor Plugin"
            sub="C# package · MCPForUnity"
            body="Receives commands on the Unity main thread. Executes via Unity Editor APIs. Returns structured payloads."
            tone="outlined"
          />
        </div>

        <div className={styles.legend}>
          <span className={styles.legendItem}>
            <span className={`${styles.legendSwatch} ${styles.swatchFilled}`} />
            Client and server processes
          </span>
          <span className={styles.legendItem}>
            <span className={`${styles.legendSwatch} ${styles.swatchOutlined}`} />
            Runs inside the Unity Editor process
          </span>
        </div>
      </div>
    </section>
  );
}

function Stage({ kicker, title, sub, body, tone }) {
  return (
    <div className={`${styles.stage} ${tone === 'outlined' ? styles.stageOutlined : styles.stageFilled}`}>
      <span className={styles.stageKicker}>{kicker}</span>
      <h3 className={styles.stageTitle}>{title}</h3>
      <p className={styles.stageSub}>{sub}</p>
      <p className={styles.stageBody}>{body}</p>
    </div>
  );
}

function Edge({ label }) {
  return (
    <div className={styles.edge} aria-hidden="true">
      <div className={styles.edgeLineHorizontal} />
      <div className={styles.edgeLineVertical} />
      <span className={styles.edgeLabel}>{label}</span>
    </div>
  );
}
