#!/usr/bin/env python
"""
Test RDS connection using psycopg.
Usage:
  python test_rds_connection.py
"""

import sys
import os
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent))

import psycopg

# Update these with your RDS values
RDS_HOST = "caughtondash.cluster-cbiq0uqgcu6m.us-east-2.rds.amazonaws.com"
RDS_PORT = "5432"
RDS_DB = "caughtondash"
RDS_USER = "caught_user"
RDS_PASSWORD = input(f"Enter password for {RDS_USER}: ").strip()

print(f"\n🔗 Connecting to {RDS_HOST}:{RDS_PORT}/{RDS_DB} as {RDS_USER}...")

try:
    # Test connection
    conn = psycopg.connect(
        host=RDS_HOST,
        port=int(RDS_PORT),
        database=RDS_DB,
        user=RDS_USER,
        password=RDS_PASSWORD,
        sslmode="require",
    )
    
    print("✅ Connection successful!\n")
    
    # Test query
    with conn.cursor() as cur:
        cur.execute("SELECT version();")
        version = cur.fetchone()
        print(f"PostgreSQL version: {version[0]}\n")
        
        cur.execute("SELECT datname FROM pg_database WHERE datname = %s;", (RDS_DB,))
        db_exists = cur.fetchone()
        if db_exists:
            print(f"✅ Database '{RDS_DB}' exists")
        else:
            print(f"❌ Database '{RDS_DB}' not found")
    
    conn.close()
    print("\n✅ All tests passed!")
    
except Exception as e:
    print(f"\n❌ Connection failed: {e}")
    sys.exit(1)
