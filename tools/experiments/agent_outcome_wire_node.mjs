import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import { once } from "node:events";
import { mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { createServer } from "node:net";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { createInterface } from "node:readline";
import { setTimeout as delay } from "node:timers/promises";

const protocol = "2025-06-18";
const client = { name: "unity-mcp-node-wire-control", version: "1" };
const target = "WireControl@6d7b412c";
const surface = "mcpforunity://capabilities";

function rpcClient(child) {
  const pending = new Map();
  const serverRequests = [];
  let sequence = 0;

  function send(message) {
    child.stdin.write(JSON.stringify({ jsonrpc: "2.0", ...message }) + "\n");
  }

  function rejectPending(error) {
    for (const { reject } of pending.values()) reject(error);
    pending.clear();
  }

  const lines = createInterface({ input: child.stdout, crlfDelay: Infinity });
  lines.on("line", (line) => {
    try {
      const message = JSON.parse(line);
      if (typeof message.method === "string" && "id" in message) {
        serverRequests.push(message.method);
        send({ id: message.id, error: { code: -32601, message: "Optional client method unavailable" } });
      } else if (pending.has(message.id)) {
        const { fulfill, reject } = pending.get(message.id);
        pending.delete(message.id);
        if (message.error) reject(new Error(`RPC error ${message.error.code}`));
        else fulfill(message.result);
      }
    } catch (error) {
      rejectPending(error);
    }
  });
  child.on("error", rejectPending);
  child.stdin.on("error", rejectPending);
  child.once("close", (code) => rejectPending(new Error(`MCP subprocess closed with code ${code}`)));

  async function request(method, params = {}) {
    const id = ++sequence;
    let timeout;
    try {
      return await new Promise((fulfill, reject) => {
        timeout = setTimeout(() => reject(new Error(`Timed out waiting for ${method}`)), 15_000);
        pending.set(id, { fulfill, reject });
        send({ id, method, params });
      });
    } finally {
      clearTimeout(timeout);
      pending.delete(id);
    }
  }

  return { request, notify: (method) => send({ method }), serverRequests };
}

async function exercise(clientSession) {
  const { request, notify, serverRequests } = clientSession;
  const initialized = await request("initialize", {
    protocolVersion: protocol,
    clientInfo: client,
    capabilities: {},
  });
  assert.equal(initialized.protocolVersion, protocol);
  notify("notifications/initialized");
  const list = await request("tools/list");
  const management = list.tools.find((tool) => tool.name === "manage_tools");
  assert(management.inputSchema.properties.action.enum.includes("prepare"));
  assert(list.tools.some((tool) => tool.name === "search_unity_tools"));

  async function toolCall(tool, args) {
    const response = await request("tools/call", { name: tool, arguments: args });
    assert(!response.isError);
    return response.structuredContent;
  }

  const draft = {
    selection: "agent",
    query: "Read this fixture's advertised project capabilities.",
    route_surfaces: "resources",
    limit: 50,
    unity_instance: target,
  };
  const preparation = await toolCall("manage_tools", {
    action: "prepare",
    tool: "search_unity_tools",
    unity_instance: target,
    arguments: draft,
  });
  assert.equal(preparation.scope, "input_schema");
  assert.equal(preparation.ready, true);
  const prepared = preparation.prepared_call;
  assert.deepEqual(prepared, { tool: "search_unity_tools", arguments: { ...draft, limit: 20 } });
  const directory = await toolCall(prepared.tool, prepared.arguments);
  assert(directory.families.some((family) => family.family === surface));
  assert.deepEqual(directory.open_family, prepared);
  const operation = await toolCall(prepared.tool, { ...directory.open_family.arguments, family: surface });
  const read = operation.operations[0].read_call;
  assert.deepEqual(read, { method: "resources/read", params: { uri: surface, _meta: { unity_instance: target } } });
  const resource = await request(read.method, read.params);
  assert.equal(resource.contents[0].uri, surface);
  assert.equal(JSON.parse(resource.contents[0].text).success, true);
  assert.deepEqual(serverRequests, []);
  return {
    client,
    runtime: process.version,
    transport: "stdio",
    protocol,
    target: "isolated_reloading_status_fixture",
    prepared_limit: prepared.arguments.limit,
    scoped_resource: surface,
    target_selector_preserved: true,
    server_initiated_requests: serverRequests,
    model_processes_launched: 0,
  };
}

async function main() {
  const [python, inputManifest, output] = process.argv.slice(2);
  assert(python && inputManifest && output, "Usage: node agent_outcome_wire_node.mjs PYTHON SOURCE_MANIFEST OUTPUT");
  const manifest = JSON.parse(await readFile(inputManifest, "utf8"));
  const mainFile = join(dirname(resolve(inputManifest)), "pinned-product", "Server", "src", "main.py");
  assert.equal(createHash("sha256").update(await readFile(mainFile)).digest("hex"), manifest.files["Server/src/main.py"]);
  const scratchRoot = resolve(tmpdir());
  const scratch = await mkdtemp(join(scratchRoot, "unity-mcp-node-wire-"));
  const peer = createServer((socket) => socket.destroy());
  let child;
  try {
    peer.listen(0, "127.0.0.1");
    await once(peer, "listening");
    const status = join(scratch, "status");
    await mkdir(status);
    await writeFile(join(status, "unity-mcp-status-6d7b412c.json"), JSON.stringify({
      project_path: join(scratch, "WireControl", "Assets"),
      unity_port: peer.address().port,
      reloading: true,
    }));
    child = spawn(resolve(python), [mainFile, "--transport", "stdio", "--tool-profile", "full"], {
      cwd: scratch,
      env: {
        SystemRoot: process.env.SystemRoot,
        PATH: process.env.PATH,
        USERPROFILE: process.env.USERPROFILE,
        TEMP: process.env.TEMP,
        TMP: process.env.TMP,
        PYTHONUTF8: "1",
        PYTHONNOUSERSITE: "1",
        PYTHONDONTWRITEBYTECODE: "1",
        UNITY_MCP_STATUS_DIR: status,
        UNITY_MCP_LOG_DIR: join(scratch, "logs"),
        UNITY_MCP_SKIP_STARTUP_CONNECT: "1",
        UNITY_MCP_TELEMETRY_ENABLED: "0",
      },
      stdio: ["pipe", "pipe", "ignore"],
      windowsHide: true,
    });
    const result = await exercise(rpcClient(child));
    await writeFile(output, JSON.stringify({
      ...result,
      source_revision: manifest.base_git_revision,
      source_tree_sha256: manifest.tree_content_sha256,
    }, null, 2) + "\n");
    process.stdout.write("Node stdlib completed initialize, tools/list, prepare, scoped tools/call and resources/read.\n");
  } finally {
    if (child && child.exitCode === null && child.signalCode === null) {
      const closed = once(child, "close");
      child.stdin.end();
      const stopped = await Promise.race([closed.then(() => true), delay(2_000, false)]);
      if (!stopped) {
        child.kill();
        await closed;
      }
    }
    peer.close();
    assert.equal(dirname(scratch), scratchRoot);
    await rm(scratch, { recursive: true, force: true, maxRetries: 4, retryDelay: 50 });
  }
}

await main();
