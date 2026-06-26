# Campaign Copilot

An LLM agent that helps a marketer go from a plain-English goal to a ready-to-launch campaign.

Copilot drafts a ready-to-launch campaign through multi-step reasoning and tool use:

1. **Understand the goal** and figure out what data it needs.
2. **Query the provided dataset** of users and events to build a target segment.
3. **Ground itself** in our messaging guidelines (provided in `/guidelines`) using retrieval, so its recommendations follow our best practices.
4. **Draft the campaign**: the target segment definition plus suggested message content (copy, and where the channel supports it, richer elements like an image or an offer/incentive), then **create it** via an idempotent "create campaign" operation.

The agent decides *which* steps to take and *when*; it should plan, not follow a hardcoded script.