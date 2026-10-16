# Knowledge Forge — user manual

**Forge → Accept → Ask.** A person can give a terminal-capable AI agent a
document, review its knowledge skills, accept them into a personal library,
then ask questions grounded in the stored source references. This is the
ordinary-user workflow; it does not need privileged operator deployment.

## Give the agent one clear instruction

Before the first request, the agent needs the
[newcomer guide](docs/NEWCOMER_GUIDE.md) and a working local installation.
It needs Git, Python 3.10+, and permission to read the file you name.
The generic installer adds commands, **not** an automatic Claude/Codex
plugin or chat hook.

Copy this once and replace the path:

```text
Install https://github.com/ToadAid/knowledge-forge-public by following
docs/NEWCOMER_GUIDE.md in my private workspace. Keep my existing agent
configuration unchanged. Use docs/FILE_TO_SKILL_AGENT_GUIDE.md to forge
<absolute-path-to-my-file> into source-grounded VALIDATED knowledge.
Preserve the original file and report complete coverage, omissions,
claim judgments and citations. Ask me before adding anything to my
PERSONAL library. After I say yes, use knowledge-forge personal add.
When I ask a question, use knowledge-forge personal lookup, and cite
only source bindings actually returned. Do not perform governed promotion.
```

Once the agent understands the workflow, a short exchange might look like:

```text
You:   Forge this book into skills, brother.
Agent: Forged 2 VALIDATED packages. Covered 26 of 28 sections; 2 omitted.
       The claim-support decisions were model-judged. Add them?
You:   Yes, add both to my library.
Agent: Added both to your local PERSONAL library; neither is PROMOTED.
You:   What does it say about patience?
Agent: [Runs personal lookup; summarizes supported units with sources.]
```

This is an illustrative conversation, not a claim that an agent
integration or native UI has already been installed.

## What happens behind the scenes

| Step | The agent does | What you check |
| --- | --- | --- |
| Inspect | Reads only the chosen source, records identity and content hash | Right file and permission |
| Plan | Counts sections/records and lists intended coverage and omissions | No hidden sampling |
| Forge | Creates structured draft, support plan and source-grounded package | Attribution, quotes, dates, uncertainty |
| Validate | Runs `finalize` then `validate-package`; checks `VALIDATED` lifecycle | Real results and source bindings |
| Accept | **Asks you first**, then invokes `personal add --accept` if you agree | Local acceptance, not promotion |
| Ask | Invokes `personal lookup` and reads its bounded source-linked context | Answer and references actually returned |

A personal library stores package references and validation data, **not a
second copy of your source or package**. Moving, changing or deleting the
package can cause future lookup to refuse. `personal remove` deletes the
library reference only; original files are untouched.

The agent must not silently substitute raw-file reading or its general
knowledge for a Forge lookup. If lookup returns no match, report that
honestly and do not fabricate citations.

## Review the result

If a 132-record collection is reduced to five records, the report must say
**5 of 132**, naming and explaining omissions. Ask the agent to show the
source evidence, uncertain claims, original author, dates, URLs where they
exist, and any AI-written interpretation.

A model's `SUPPORTED` entailment is **not independent verification** of
truth. A `VALIDATED` package proves the specified structure/integrity
checks passed, not perfect semantic faithfulness. Qualifiers such as
*may*, *if* and *according to* should stay with their original propositions.

Never provide sources you do not have rights to process or redistribute.
Local software does not override an agent provider's separate data policies.

## Linux, native Windows and PDFs

The ordinary installer works on Linux and native Windows 10/11 with
Python 3.10+ and Git. Windows users can use normal `cmd.exe` without WSL or
administrator rights. Text-bearing PDF extraction optionally needs Poppler;
explicit OCR needs Poppler and Tesseract. Knowledge Forge does not install
these system tools. See [INSTALL.md](INSTALL.md).

## What does "accept" mean?

For Personal Forge, your explicit **yes** permits the agent to run
`personal add --accept`. The library records conversational acceptance:
`authenticated_human=false`. It remains `VALIDATED`, not `PROMOTED`.
Retrieved content is `UNTRUSTED_KNOWLEDGE_DATA`/`KNOWLEDGE_ONLY`,
with no instruction authority, execution authority or tool grants.

Advanced governed promotion is different: independently authenticated
human authorization and a protected host are required. A native approval
adapter contract alone does **not** prove live client integration.

[Personal Forge commands](docs/PERSONAL_FORGE.md) ·
[Feature status](docs/FEATURE_STATUS.md) ·
[Optional governed approval](docs/INLINE_APPROVAL.md)

**Laws:** `VALIDATED != PROMOTED` · `KNOWLEDGE != AUTHORITY` ·
`ACCEPTED != AUTHENTICATED`.
