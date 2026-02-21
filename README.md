# Project StockWars

Online stock trading simulator built with **Python + Django + Django Templates + Bootstrap 5 + PostgreSQL**.

## Local setup

## TwelveData API Key = 24512fc688324e809779b067b411d418

## rwosborne330, rwosborne3302, rwosborne3303, tprice (weaverofwishes001!)
## stockwars, stockwars - Django Admin

## Operational note (how to run at market open)
## Run these in sequence:
## python3 manage.py activate_queued_participants
## python3 manage.py execute_scheduled_basket_orders
##
##
##
##
##
##

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

## EC2 (git-based) deployment checklist

### One-time provisioning (run on EC2 as `ubuntu`)

```bash
cd /path/to/ProjectStockWars
bash scripts/ec2_provision_server.sh
```

Create `/opt/stockwars/.env` (see `scripts/ec2_env_example.sh`). For local Postgres on the instance, use:

- `POSTGRES_HOST=127.0.0.1`

Then (recommended) clone your repo into `/opt/stockwars/app` as the `stockwars` user:

```bash
sudo -u stockwars bash -lc 'cd /opt/stockwars && git clone <your-repo-url> app'
```

### Repeatable deploy (run on EC2 after `git pull`)

```bash
cd /opt/stockwars/app
sudo bash scripts/deploy_on_server.sh
```

### Verify (quick)

```bash
sudo systemctl status daphne-stockwars --no-pager
sudo nginx -t
curl -I http://127.0.0.1/
sudo ss -lntp | rg ':443|:80'
```

