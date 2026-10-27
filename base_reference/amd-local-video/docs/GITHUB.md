# Put the workspace on GitHub

This folder is ready to become a repository. It has not been published or linked
to a GitHub account. The scripts are reconstructed from the conversation; copy
any additional local changes from the Linux server before committing if needed.

Create an empty repository on GitHub without an initial README, then run from
the root of this folder. Substitute the actual username and repository name in
the remote URL. These are terminal commands, not Python file contents.

```bash
git init -b main
git add .
git diff --cached --stat
git status --short
git commit -m "Add AMD local video generation workspace"
git remote add origin https://github.com/YOUR-USERNAME/YOUR-REPOSITORY.git
git push -u origin main
```

Before committing, inspect the staged list. Only code, requirements, and
documentation should be present. `.gitignore` excludes the usual video/model
files and environments, but cannot recognize every possible secret filename.

For existing server files, copy only source and any desired documentation into
the appropriate folder. Do not copy `.venv`, model caches or generated outputs.
The packaged CLI uses `--prompt "..."` rather than a positional prompt argument.

The code has no LICENSE file because the owner has not chosen a license. Add a
license of your choice before distributing it for general reuse. A code license
does not replace upstream model licenses or usage terms.
