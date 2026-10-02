# MCP for Unity Server (Docker Image)

[![MCP](https://badge.mcpx.dev?status=on 'MCP Enabled')](https://modelcontextprotocol.io/introduction)
[![License](https://img.shields.io/badge/License-MIT-red.svg 'MIT License')](https://opensource.org/licenses/MIT)
[![Discord](https://img.shields.io/badge/discord-join-red.svg?logo=discord&logoColor=white)](https://discord.gg/y4p8KfzrN4)

Model Context Protocol server for Unity Editor integration. Control Unity through natural language using AI assistants like Claude, Cursor, and more.

**Maintained by [Coplay](https://www.coplay.dev/?ref=unity-mcp)** - This project is not affiliated with Unity Technologies.

💬 **Join our community:** [Discord Server](https://discord.gg/y4p8KfzrN4)

**Required:** Install the [Unity MCP Plugin](https://github.com/CoplayDev/unity-mcp?tab=readme-ov-file#-step-1-install-the-unity-package) to connect Unity Editor with this MCP server.

---

## Quick Start

### 1. Pull the image

```bash
docker pull msanatan/mcp-for-unity-server:latest
```

### 2. Run the server

```bash
docker run -p 8080:8080 msanatan/mcp-for-unity-server:latest
```

This starts the MCP server on port 8080.

### 3. Configure your MCP Client

Add the following configuration to your MCP client (e.g., Claude Desktop config, Cursor settings):

```json
{
  "mcpServers": {
    "UnityMCP": {
      "url": "http://localhost:8080/mcp"
    }
  }
}
```

---

## Configuration

The server connects to the Unity Editor automatically when both are running. No additional configuration is needed.

**Environment Variables:**

- `DISABLE_TELEMETRY=true` - Opt out of anonymous usage analytics
- `LOG_LEVEL=DEBUG` - Enable detailed logging (default: INFO)

Example running with environment variables:

```bash
docker run -p 8080:8080 -e LOG_LEVEL=DEBUG msanatan/mcp-for-unity-server:latest
```

---

## Remote-Hosted Mode

Remote clients must use HTTPS and Unity plugins must use WSS. The Python server is a private HTTP backend behind a TLS proxy. Use the repository's `docker-compose.remote.yml`, which builds the current source and publishes only the Caddy proxy's ports. From the repository root:

```bash
export MCP_DOMAIN=mcp.example.com
export UNITY_MCP_API_KEY_VALIDATION_URL=https://auth.example.com/api/validate-key
export UNITY_MCP_API_KEY_LOGIN_URL=https://app.example.com/api-keys
docker compose -f docker-compose.remote.yml up -d --build
```

Point the domain at the Docker host and allow inbound ports 80 and 443 for Caddy's certificate issuance and HTTPS service. Do not combine this Compose file with the local one, add a backend `ports` mapping, or send API keys to an HTTP URL. Keep the proxy-to-backend hop on the private host network. When configuring another proxy, explicitly set `UNITY_MCP_HTTP_BEHIND_TLS_PROXY=true` only after securing that boundary; the server refuses remote startup without it, and the setting itself does not enable TLS.

In this mode:

- All MCP HTTP requests, including initialization and catalogs, and Unity plugin WebSocket upgrades require a valid `X-API-Key` header.
- Each user only sees Unity instances that connected with their API key.
- Users must explicitly call `set_active_instance` to select a Unity instance.

**Remote-hosted environment variables:**

| Variable | Description |
|----------|-------------|
| `UNITY_MCP_HTTP_REMOTE_HOSTED` | Enable remote-hosted mode (`true`, `1`, or `yes`) |
| `UNITY_MCP_HTTP_BEHIND_TLS_PROXY` | Required: confirm a private backend behind an HTTPS/WSS proxy |
| `UNITY_MCP_API_KEY_VALIDATION_URL` | External endpoint to validate API keys (required) |
| `UNITY_MCP_API_KEY_LOGIN_URL` | URL where users can obtain/manage API keys |
| `UNITY_MCP_API_KEY_CACHE_TTL` | Cache TTL for validated keys in seconds (default: `300`) |
| `UNITY_MCP_API_KEY_SERVICE_TOKEN_HEADER` | Header name for server-to-auth-service authentication |
| `UNITY_MCP_API_KEY_SERVICE_TOKEN` | Token value sent to the auth service |

**MCP client config with API key:**

```json
{
  "mcpServers": {
    "UnityMCP": {
      "url": "https://mcp.example.com/mcp",
      "headers": {
        "X-API-Key": "<your-api-key>"
      }
    }
  }
}
```

For full details, see the [Remote Server Auth Guide](../website/docs/guides/remote-server-auth.md).

---

## Example Prompts

Once connected, try these commands in your AI assistant:

- "Create a 3D player controller with WASD movement"
- "Add a rotating cube to the scene with a red material"
- "Create a simple platformer level with obstacles"
- "Generate a shader that creates a holographic effect"
- "List all GameObjects in the current scene"

---

## Documentation

For complete documentation, troubleshooting, and advanced usage, please visit the GitHub repository:

📖 **[Full Documentation](https://github.com/CoplayDev/unity-mcp#readme)**

---

## License

MIT License - See [LICENSE](https://github.com/CoplayDev/unity-mcp/blob/main/LICENSE)
