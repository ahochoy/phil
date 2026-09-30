# Phil Live-Testing Feedback

**Date:** September 29, 2026  
**Status:** Organized feedback; not an implementation plan

## Summary

The feedback points toward a clear product direction: make Phil simpler to configure, smarter about how much process a task deserves, more efficient in its use of models and tokens, and more transparent while it works.

The strongest theme is proportionality. Phil should use the smallest sufficient workflow, select the appropriate model tier, keep internal communication concise, and make its decisions and activity visible to the user.

## Product principles

- Simple tasks should stay simple.
- Advanced customization should remain possible without dominating the default experience.
- Users should configure common preferences once and inherit them across repositories.
- Model power and orchestration depth should match the task.
- Agent-to-agent communication should be concise and economical.
- Activity, errors, costs, and sub-agent work should be visible.
- The terminal UI should feel structured and interactive rather than like an unformatted stream.
- Permissions should be safe but less repetitive.
- Extensibility through skills, plugins, snippets, explorers, and diagrams should be part of Phil's long-term identity.

## 1. Configuration hierarchy and first-run experience

### Desired configuration layers

Phil should support at least two configuration scopes:

1. Global defaults stored under `~/.phil`
2. Repository-specific settings that override those defaults

A likely resolution order would be:

```text
Built-in defaults
    ↓
Global ~/.phil settings
    ↓
Repository-specific settings
    ↓
Session or command-level overrides
```

### First-run onboarding

On a fresh installation, Phil should recognize that no user configuration exists and start a guided setup.

That setup would collect:

- Model providers
- Credentials or provider access
- Default model selections
- Basic behavioral preferences
- Possibly default permission policies

After setup, Phil would write the resulting configuration under `~/.phil` and enter the normal workflow.

### Manual control

The generated configuration should remain understandable and directly editable. Guided setup is a convenience, not the only way to configure Phil.

## 2. Simplified model selection

The current model-per-agent configuration feels too granular for normal use.

### Proposed model roles

Instead of asking users to configure every agent separately, users would configure two or three model tiers.

#### High-reasoning model

Suitable for:

- Complex architecture
- Difficult implementation
- High-stakes review
- Tasks requiring frontier-level reasoning

#### Low-reasoning model

Suitable for:

- Routine development work
- Verification and mechanical tasks
- Summaries and ordinary tool-oriented work
- Classification when no dedicated classifier is configured

#### Classifier or decision model

This would be optional and intended for fast, System 1-style routing. It could determine task type, complexity, and the required workflow, including through specialized decision models such as Dev-, JEV-, or Leia-type models.

If no classifier model is configured, classification work should fall back to the low-reasoning model.

### Agent-to-tier mapping

Internal agents would reference a model role rather than a specific provider model.

| Agent responsibility | Likely model tier |
| --- | --- |
| Task classifier or router | Classifier, or low fallback |
| Architect | High |
| Complex implementer | High |
| Routine implementer | Low or high, depending on classification |
| Reviewer | Usually high |
| Verifier | Usually low |
| Summarizer or status reporter | Low |

The exact mapping could remain configurable, while the normal user-facing setup stays compact.

### Intended benefit

This provides meaningful control over quality and spending without forcing users to make a model decision for every individual agent.

## 3. Task classification and proportional orchestration

This is one of the central issues revealed by live testing.

### Observed problem

A simple task triggered the complete development pipeline:

- Detailed planning
- Plan review
- Architecture work
- Multiple implementation tasks
- Implementer involvement
- Additional review and verification stages

The workflow was thorough but disproportionate to the task.

### Desired behavior

Phil should classify incoming work before choosing an execution path.

Potential task classes include:

- Question or explanation
- Small deterministic operation
- Simple code change
- Bug diagnosis
- Focused bug fix
- Feature implementation
- Refactor
- Architecture or design task
- Broad multi-file project

Each class should map to an appropriate workflow depth.

```text
Small task
→ one worker
→ focused change
→ relevant test
→ stop

Complex feature
→ investigation
→ planning
→ architecture review
→ implementation tasks
→ review
→ verification
```

### Key distinction

Model routing and workflow routing are related but separate:

- Model routing chooses how much intelligence the task needs.
- Workflow routing chooses how much process the task needs.

A simple task might occasionally need a powerful model, but it should not automatically require a large multi-agent ceremony.

## 4. Agent prompts and communication efficiency

### Prompt-quality goals

Sub-agent system prompts should be refined to emphasize:

- Accuracy
- Clean, maintainable code
- Established repository conventions
- Modern development practices
- Appropriate testing
- Efficient communication
- Concise tool usage
- Clear stopping conditions

### Token-efficiency concern

Internal communication risks becoming too verbose in areas such as:

- Orchestrator-to-agent instructions
- Agent-to-agent handoffs
- Tool-call explanations
- Progress summaries
- Repeated context

The desired direction is structured, concise communication that preserves the information necessary for correctness without repeatedly narrating it.

### Areas to evaluate later

- Smaller role-specific prompts
- Standardized handoff formats
- Compact task briefs
- Structured tool-result summaries
- Avoiding repeated repository context
- Different verbosity rules for user-visible and internal communication
- Explicit guidance to avoid overbuilding and stop when acceptance conditions pass

## 5. Failure visibility and error reporting

This portion is less fully formed but identifies a real visibility gap.

### Desired outcome

When something fails, Phil should explain what category of failure occurred and where it originated.

Potential categories include:

- LLM or provider failure
- Authentication or quota failure
- Network failure
- Tool failure
- Command failure
- Permission denial
- Invalid configuration
- Agent or orchestration failure
- Internal Phil failure

### User-facing information

A failure presentation could eventually include:

- What failed
- Which component failed
- Whether anything changed before the failure
- Whether Phil will retry
- Whether the user needs to act
- A concise diagnostic summary
- Expandable technical details

### Open point

It is not yet clear whether this should be primarily an activity-feed feature, a dedicated error panel or callout, a persistent run history, or some combination of these.

## 6. Permissions and persistent approvals

### Observed problem

Safe, repetitive CLI operations—especially `gh` commands—prompted for permission repeatedly, slowing the workflow.

### Desired approval scopes

A permission decision should potentially support:

- This invocation only
- This session
- This repository or folder
- All repositories
- Persistent across sessions

### Possible approval targets

Permissions might apply to:

- An exact command
- A command prefix, such as `gh`
- A tool
- A class of read-only operations
- A directory scope
- A specific repository
- A named workflow or agent

### Persistence

Persistent approvals could be written to:

- The global `~/.phil` configuration
- Repository-local configuration
- A dedicated permissions or policy file

### Important design tension

This feature needs to balance convenience with precision. Approving `gh status` is different from approving all possible `gh` commands, some of which can mutate remote state.

The eventual design should make the approved scope obvious before the user confirms it.

## 7. Terminal UI information architecture

The proposed UI has several persistent layers.

```text
┌──────────────────────────────────────────────────────────┐
│ Welcome/header information                               │
├──────────────────────────────────────────────────────────┤
│                                                          │
│ Main activity feed                                       │
│ - user messages                                          │
│ - reasoning summaries                                    │
│ - deterministic operations                               │
│ - tool calls and summarized results                      │
│ - errors, usage, cost, and progress                      │
│                                                          │
├──────────────────────────────────────────────────────────┤
│ Active sub-agent bar, shown when applicable              │
├──────────────────────────────────────────────────────────┤
│ Persistent chat input                                    │
├──────────────────────────────────────────────────────────┤
│ Status bar                                               │
└──────────────────────────────────────────────────────────┘
```

A sidebar or secondary pane could optionally display:

- File explorer
- Changed-file tree
- Diffs
- Diagrams
- Plugin-specific views

This is a conceptual structure only. A detailed mockup will come later.

## 8. Welcome screen

When Phil starts, it should show a recognizable welcome banner similar to other terminal development agents.

Potential content:

- Phil branding or ASCII art
- Active agent
- Active model or model tiers
- Current working directory
- Repository or worktree
- Branch
- Host
- Configuration scope
- Relevant startup state or warnings

This should provide orientation without consuming excessive terminal space.

## 9. Main activity feed

### Current issue

Too much work is hidden in background threads, leaving the user without a clear sense of what Phil is doing.

### Desired feed contents

The main feed should combine conversation and execution history:

- User messages
- Assistant responses
- Concise reasoning or intent summaries
- Deterministic or internal operations
- Tool calls
- Tool-result summaries
- File changes
- Agent lifecycle events
- Failures and retries
- Usage and pricing information
- Completion and verification results

### Presentation principle

The feed should expose meaningful progress without dumping raw internal prompts or huge command outputs into the main view.

A likely information hierarchy is:

1. Concise summary in the feed
2. Expandable details
3. Full raw output available when needed

### History

Because user messages appear in the same feed, scrolling upward should reconstruct the full run rather than showing only execution events.

## 10. Persistent chat input and status areas

### Chat input

The input bar should stay fixed and available while the feed scrolls.

### Sub-agent activity bar

When one or more sub-agents are working, a persistent bar should appear with information such as:

- Agent name
- Task
- Current state
- Elapsed time
- Model tier
- Completion or failure status

Users should eventually be able to select an agent and inspect its individual activity feed.

### Bottom status bar

Candidate fields include:

- Active agent
- Current model or tier
- Configured tiers: high, low, and classifier
- Host name
- Current working directory
- Repository or worktree
- Git branch
- Git working-tree status
- Possibly token, cost, or context usage

The final field selection should favor information that is useful continuously. Transient details belong in the feed or sub-agent bar.

## 11. Questions and approval UX

Phil's questions and permission requests should be easier to notice and answer.

Potential presentation patterns include:

- Bordered callouts
- Clear question titles
- Visually distinct warnings
- Keyboard-selectable choices
- Default and recommended options
- Explicit approval scope
- Short consequences for each option

The goal is to make interaction feel intentional and legible while remaining compatible with a CLI environment.

## 12. File explorer and diff experience

### File explorer

An optional sidebar or plugin could show:

- Repository tree
- Changed files
- Newly created files
- Deleted files
- Current file or active task
- Which agent touched each file

### Diff views

Two related but distinct views are desired:

1. A changed-file overview
   - Files touched
   - Type of change
   - Change counts or status
2. Actual file diffs
   - Patch contents
   - Added and removed lines
   - Possibly per-agent or per-task attribution

This should integrate with the main conversation rather than replacing it.

## 13. Diagrams and richer terminal output

Phil could differentiate itself by presenting technical information visually inside the terminal.

Possible forms include:

- ASCII diagrams
- Trees
- Flowcharts
- Dependency maps
- Tables
- Mermaid source
- Rendered Mermaid where the terminal environment supports it

The feasibility depends on terminal capabilities, but even reliable ASCII visualization could be valuable.

## 14. Extensibility topics reserved for later design

Several important areas were intentionally raised without defining their implementation yet:

- Code snippets
- Skills
- Plugins
- Extensions and UI modifiers
- File explorer integration
- Diagram support
- Memory
- Self-learning
- Rich diff presentation

These should become their own design discussions rather than being folded prematurely into the core UI work.

A useful future distinction may be:

- **Core capabilities:** Essential Phil behavior
- **Built-in optional modules:** Maintained with Phil but toggleable
- **Plugins:** Independently installable capabilities
- **UI extensions:** Panels, viewers, and presentation modifiers
- **Skills:** Reusable behavioral or procedural knowledge

## 15. Memory and self-learning

Phil already has some memory functionality, but the long-term behavior needs a deliberate plan.

Questions to resolve later include:

- What belongs in global versus repository memory?
- What may Phil learn automatically?
- What requires user approval?
- How are memories viewed, edited, or deleted?
- How are incorrect or stale memories detected?
- Should model and permission preferences be treated as memory or configuration?
- How is sensitive information excluded?
- How does the UI show when memory influenced a decision?

This is closely related to `~/.phil`, but configuration, permissions, and learned memory should probably remain conceptually separate.

## Open decisions

The feedback surfaces the following unresolved choices:

1. Whether per-agent model selection remains available as an advanced option.
2. Whether classifier selection is explicit or automatically inferred from configured models.
3. Which task classes Phil should recognize.
4. Which workflows correspond to those task classes.
5. How much reasoning should appear in the activity feed.
6. How approval rules are scoped without accidentally granting broad mutation rights.
7. Which status information belongs permanently on screen.
8. Whether the file explorer and diff viewer are core features or extensions.
9. What level of mouse interaction and clickable navigation the terminal UI should support.
10. Whether sub-agent feeds are live interactive views or summarized snapshots.
11. How configuration, permission policies, and learned memory are separated on disk.
12. Which richer visualizations can be supported consistently across terminals.

## Potential future planning tracks

When this feedback is ready to become a plan, it naturally divides into six workstreams.

### 1. Configuration and onboarding

- `~/.phil`
- Layered settings
- First-run setup

### 2. Model and workflow intelligence

- High, low, and classifier tiers
- Task classification
- Proportional orchestration

### 3. Efficiency and reliability

- Agent prompt refinement
- Concise handoffs
- Error categorization
- Usage and cost visibility

### 4. Permissions

- Approval scopes
- Persistent policies
- Safe command matching

### 5. Terminal experience

- Welcome screen
- Activity feed
- Input and status bars
- Questions and approvals
- Sub-agent navigation
- File and diff views

### 6. Extensibility and learning

- Skills
- Plugins
- Snippets
- Diagrams
- Memory
- Self-learning

