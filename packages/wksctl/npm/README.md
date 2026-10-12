# wksctl

Standalone WKS CLI for the public HTTP gateway. No Python, Go compiler or server
installation required. Node.js 20+ launches the bundled native Go client on Linux,
macOS and Windows (x64/ARM64). All six binaries are shipped inside the npm tarball;
there are no dependencies, install scripts or additional executable downloads.

```sh
npm install -g wksctl
wksctl version
wksctl skill install --directory ./my-project
```

Or install the Skill without a global CLI installation:

```sh
npx --yes wksctl skill install --directory ./my-project
```

The Skill is written to `.agents/skills/wks/SKILL.md` inside the selected directory.
Installation works offline once the package is installed, does not create an account
or workspace, is idempotent and preserves customized skills and `AGENTS.md`.
`wksctl init --agents` also initializes a workspace manifest with the Skill.

Inspect `wksctl help --json` and `wksctl schema` before configuring a context.
Pass credentials with `wksctl auth login --token-stdin`; never put tokens in arguments.
See https://github.com/A1b3rt0M3rcad0/wks/blob/master/docs/CLI.md.

Stable releases use the npm `latest` tag; prereleases use `next`.
Licensed under Apache-2.0. Bundled dependency notices are included.
