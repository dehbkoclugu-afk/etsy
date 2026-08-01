# Open Design Integration

This repo integrates [Open Design](https://github.com/nexu-io/open-design), an
open-source, local-first alternative to Claude Design. It lets coding agents
generate design artifacts (prototypes, dashboards, decks, images, videos) that
export to HTML, PDF, PPTX, or MP4.

Open Design is not distributed as an npm package — it runs as a desktop app,
a Docker service, or a local MCP server that agents connect to.

## Setup options

### 1. Desktop app (recommended for local use)
Download from https://open-design.ai, then run:

```bash
npm run design:install
```

This wires Open Design into Claude Code via `od mcp install claude-code`.

### 2. Self-hosted via Docker
```bash
docker compose up -d
```
Starts the Open Design service defined in `docker-compose.yml` on port 3939.

### 3. From source
```bash
git clone https://github.com/nexu-io/open-design
cd open-design
pnpm install
pnpm tools-dev run web
```

## Related resources

- [awesome-claude-design](https://github.com/rohitg00/awesome-claude-design) —
  curated list of Claude Design resources, prompts, and examples worth
  browsing alongside this integration.

## License

Open Design is Apache-2.0 licensed.
