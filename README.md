# Project StockWars

Online stock trading simulator built with **Python + Django + Django Templates + Bootstrap 5 + PostgreSQL**.

## Local setup

## TwelveData API Key = 24512fc688324e809779b067b411d418

## rwosborne330, rwosborne3302, rwosborne3303, tprice (weaverofwishes001!)
## stockwars, stockwars - Django Admin

### Dependencies

```bash
cd /Users/ronosborne/Desktop/ProjectStockWars
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Environment variables

Copy `example.env` to `.env` and adjust values.

Required (Postgres):
- `POSTGRES_DB`
- `POSTGRES_USER`
- `POSTGRES_PASSWORD`
- `POSTGRES_HOST`
- `POSTGRES_PORT`

Optional:
- `TWELVE_DATA_API_KEY`
- `MAX_QUOTE_AGE_SECONDS` (default 300)

### Run

```bash
source .venv/bin/activate
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

