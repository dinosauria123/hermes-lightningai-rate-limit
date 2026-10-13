# lightningai-rate-limit Hermes plugin

Per-process token bucket that keeps Lightning AI requests to <=15/minute.
Middleware registered as `llm_execution`; activates only when the active
provider/base_url points at Lightning AI.

## Disclosure

- Registers one `llm_execution` middleware and nothing else.
- No network, files, or processes of its own. In-memory lock only.
