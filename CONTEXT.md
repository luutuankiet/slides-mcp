# slides-mcp

An MCP server that lets agents read and make narrow edits to Google Slides decks, run either locally over stdio or as a shared remote server that each caller signs in to with their own Google account.

## Language

### Remote sign-in

**Server token**:
The token slides-mcp issues to an MCP client after sign-in, and the only credential a client ever presents to slides-mcp.
_Avoid_: access token, bearer, MCP token

**Google grant**:
One caller's Google access token and refresh token, held by slides-mcp and never sent to the client. It is what a caller gives at Google's consent screen and what a revocation or suspension ends.
_Avoid_: Google token, upstream token, credentials

**Auth store**:
The shared state a remote slides-mcp keeps between calls: registered clients, in-flight sign-ins and Google grants. It must outlive any one server instance, and losing it signs everyone out.
_Avoid_: token store, session store, cache

**Deployer**:
The person who runs a remote slides-mcp instance and owns its Google OAuth app, and so decides who may sign in.
_Avoid_: operator, admin
