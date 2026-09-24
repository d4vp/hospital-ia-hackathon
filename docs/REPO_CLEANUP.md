# Repository cleanup (secrets, clinical data and junk files)

The original repository tracked: `.env` and `backend/.env` (real OpenAI key),
`backend/app/data/DateBaseHIS.xlsx` (67 MB, clinical free text), two Windows virtual
environments (`backend/source/`, `frontend/source/`, ~60,000 files), `__pycache__/` and
view files with emoji names. Deleting them in a new commit is **not enough**: they remain in
the history and in every clone. Do all the steps below.

## 0. Before anything else
1. **Revoke the OpenAI key** at https://platform.openai.com/api-keys and create a new one.
   Put the new key only in your local `.env`. Set a monthly budget limit.
2. On GitHub: *Settings → General → Danger zone → Change visibility → Private*.

## 1. Back up
```bash
git clone --mirror https://github.com/d4vp/hospital-hackathon.git hospital-hackathon-backup.git
```
Keep the backup offline and delete it when you are sure it is no longer needed
(it still contains the key and the data).

## 2. Rewrite history (removes files from every commit)
```bash
pip install git-filter-repo
git clone https://github.com/d4vp/hospital-hackathon.git hh-clean
cd hh-clean
git filter-repo --force \
  --invert-paths \
  --path .env \
  --path backend/.env \
  --path backend/source \
  --path frontend/source \
  --path-glob '*.xlsx' \
  --path-glob '*/__pycache__/*' \
  --path-glob '*.pyc'
```
Check that nothing sensitive is left:
```bash
git log --all --stat -- '*.env' '*.xlsx' | head          # must print nothing
git grep -I "sk-" $(git rev-list --all) | head          # must print nothing
du -sh .git                                              # should drop from ~340 MB to a few MB
```

## 3. Push the clean history
`git filter-repo` removes the remote on purpose:
```bash
git remote add origin https://github.com/d4vp/hospital-hackathon.git
git push origin --force --all
git push origin --force --tags
```
Then:
* Every collaborator must **delete their old clone and clone again** (an old clone would
  push the files back).
* Close or re-create open pull requests (they reference old commits).
* GitHub may keep cached views of old commits: open a request at
  https://support.github.com/request (“Remove sensitive data”) with the commit SHAs.

## 4. Keep it clean
* The new `.gitignore` covers `.env*`, `*.xlsx`, `source/`, `.venv/`, `__pycache__/` and
  `backend/app/data/*`; `.dockerignore` keeps them out of images.
* Optional pre-commit guard:
  ```bash
  pip install pre-commit detect-secrets
  detect-secrets scan > .secrets.baseline
  ```
  and add the `detect-secrets` hook to `.pre-commit-config.yaml`.
* Enable *Settings → Code security → Secret scanning / Push protection* on GitHub.
