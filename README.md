# burnout. — Early Burnout Detection & Adaptive Study Scheduling

A single-codebase full-stack student wellness/study dashboard built with:

- FastAPI + Jinja2
- SQLite
- Cookie-based sessions
- Rule-based early burnout risk engine
- Chart.js for interactive trends
- `seed.py` for two 14-day demo scenarios

## Run

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python seed.py
uvicorn main:app --reload
```

Open `http://127.0.0.1:8000`.

## Demo accounts

- `steady@demo.com` / `demo123`
- `burnout@demo.com` / `demo123`

The seed script resets the demo accounts and their 14 days of history each time it runs.

## Project structure

```text
burnout_app/
├── main.py
├── database.py
├── risk.py
├── seed.py
├── requirements.txt
├── templates/
│   ├── base.html
│   ├── index.html
│   ├── auth.html
│   └── dashboard.html
└── static/
    └── app.css
```

The ML layer can later be added as a second signal alongside `risk.py`; the dashboard is already structured to display an explainable risk result and adaptive schedule.
