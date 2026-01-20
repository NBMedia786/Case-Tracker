
import os
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    print("❌ Error: SUPABASE_URL or SUPABASE_KEY not found in .env")
    exit(1)

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# SQL to add columns
migration_sql = """
ALTER TABLE cases 
ADD COLUMN IF NOT EXISTS processing_status TEXT DEFAULT 'idle',
ADD COLUMN IF NOT EXISTS progress_percent INT DEFAULT 0,
ADD COLUMN IF NOT EXISTS progress_message TEXT DEFAULT '';
"""

print("🚀 Running DB Migration to add progress columns...")

try:
    # Try using RPC if 'exec_sql' function exists (common pattern)
    supabase.rpc("exec_sql", {"query": migration_sql}).execute()
    print("✅ Migration successful via RPC!")
except Exception as e:
    print(f"⚠️ RPC method failed ({e}). trying manual check or alternative...")
    # NOTE: The python client for Supabase typically interacts via PostgREST which doesn't allow DDL (ALTER TABLE) directly unless via an RPC function.
    # If this fails, the user might need to run it in their Supabase dashboard.
    # However, we can TRY to just proceed and hope the columns exist or that we can use them.
    # But wait, if we can't alter table, Agent writing to them will fail.
    
    print("\nIMPORTANT: If the above failed, please run this SQL in your Supabase SQL Editor:")
    print("-" * 40)
    print(migration_sql)
    print("-" * 40)
