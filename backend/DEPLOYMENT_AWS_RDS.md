# AWS RDS PostgreSQL Deployment Checklist

This guide walks through deploying CaughtOnDash with AWS RDS PostgreSQL and AWS Secrets Manager.

## 1. Provision AWS RDS Instance

**AWS Console Steps:**

1. Go to **RDS → Databases → Create Database**
2. Choose **Standard Create**
3. **Engine Options:**
   - Engine: `PostgreSQL`
   - Version: `16.3` (or latest)
4. **Templates:**
   - Choose `Free tier` (optional, for testing) or `Production`
5. **Settings:**
   - DB instance identifier: `caughtondash-prod`
   - Master username: `postgres`
   - Master password: **Generate strong random password** → copy and save to Secrets Manager (step 4 below)
6. **Instance Configuration:**
   - Burstable classes: `db.t4g.micro` (free tier) or `db.t3.small` (recommended)
7. **Storage:**
   - Type: `gp3`
   - Allocated: `20 GB`
   - Enable autoscaling: ✅ Yes (max 100 GB)
8. **Connectivity:**
   - VPC: Your application VPC
   - DB Subnet Group: Create or select existing
   - Public access: ❌ **OFF** (keep it private!)
   - VPC Security Group: Create new → Name: `rds-caughtondash`
9. **Database Authentication:**
   - IAM DB authentication: Optional (skip for now)
10. **Additional Configuration:**
    - Initial DB name: `caughtondash`
    - Backup retention: `7` days
    - Enable encryption: ✅ Yes (default KMS)
    - CloudWatch monitoring: ✅ Yes
    - Enable IAM DB authentication: Optional
    - Enhanced monitoring: ✅ Yes
11. **Click "Create Database"** → Wait ~5-10 minutes for provisioning

**After creation, note your endpoint:**
```
caughtondash-prod.XXXXXXXXX.us-east-1.rds.amazonaws.com
```

---

## 2. Configure Security Group

Once RDS is created:

1. Go to **RDS → Databases → caughtondash-prod**
2. Under **Connectivity & security**, click the **VPC security group** link
3. Edit **Inbound rules**:
   - Add rule: Type `PostgreSQL`, Protocol `TCP`, Port `5432`
   - Source: Choose your app server security group (or VPC CIDR for testing)
4. Save rules

**For local testing from your machine:**
   - Add inbound rule: Source `Your IP/32` (find via `curl ifconfig.me`)
   - ⚠️ Remove this before production!

---

## 3. Create Database and App User

From your local machine (requires `psql` CLI):

```bash
# Connect to master database
psql -h caughtondash-prod.XXXXXXXXX.us-east-1.rds.amazonaws.com \
     -U postgres \
     -d postgres
```

Run these SQL commands:

```sql
-- Create application database
CREATE DATABASE caughtondash;

-- Create application user (NOT a superuser)
CREATE USER caught_user WITH ENCRYPTED PASSWORD 'GenerateStrongRandomPasswordHere';

-- Grant connection privilege
GRANT CONNECT ON DATABASE caughtondash TO caught_user;

-- Switch to the app database and grant privileges
\c caughtondash

-- Grant schema privileges
GRANT ALL PRIVILEGES ON SCHEMA public TO caught_user;

-- Grant default privileges for future tables/sequences
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO caught_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO caught_user;

-- Verify
\du  -- should show caught_user
\l   -- should show caughtondash owned by postgres, with caught_user having CONNECT
```

Then disconnect:
```sql
\q
```

---

## 4. Store Credentials in AWS Secrets Manager

**AWS Console Steps:**

1. Go to **Secrets Manager → Store a new secret**
2. **Secret type**: Select `Other type of secret`
3. **Key/value pairs** (plain text):
   ```
   db_host: caughtondash-prod.XXXXXXXXX.us-east-1.rds.amazonaws.com
   db_port: 5432
   db_name: caughtondash
   db_user: caught_user
   db_password: GenerateStrongRandomPasswordHere
   ```
4. **Secret name**: `caughtondash/db/production`
5. **Encryption key**: Use default AWS managed key
6. **Click Store**
7. **Copy the ARN** (looks like: `arn:aws:secretsmanager:us-east-1:123456789:secret:caughtondash/db/production-AbCdEf`)

---

## 5. Build DATABASE_URL

Construct the connection string from the credentials:

```
postgresql://caught_user:GenerateStrongRandomPasswordHere@caughtondash-prod.XXXXXXXXX.us-east-1.rds.amazonaws.com:5432/caughtondash?sslmode=require
```

**Format:**
```
postgresql://USER:PASSWORD@HOST:PORT/DBNAME?sslmode=require
```

**Note:** `sslmode=require` ensures encrypted connections to RDS (enforced).

---

## 6. Set Up Environment Variables

### Local Development (`.env`)

Keep using SQLite:
```
DEBUG=True
DATABASE_URL=sqlite:///db.sqlite3
CLERK_SECRET_KEY=...
```

### Production (Application Host)

Set via environment variable (e.g., in your deployment system like ECS, Lambda, App Runner, EC2):

**Option A: Direct environment variable**
```bash
export DATABASE_URL="postgresql://caught_user:PASSWORD@host:5432/caughtondash?sslmode=require"
```

**Option B: Fetch from Secrets Manager (recommended)**

Create a deployment script:
```bash
#!/bin/bash
SECRET_JSON=$(aws secretsmanager get-secret-value \
  --secret-id caughtondash/db/production \
  --query SecretString \
  --output text \
  --region us-east-1)

DB_HOST=$(echo "$SECRET_JSON" | jq -r '.db_host')
DB_PORT=$(echo "$SECRET_JSON" | jq -r '.db_port')
DB_NAME=$(echo "$SECRET_JSON" | jq -r '.db_name')
DB_USER=$(echo "$SECRET_JSON" | jq -r '.db_user')
DB_PASSWORD=$(echo "$SECRET_JSON" | jq -r '.db_password')

export DATABASE_URL="postgresql://${DB_USER}:${DB_PASSWORD}@${DB_HOST}:${DB_PORT}/${DB_NAME}?sslmode=require"

# Start the application
python manage.py migrate
gunicorn caughtondash.wsgi:application
```

---

## 7. Run Migrations and Create Superuser

**First time deployment (maintenance window):**

```bash
# Connect to production host with DATABASE_URL set
export DATABASE_URL="postgresql://caught_user:PASSWORD@host:5432/caughtondash?sslmode=require"

# Run all migrations
python manage.py migrate

# Create superuser
python manage.py createsuperuser --noinput \
  --username admin \
  --email admin@example.com

# Set admin password (via Django shell or env)
python manage.py shell
> from django.contrib.auth.models import User
> u = User.objects.get(username='admin')
> u.set_password('SecureAdminPasswordHere')
> u.save()
> exit()
```

Or use environment variables:
```bash
export DJANGO_SUPERUSER_PASSWORD="SecureAdminPasswordHere"
python manage.py createsuperuser --noinput \
  --username admin \
  --email admin@example.com
```

---

## 8. Enable Backups & Monitoring

**AWS Console → RDS → Databases → caughtondash-prod:**

1. **Automated Backups:**
   - Backup retention: Set to `7` or `30` days
   - Backup window: Set to off-peak hours (e.g., 02:00 UTC)
   - ✅ Already enabled during instance creation

2. **CloudWatch Monitoring:**
   - Go to **CloudWatch → Dashboards → Create Dashboard**
   - Add widgets:
     - **CPU Utilization**
     - **Database Connections**
     - **Read/Write IOPS**
     - **Read Latency**
     - **Free Storage**

3. **Alarms:**
   - **High CPU**: Alert if CPU > 80% for 5 minutes
   - **Low Storage**: Alert if free storage < 5 GB
   - **Connection Count**: Alert if connections > 80% of max

**CloudWatch Alarm Example (CLI):**
```bash
aws cloudwatch put-metric-alarm \
  --alarm-name rds-caughtondash-high-cpu \
  --alarm-description "Alert when RDS CPU > 80%" \
  --metric-name CPUUtilization \
  --namespace AWS/RDS \
  --statistic Average \
  --period 300 \
  --threshold 80 \
  --comparison-operator GreaterThanThreshold \
  --dimensions Name=DBInstanceIdentifier,Value=caughtondash-prod \
  --evaluation-periods 1 \
  --alarm-actions arn:aws:sns:us-east-1:123456789:your-alert-topic
```

---

## 9. Test the Connection

**From your local machine:**

```bash
# Test with psql
psql -h caughtondash-prod.XXXXXXXXX.us-east-1.rds.amazonaws.com \
     -U caught_user \
     -d caughtondash \
     -c "SELECT 1 as connection_test;"
```

**From your Django app (development):**

```bash
cd backend
export DATABASE_URL="postgresql://caught_user:PASSWORD@host:5432/caughtondash?sslmode=require"
python manage.py dbshell
> \dt  # Should show no tables initially (migrations haven't run yet)
> \q
```

---

## 10. Set Up Connection Pooling (Optional, for High Traffic)

If you expect high concurrent connections, add **PgBouncer** as a connection pooler:

**Option A: AWS RDS Proxy (managed)**
1. Go to **RDS → Proxies → Create Proxy**
2. Name: `caughtondash-proxy`
3. Target: `caughtondash-prod`
4. Auth: Use default (or Secrets Manager integration)
5. Session pooling mode: `TRANSACTION`
6. Max connections: `100`
7. Click Create

Then update `DATABASE_URL` to point at the proxy endpoint (displayed after creation).

**Option B: Self-hosted PgBouncer (advanced)**
- Deploy PgBouncer in transaction-pooling mode on a separate server/container
- Configure `pgbouncer.ini` and point `DATABASE_URL` at the PgBouncer endpoint

---

## 11. Production Checklist

Before deploying to production:

- [ ] RDS instance created and running
- [ ] Database `caughtondash` and user `caught_user` created
- [ ] Credentials stored in AWS Secrets Manager
- [ ] Security group configured (inbound 5432 from app servers only)
- [ ] Backups enabled (7+ day retention)
- [ ] CloudWatch monitoring and alarms set up
- [ ] `DATABASE_URL` environment variable set on app servers
- [ ] `requirements.txt` includes `dj-database-url` (✅ already added)
- [ ] `settings.py` uses `dj_database_url.parse()` (✅ already updated)
- [ ] Run `python manage.py migrate` in production (one-time)
- [ ] Create superuser via Django shell or `createsuperuser` command
- [ ] Test connection from app host to RDS
- [ ] Set up log retention (CloudWatch Logs) for app errors
- [ ] Enable enhanced monitoring (optional, for deeper RDS metrics)
- [ ] Test restore from backup at least once

---

## 12. Disaster Recovery & Testing

**Monthly Backup Restore Test:**

```bash
# Create a test RDS instance from snapshot
aws rds restore-db-instance-from-db-snapshot \
  --db-instance-identifier caughtondash-restore-test \
  --db-snapshot-identifier caughtondash-prod-snapshot-id
```

**Expected RTO/RPO:**
- **RTO** (Time to Restore): ~5-10 minutes (automated failover to standby)
- **RPO** (Recovery Point Objective): <1 minute (continuous replication)

---

## Troubleshooting

**Connection Refused?**
- Check security group inbound rules (port 5432 open?)
- Verify app server IP is in allowed range
- Test with `psql` from your machine first

**SSL Certificate Error?**
- AWS RDS uses self-signed certificates by default
- `sslmode=require` is safe; `sslmode=verify-full` requires CA bundle
- Download CA bundle if using `verify-full`:
  ```bash
  wget https://truststore.pem.s3.amazonaws.com/global-bundle.pem
  # Add to settings.py OPTIONS: 'sslrootcert': '/path/to/global-bundle.pem'
  ```

**Password Reset?**
```bash
# In Secrets Manager UI, click "Rotate Secret" → follow prompts
# Then update application DATABASE_URL accordingly
```

---

## Next Steps

1. **Complete section 1–4 above** (provision RDS, create user, store in Secrets Manager)
2. **Run migrations**: `python manage.py migrate`
3. **Create superuser**: `python manage.py createsuperuser`
4. **Deploy app** with `DATABASE_URL` environment variable set
5. **Monitor** CloudWatch metrics and test connections
6. **Enable backups** and PITR retention
7. **Document** your recovery runbook and test it monthly

---

For questions or issues, refer to:
- [AWS RDS User Guide](https://docs.aws.amazon.com/rds/)
- [Django Database Config](https://docs.djangoproject.com/en/5.2/ref/settings/#databases)
- [dj-database-url Docs](https://github.com/jazzband/dj-database-url)
