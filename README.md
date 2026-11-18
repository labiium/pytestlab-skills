# PyTestLab skills

Give your coding agent the workflow for setting up a bench and writing real measurement procedures with [PyTestLab](https://pytestlab.org/).

## Install for Claude Code

Run this in your experiment project:

```sh
npx skills add labiium/pytestlab-skills --skill pytestlab-onboarding pytestlab-experiments ptl-profile-skill -a claude-code -y
```

Then ask Claude Code:

> Use pytestlab-onboarding to install PyTestLab in this project, discover the instruments available to this computer and help me configure my oscilloscope. Match an existing profile, or use ptl-profile-skill with its programming manual if one is missing. Then use pytestlab-experiments to write a short channel-1 capture. Show me the selected instrument and planned changes before running hardware commands.

`skills` is the open-source [Vercel skill installer](https://github.com/vercel-labs/skills). It installs the skill instructions into your project for Claude Code; it does not install PyTestLab or operate equipment itself. The agent follows the onboarding skill to do setup. For another agent, use the installer's `--agent` option or place each skill folder in the agent's supported skills directory.

### Without a Node.js installer

```sh
git clone https://github.com/labiium/pytestlab-skills.git
mkdir -p .claude/skills
cp -R pytestlab-skills/skills/* .claude/skills/
```

## Included skills

| Skill | What it teaches |
| --- | --- |
| `pytestlab-onboarding` | Install into a project, discover VISA resources, match profiles, prepare `bench.yaml` |
| `ptl-profile-skill` | Build reusable instrument profiles from programming manuals and datasheets, validate against the installed version, verify authorized hardware operations |
| `pytestlab-experiments` | Use Bench/factory APIs, simple loops, channel reads, requested resets and waveform analysis |

## Compatibility and verification

The onboarding and experiment APIs were checked against public PyTestLab **0.2.4**. The profile-authoring skill also supports development workflows and starts by checking the actual installed schema/CLI; newer fields and validators are not assumed to exist in 0.2.4. `ptl list` lists software resources, not attached equipment. Hardware discovery uses PyVISA when the installed CLI has no discovery command.

These skills do not bundle instrument drivers, programming manuals, a VISA runtime or a simulated oscilloscope. Public hardware profiles need not contain simulation behavior. Hardware acquisition requires compatible equipment and an approved physical setup; installing the skill does not validate that setup. No live equipment was operated to validate this skill package.

The profile feedback runner has offline regression tests:

```sh
python -m pip install pytest
python -m unittest discover -s skills/ptl-profile-skill/tests -v
```

Read the profile skill's compatibility gate before using its optional runner. On releases without its required CLI checks, use only the validators actually supported by that release and report the missing coverage.
