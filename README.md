# Skills

Portable agent skills for coding agents such as Codex and Claude Code.

## Available skills

| Skill | What it does |
| --- | --- |
| [`request-pov`](request-pov/) | Gets an independent second opinion from a model in another AI lineage through OpenRouter. |

## Install `request-pov`

### Before you begin

This works in the **Codex coding agent** (desktop or CLI) and **Claude Code**.
It cannot install itself in an ordinary ChatGPT or Claude web conversation,
because those chats do not have access to your computer's persistent skill
directories.

You will need:

- Git and Python 3.10 or newer
- An [OpenRouter](https://openrouter.ai/) account
- An OpenRouter API key and enough account credit for the model you call

Never paste an API key into an agent conversation. Set it privately in a
separate terminal when the agent gives you instructions.

### The easy install

Paste this into Codex or Claude Code:

```text
Install the request-pov skill for the coding agent you are currently running. This instruction is for Codex or Claude Code, not an ordinary ChatGPT or Claude web conversation.

1. Clone https://github.com/piersonr/skills into a temporary directory.
2. Copy only the request-pov directory into the current agent's user-level skill directory: ~/.agents/skills/request-pov for Codex, or ~/.claude/skills/request-pov for Claude Code. Install only for the current agent. Do not use a symlink and do not install a second copy for another agent.
3. Run the bundled unit tests and a dry-run. Do not make a paid API request.
4. If OPENROUTER_API_KEY is unavailable, do not ask me to paste it into this conversation. Explain how to create an OpenRouter key and add account credit, then give me OS-appropriate instructions to set the variable privately and persistently from a separate terminal. Warn me that I may need to restart the coding agent before it can read the new variable. Never display the key or modify my shell profile, credential store, or .env without my permission.
5. Show me the installed file tree and the validation results. Tell me whether I need to exit and restart the agent for skill discovery, then give me the exact sentence to invoke request-pov after restarting.
```

If you use both Codex and Claude Code, repeat the prompt in the other agent.
Keeping the installations independent avoids symlink and cross-platform issues.

## Use `request-pov`

In Codex:

```text
Use $request-pov to ask an Anthropic model to challenge this implementation plan.
```

In Claude Code:

```text
Use /request-pov to ask an OpenAI model for a second opinion on this architecture.
```

You can also ask naturally: “Get a cross-lineage POV on this diff” or “What would
Anthropic challenge about this approach?”

The skill defaults to an Anthropic model when called from an OpenAI agent and an
OpenAI model when called from an Anthropic agent. Its model slugs can be changed
with `POV_ANTHROPIC_MODEL`, `POV_OPENAI_MODEL`, or the helper's `--model`
option.

## What leaves your machine

The question and any context files you deliberately include are sent to
OpenRouter and the selected model provider. The skill:

- does not give the outside model access to your workspace;
- blocks common credential patterns and credential-like files;
- caps context at 200 KiB by default;
- asks OpenRouter to use providers that deny data collection;
- can require a zero-data-retention endpoint with `--zdr`;
- never prints your API key.

Those safeguards reduce accidental disclosure; they are not a substitute for
reviewing what you send or for reading OpenRouter's and the model provider's
current privacy terms. Do not send secrets, personal data, customer data, or
confidential code without appropriate permission.

## What this is—and is not

This is an external model consultation, not a native subagent. It is useful for
challenging assumptions, comparing architectural tradeoffs, and getting a fresh
code-review pass. It does not replace tests, required human review, or your own
judgment.

Each subdirectory in this repository is a self-contained skill following the
open [Agent Skills](https://agentskills.io/) format.

## Verify `request-pov`

```bash
python3 request-pov/scripts/test_request_pov.py
python3 request-pov/scripts/request_pov.py \
  --lineage anthropic \
  --prompt "Challenge this harmless test plan" \
  --dry-run
```

Both commands are local and make no paid API request.

## License

[MIT](LICENSE)
