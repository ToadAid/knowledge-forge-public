# Knowledge Forge user manual: from a file to a skill

**No programming required to describe the job.** Tell your coding agent where
your file is. The agent handles the technical workflow and shows you what it
created. You decide whether to request review; a separate authorized human
operator decides whether it may become promoted knowledge.

## The short version

After Knowledge Forge has been installed for your agent, you can say:

> Make `~/Downloads/my-notes.md` into a knowledge skill.

Or, when you want to start the review process:

> Make `~/Downloads/my-book.pdf` into knowledge skills and submit them for approval.

Or:

> Turn `~/Downloads/temp/toadgod-lore.json` into source-grounded skills.
> Preserve original quotations and attribution. Prepare them for human review.

You do **not** need to write a long technical prompt or know the names of the
authoring commands. The agent should explain what it is doing in ordinary
language and tell you when a decision is needed.

**First-time setup:** installing Knowledge Forge does not automatically
teach every Claude/Codex/other agent to recognize the short request. Until your
agent has an explicitly installed file-to-skill workflow, start by saying:

> Install ToadAid Knowledge Forge from
> https://github.com/ToadAid/knowledge-forge-public, following its
> [installation guide](INSTALL_AGENT.md) and
> [file-to-skill agent guide](docs/FILE_TO_SKILL_AGENT_GUIDE.md), in a private
> workspace without changing my agent's global configuration. Then make
> `/path/to/my-file` into a knowledge skill and prepare it for review.

You can instead share this manual with an agent that already has the ordinary
consumer and authoring commands. The current generic installer installs the
commands, **not** a native Claude/Codex hook, background assistant, or
automatic knowledge-search integration.

## What happens after you say "make this a skill"?

| Step | What the agent does | What you see |
| --- | --- | --- |
| 1. Check the file | Checks access, format, size, rights and source identity without changing your original | File name, type, count/coverage when available |
| 2. Plan coverage | Lists what will become knowledge and what will be omitted; proposes multiple skills when the source is large | A plain-language coverage plan |
| 3. Forge | Uses Knowledge Forge's documented source-bound authoring workflow | Draft skill names and supporting sources |
| 4. Validate | Checks the generated packages, citations, hashes and lifecycle | PASS or actionable failure; unproven claims clearly marked |
| 5. Request review | Prepares the exact validated package and review evidence for a human | A review handoff and its *actual* delivery status |
| 6. Human decision | A separately authenticated, authorized operator may approve or reject through a properly configured host | Verified decision and, only after authorized execution, promoted registry state |

The first four steps are available today in the ordinary local authoring
workflow. **A universal submit-for-approval service, authenticated reviewer,
and automatic promotion path are not included in the generic installer.**
An agent can prepare a review handoff today; it must say
`REVIEW_HANDOFF_READY — NOT SENT` unless a separately configured, permitted
transport has actually accepted the request. A local review-only TUI can
record a review choice, but its receipt is **not authenticated promotion
authorization**.

The intended integrated product experience needs no user command:

```text
You:    Promote it.
Host:   [View details] [Approve & Promote] [Reject]
You:    select Approve & Promote.
Agent:  Promoted. Receipt and registry identity are shown.
```

The repository provides the inline host adapter for this flow, but the generic
installer does not deploy the trusted UI. The surrounding product must capture
the click outside the agent channel, independently authenticate the user, and
keep its signing key and governed registry outside agent permissions. Saying
`approve` in chat requests the card; it does not itself authorize promotion.
If that host integration is absent, the request remains pending.

For an independently administered Linux deployment, the repository also keeps
the protected socket service as a hardened backend reference. It is
intentionally separate from the agent installer and requires an administrator
to provision and verify distinct agent, reviewer, and operator identities.

## What you should check before approving a skill

Look at the agent's **coverage report**. For example, if the source has 132
records but only five were selected, the agent must say **5 of 132**, name the
five and explain the 127 omitted records. It must not claim that the entire
file was forged.

Check that each important claim cites its source. In a history or lore
collection, distinguish the original author, a repost, a later explanation,
and an AI interpretation. An AI's statement that a claim is
`SUPPORTED` is its semantic judgment; package validation does not prove that
the claim is factually true.

If the document is large, the agent may propose multiple packages. It should
respect the current 64-unit-per-draft and maximum three support
sections-per-unit limits rather than silently discarding entries.

For private or copyrighted files, use only sources you are authorized to
process. The agent should not upload or publish their contents without your
informed permission.

## Ask the agent what happened

Natural-language follow-ups work:

- "Show me which parts of the file you included and which you skipped."
- "Show me the exact original text supporting this knowledge unit."
- "What did you infer that the source did not say?"
- "Show me the validation report and where the finished package is."
- "Prepare a promotion review request; do not approve it yourself."
- "Was my review request actually sent? To whom?"
- "Is this skill validated, reviewed, authorized, or promoted? Show the evidence."

If a claim is not supported, ask the agent to revise the draft, then validate
a **new candidate**. Do not work around a failed validator or invent missing
source evidence.

## Understand the status

| Status | Meaning |
| --- | --- |
| `DRAFT` | Proposed content; not a validated package |
| `VALIDATED` | Integrity/contract checks passed; **not promoted** |
| `REVIEW_HANDOFF_READY` | Local review materials prepared; may not have been sent |
| `REVIEWED_ONLY` | A local review choice recorded; **not authentication or approval authority** |
| `PROMOTED` | Only after a separate authenticated, authorized operator action and verified registry/lifecycle evidence |

These are **plain-language UX labels**, not a promise that the current CLI
implements a queue, notification service, or these exact names as lifecycle
states.

A model **must not** approve or promote the skill it authored. Saying "please
promote it" means **request a human decision**, not waive the approval gate.
Validation success and an agent's confident answer do not grant authority.

## Can I immediately ask my agent questions about the new skill?

You can request a clearly labeled **manual package-inspection preview** from
a `VALIDATED` package. This is a test, not trusted retrieval. It must not
pretend the package is promoted.

Automatic, governed retrieval is a **separate host integration**: an authorized
operator first promotes the exact validated package, and a tested host adapter
discovers and loads only current promoted knowledge under existing host policy.
Installation or a manual preview alone does not provide this functionality.

See the [newcomer guide](docs/NEWCOMER_GUIDE.md),
[agent file-to-skill guide](docs/FILE_TO_SKILL_AGENT_GUIDE.md), and
[command-free approval integration](docs/INLINE_APPROVAL.md) for details. The
[review-only approval boundary](docs/GENERIC_APPROVAL_TUI.md) remains useful
for diagnostic review receipts.

## If the agent says it cannot proceed

A refusal can be the correct result: unreadable source, unsupported input,
unknown author, missing sections, a changed hash, absent optional PDF/OCR
dependencies, failed validation, or an unavailable approval destination.
Ask it to report the exact reason and what *you* can do next. It must not
invent a success, skip evidence, or promote through a workaround.
