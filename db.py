import os
from supabase import create_client, Client
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Supabase configuration
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

# Global Supabase client
supabase: Client = None


def get_supabase_client() -> Client:
    """
    Get or create the Supabase client instance.
    
    Returns:
        Client: The Supabase client instance.
    
    Raises:
        ValueError: If SUPABASE_URL or SUPABASE_KEY environment variables are not set.
    """
    global supabase
    
    if supabase is None:
        if not SUPABASE_URL or not SUPABASE_KEY:
            raise ValueError(
                "SUPABASE_URL and SUPABASE_KEY environment variables must be set. "
                "Please check your .env file."
            )
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    
    return supabase


def init_db() -> bool:
    """
    Initialize the database by checking for and creating the 'cases' table if it doesn't exist.
    
    The 'cases' table schema:
    - id: int (primary key, auto-increment)
    - case_name: text (required)
    - victim_name: text (nullable)
    - suspect_name: text (nullable)
    - next_hearing_date: date (nullable)
    - last_checked_date: timestamp
    - status: text (e.g., 'Open', 'Closed', 'Verdict Reached')
    - notes: text (for AI summaries)
    
    Returns:
        bool: True if initialization was successful, False otherwise.
    
    Note:
        For self-hosted Supabase, you may need to run the SQL migration directly
        in your Supabase SQL editor if table creation via API is not supported.
    """
    try:
        client = get_supabase_client()
        
        # Check if the 'cases' table exists by attempting to query it
        try:
            response = client.table("cases").select("id").limit(1).execute()
            print("✓ 'cases' table already exists.")
            return True
        except Exception as table_error:
            # Table doesn't exist, attempt to create it
            print("'cases' table not found. Attempting to create...")
            
            # SQL to create the cases table
            # Note: Supabase Python client doesn't directly support raw SQL execution.
            # For self-hosted Supabase, you should run this migration manually in the SQL editor
            # or use the Supabase Management API.
            
            create_table_sql = """
            CREATE TABLE IF NOT EXISTS cases (
                id SERIAL PRIMARY KEY,
                case_name TEXT NOT NULL,
                docket_url TEXT,
                victim_name TEXT,
                suspect_name TEXT,
                next_hearing_date DATE,
                last_hearing_date DATE,
                last_checked_date TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                status TEXT DEFAULT 'Open' CHECK (status IN ('Open', 'Closed', 'Verdict Reached')),
                notes TEXT,
                confidence TEXT DEFAULT 'high',
                created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
            );
            
            -- Create an index on status for faster filtering
            CREATE INDEX IF NOT EXISTS idx_cases_status ON cases(status);
            
            -- Create an index on next_hearing_date for scheduling queries
            CREATE INDEX IF NOT EXISTS idx_cases_next_hearing ON cases(next_hearing_date);
            """
            
            # Try using RPC if available (requires a function to be set up in Supabase)
            try:
                client.rpc("exec_sql", {"query": create_table_sql}).execute()
                print("✓ 'cases' table created successfully via RPC.")
                return True
            except Exception as rpc_error:
                # RPC not available, provide manual instructions
                print("\n" + "=" * 60)
                print("MANUAL TABLE CREATION REQUIRED")
                print("=" * 60)
                print("\nThe 'cases' table could not be created automatically.")
                print("Please run the following SQL in your Supabase SQL Editor:\n")
                print(create_table_sql)
                print("=" * 60 + "\n")
                
                # Return False to indicate manual intervention needed
                return False
                
    except ValueError as ve:
        print(f"Configuration Error: {ve}")
        return False
    except Exception as e:
        print(f"Database initialization error: {e}")
        return False


def get_all_cases():
    """
    Retrieve all cases from the database.
    
    Returns:
        list: List of case records.
    """
    client = get_supabase_client()
    response = client.table("cases").select("*").execute()
    return response.data


def get_case_by_id(case_id: int):
    """
    Retrieve a specific case by its ID.
    
    Args:
        case_id: The ID of the case to retrieve.
    
    Returns:
        dict: The case record, or None if not found.
    """
    client = get_supabase_client()
    response = client.table("cases").select("*").eq("id", case_id).execute()
    return response.data[0] if response.data else None


def create_case(case_data: dict):
    """
    Create a new case in the database.
    
    Args:
        case_data: Dictionary containing case fields.
    
    Returns:
        dict: The created case record.
    """
    client = get_supabase_client()
    response = client.table("cases").insert(case_data).execute()
    return response.data[0] if response.data else None


def update_case(case_id: int, case_data: dict):
    """
    Update an existing case.
    
    Args:
        case_id: The ID of the case to update.
        case_data: Dictionary containing fields to update.
    
    Returns:
        dict: The updated case record.
    """
    client = get_supabase_client()
    response = client.table("cases").update(case_data).eq("id", case_id).execute()
    return response.data[0] if response.data else None


def delete_case(case_id: int):
    """
    Delete a case from the database.
    
    Args:
        case_id: The ID of the case to delete.
    
    Returns:
        bool: True if deletion was successful.
    """
    client = get_supabase_client()
    response = client.table("cases").delete().eq("id", case_id).execute()
    return True


def get_cases_by_status(status: str):
    """
    Retrieve cases filtered by status.
    
    Args:
        status: The status to filter by ('Open', 'Closed', 'Verdict Reached').
    
    Returns:
        list: List of case records matching the status.
    """
    client = get_supabase_client()
    response = client.table("cases").select("*").eq("status", status).execute()
    return response.data


def get_upcoming_hearings(days: int = 7):
    """
    Retrieve cases with hearings in the next N days.
    
    Args:
        days: Number of days to look ahead (default: 7).
    
    Returns:
        list: List of cases with upcoming hearings.
    """
    from datetime import datetime, timedelta
    
    client = get_supabase_client()
    today = datetime.now().date().isoformat()
    future_date = (datetime.now() + timedelta(days=days)).date().isoformat()
    
    response = client.table("cases").select("*")\
        .gte("next_hearing_date", today)\
        .lte("next_hearing_date", future_date)\
        .order("next_hearing_date")\
        .execute()
    
    return response.data
