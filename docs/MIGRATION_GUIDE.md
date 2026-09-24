# Applying version 2 to your local repository, step by step

## 1. Clean the repository first
Follow `REPO_CLEANUP.md` (history rewrite) and work on the **fresh clone** `hh-clean`.

## 2. Create a branch
```bash
cd hh-clean
git checkout -b feature/v2-secure-agent
```

## 3. Remove files that are replaced or no longer used
```bash
git rm -r --cached --ignore-unmatch backend/source frontend/source .env backend/.env
git rm -r --ignore-unmatch \
  backend/app/config.py backend/app/database.py backend/app/data/README.txt \
  frontend/utils \
  "frontend/views/1_🤖_Agente_IA.py" "frontend/views/2_📊_Dashboard_KPIs.py" "frontend/views/3_📁_Carga_Datos.py"
find . -name "__pycache__" -type d -prune -exec rm -rf {} +
```
(`backend/app/main.py`, `services/data_loader.py`, `services/kpi_service.py`,
`services/mongo_agent.py`, both `Dockerfile`s, `requirements.txt`, `docker-compose.yml`,
`README.md`, `.gitignore`, `.env.example` and `frontend/app.py` are overwritten in step 4.)

## 4. Copy the new files
Unzip `hospital-hackathon-v2.zip` somewhere else and copy its content over the repo
(hidden files included):
```bash
# Linux / macOS / Git Bash
rsync -av --exclude '.git' ../hospital-hackathon-v2/ ./
# Windows PowerShell
robocopy ..\hospital-hackathon-v2 . /E /XD .git
```

## 5. Local configuration
```bash
cp .env.example .env            # also copy to backend/.env if you run the backend from backend/
python -c "import secrets; print(secrets.token_urlsafe(48))"   # paste as JWT_SECRET
```
Fill in `BOOTSTRAP_ADMIN_PASSWORD` (10+ chars, letters and numbers), the **new**
`OPENAI_API_KEY` (optional: empty = Plan B mode) and, if you have it, `N8N_WEBHOOK_URL`.
Place `DateBaseHIS.xlsx` in `backend/app/data/` (git-ignored).

## 6. Fresh virtual environments
Delete the old `source/` folders and create new ones **outside git tracking**:
```bash
cd backend && python -m venv .venv && .venv/Scripts/activate   # macOS/Linux: source .venv/bin/activate
pip install -r requirements-dev.txt
pip freeze > requirements.lock.txt     # optional exact snapshot of your working environment
```

## 7. Database and data
```bash
docker run -d --name mongo -p 127.0.0.1:27017:27017 mongo:7.0   # or your local MongoDB
python -m app.scripts.load_data          # ~1-2 min, mostly reading the .xlsx
```
Collections are renamed to English (`admissions`, `inventory`…). Drop the old ones if you
want: `mongosh hospital_susana_lopez --eval "db.ingresos.drop()"` (adapt to the old names).

## 8. Run and verify
```bash
uvicorn app.main:app --reload                     # backend  → http://localhost:8000/docs
pytest                                            # all tests green
cd ../frontend && python -m venv .venv && .venv/Scripts/activate && pip install -r requirements.txt
streamlit run app.py                              # frontend → http://localhost:8501
```
Sign in with the bootstrap admin, ask the four demo questions (expected answers in the README),
switch language/contrast/text size in the sidebar, and create a `user` account to confirm the
admin pages disappear for it.

## 9. Commit and open a pull request
```bash
git add -A
git status            # confirm: no .env, no .xlsx, no source/ or .venv/
git commit -m "v2: secure NL2MQL agent, auth/roles, alerts via n8n, inferential reports, accessible UI"
git push -u origin feature/v2-secure-agent
```
Merge into `main` after review, then redeploy with the new environment variables.
