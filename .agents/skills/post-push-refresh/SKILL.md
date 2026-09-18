---
name: post-push-refresh
description: 'After every successful GitHub push in this project, update David''s Mac LaunchAgent, verify the installed code, regenerate today''s four charts, and send images-only to Discord. Follow this as part of finishing the push, without another request.'
---

# Post-push refresh

Run on David's Mac after each user-authorized push. This skill does not authorize another push.
Read [runtime instructions](../../../docs/launchd.md) and [images-only policy](../../../docs/adr/0009-images-only-discord-reports.md).

1. Confirm the pushed commit and passing tests. Never install unpushed code or overwrite someone else's changes.
2. From the repository root, run `./install.sh </dev/null`. Preserve the aggregate database and Keychain webhook.
3. Compare installed Python files with the pushed commit. Check `launchctl print "gui/$(id -u)/com.corral.agentic-productivity"`, a fresh completed run, and fresh BB + Cloudroom observations. A loaded job alone is not proof of success.
4. Using the installed reporting module, call `build_report` for today's local date and render all four charts with `render_chart` in parallel. Use the latest collected aggregates, not invented history.
5. Load the existing webhook with `load_webhook` and send through `post_discord`. Images only: no message text. Never expose the webhook or change daily delivery state; today's preview must not suppress tomorrow's scheduled report.
6. Confirm installation and Discord delivery briefly in chat. On failure, report the blocker instead of claiming success. Do not blindly resend after an uncertain Discord response.
