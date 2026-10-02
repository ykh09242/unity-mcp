# Website dependency overrides

Use the Node and npm versions declared in `package.json`; CI installs its declared npm version. npm 12 correctly validates the overridden peer graph.

Docusaurus 3.10.2 still selects older tooling. The three explicit overrides keep its production and development commands on current stable majors:

- `copy-webpack-plugin` 14 and `css-minimizer-webpack-plugin` 8 select patched `serialize-javascript` 7 instead of the vulnerable 6.x series.
- `webpack-dev-server` 6 removes the SockJS dependency that brought in vulnerable `uuid` 8. Docusaurus uses its supported programmatic API and default WebSocket transport.

Remove the overrides when Docusaurus's dependency ranges select these supported versions directly. After changing them, run a clean install, `npm ls --all`, `npm audit`, `npm run build`, and verify `npm start` with browser navigation and hot reload.
