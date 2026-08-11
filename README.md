# request-pov

Ask your coding agent to get an independent second opinion from a model in
another AI lineage.

`request-pov` is a portable agent skill for Codex and Claude Code. It sends a
focused question and only the context you choose through OpenRouter, then asks
your current agent to compare that outside perspective with its own analysis.

## The easy install

Paste this into Codex or Claude Code:

```text
Install the request-pov skill from https://github.com/piersonr/request-pov/tree/main/skills/request-pov for this coding agent. Detect whether you are running in Codex or Claude Code and place it in the appropriate user-level skills directory so it works across projects. If both agents are installed, make the skill available to both without maintaining duplicate copies. Help me create an OpenRouter API key if I do not already have one, and store it securely as OPENROUTER_API_KEY. Never display, log, or commit the key. Run the bundled unit tests and a dry-run, then tell me what was installed and how to invoke it. Do not make a paid API request until I explicitly approve one.
```

The agent should handle the file locations and validation. You will need:

- Codex or Claude Code
- Python 3.10 or newer
- An [OpenRouter](https://openrouter.ai/) account, API key, and enough credit for
  the model you call

## Use it

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

The skill follows the open [Agent Skills](https://agentskills.io/) format. The
same `SKILL.md` and Python helper are shared by Codex and Claude Code.

## Verify the packaged skill

```bash
python3 skills/request-pov/scripts/test_request_pov.py
python3 skills/request-pov/scripts/request_pov.py \
  --lineage anthropic \
  --prompt "Challenge this harmless test plan" \
  --dry-run
```

Both commands are local and make no paid API request.

## License

[MIT](LICENSE)
