# Deployment

**Status: not deployed.** The build environment had no AWS credentials and no Docker daemon. The
application was run and tested locally against PostgreSQL 16. The Dockerfile and Compose file have
not been built; `docker compose config` accepts the Compose file and the CloudFormation template
parses as YAML, but neither has been executed. Treat the first build as a test.

## Design

One EC2 instance runs Docker Compose with two containers: PostgreSQL 16, and the API, which also
serves the built dashboard. This is the cheapest arrangement that still uses a real database, and
it has one thing to start and one thing to delete. A managed database, load balancer or container
service would each cost more than the whole demo.

## Smallest action needed from you

1. Push this repository to GitHub (public, or adjust the template to use a deploy key).
2. In the AWS console choose a region, open CloudFormation, create a stack from
   `deploy/aws/cloudformation.yml`, and fill in: repository URL, your IP as `x.x.x.x/32`, an API
   key and a database password (16+ characters each), and an email for budget alerts.
3. After about 10 minutes open the `DashboardUrl` output. Check `/api/health` first, enter the API
   key when asked, then choose "Load the public case study".

CLI equivalent:

```
aws cloudformation validate-template --template-body file://deploy/aws/cloudformation.yml
aws cloudformation deploy --stack-name sme-di --template-file deploy/aws/cloudformation.yml \
  --capabilities CAPABILITY_IAM --parameter-overrides RepoUrl=https://github.com/<you>/<repo>.git \
  AllowedCidr=<your-ip>/32 ApiKey=<16+ chars> DbPassword=<16+ letters/digits> BudgetEmail=<you@example.com>
```

## Run it anywhere with Docker

```
cp .env.example .env        # set POSTGRES_PASSWORD and API_KEY
docker compose up -d --build
curl http://localhost/api/health
```

## Environment variables

| Variable | Purpose | Default |
|---|---|---|
| `DATABASE_URL` | SQLAlchemy URL | `sqlite:///./local.db` (Compose sets PostgreSQL) |
| `POSTGRES_PASSWORD` | Database password used by Compose | required |
| `API_KEY` | If set, all `/api` routes except health need `X-API-Key` | empty (open) |
| `DATA_DIR` | Uploaded files, cleaned Parquet, saved outputs | `./var` |
| `MAX_UPLOAD_MB` | Upload size limit | 150 |
| `CORS_ORIGINS` | Allowed origins when the dashboard is hosted elsewhere | `http://localhost:5173` |
| `STATIC_DIR` | Built dashboard served by the API | unset in development |
| `DEMO_CSV`, `DEMO_CONFIG` | Bundled case study and its mapping | repository paths |
| `LOG_LEVEL` | Logging level | INFO |

## Database

Tables are created at start-up (`init_db`), and the schema version is recorded in
`schema_version`. `python -m app.api.db` creates them manually. There is one schema version so
far; Alembic migrations are on the roadmap for the first schema change.

## Health and logs

- `GET /api/health` returns 200 with database status, 503 if the database is unreachable. The
  container health check and Compose use it.
- Logs go to standard output in one line per event; Compose keeps three 10 MB files.
  `docker compose logs -f app` follows them. Unhandled errors are logged with a traceback and the
  client receives a generic message.

## Cost drivers and controls

Prices vary by region and change; confirm with the AWS Pricing Calculator.

- **EC2 instance** is nearly all of the cost. A t3.medium left on all month is on the order of
  USD 30 to 40 in most regions. Stopping the instance when you are not demonstrating stops this charge.
- **EBS volume** (20 GB gp3): a couple of dollars a month, charged even while stopped.
- **Public IPv4 address**: charged per hour while the instance runs.
- **Data transfer**: negligible for a demo.
- The template creates an **AWS Budget** with email alerts at 80% of actual and 100% of
  forecast spend (default USD 40 a month). It alerts; it does not stop anything.
- The security group opens port 80 only to the address range you give. There is no SSH port;
  use Session Manager.

Traffic is plain HTTP. Before sharing the link publicly, put a domain and TLS in front (for
example Caddy as a third Compose service) so the API key is not sent in clear text.

## Shutdown and cleanup

```
aws ec2 stop-instances --instance-ids <InstanceId>          # pause: keeps data, stops compute charges
aws cloudformation delete-stack --stack-name sme-di          # remove everything, including the volume and data
```

Locally: `docker compose down` stops the containers; `docker compose down -v` also deletes the
database and uploaded files.
