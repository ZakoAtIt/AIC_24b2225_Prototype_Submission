import pandas as pd
import os
import shutil
import argparse

DATA_DIR = "data"
BACKUP_DIR = "data_backup"

def backup_data():
    """Saves a pristine copy of the CSV data before mutating it."""
    if not os.path.exists(BACKUP_DIR):
        os.makedirs(BACKUP_DIR)
    
    # Only copy CSVs to avoid Windows file locks on the running DuckDB
    for file in os.listdir(DATA_DIR):
        if file.endswith(".csv"):
            shutil.copy2(os.path.join(DATA_DIR, file), os.path.join(BACKUP_DIR, file))
    print("✅ Pristine CSV data backed up safely.")

def restore_data():
    """Restores the pristine CSVs to instantly fix the app."""
    if os.path.exists(BACKUP_DIR):
        for file in os.listdir(BACKUP_DIR):
            if file.endswith(".csv"):
                shutil.copy2(os.path.join(BACKUP_DIR, file), os.path.join(DATA_DIR, file))
        print("✅ System restored to golden baseline.")
    else:
        print("⚠️ No backup found. Run `python generate_data.py --scenario default`")

def test_missing_data():
    """Empties the marketing spend file to simulate a broken data pipeline."""
    backup_data()
    file_path = os.path.join(DATA_DIR, "marketing_spend.csv")
    df = pd.read_csv(file_path)
    df.iloc[0:0].to_csv(file_path, index=False) # Leaves only headers
    print("🔥 CHAOS INJECTED: marketing_spend.csv is now completely empty.")

def test_extreme_outlier():
    """Injects a massive anomaly to trigger the AI Contradiction Guard."""
    backup_data()
    file_path = os.path.join(DATA_DIR, "pos_transactions.csv")
    df = pd.read_csv(file_path)
    
    # Target the last few days of South and crash them to zero
    south_mask = df['region'] == 'South'
    df.loc[south_mask, 'units_sold'] = 1  # Near-zero anomaly
    
    df.to_csv(file_path, index=False)
    print("🔥 CHAOS INJECTED: South sales completely flatlined to trigger firewall.")

def test_schema_drift():
    """Renames a critical column to see how the semantic contract handles it."""
    backup_data()
    file_path = os.path.join(DATA_DIR, "support_tickets.csv")
    df = pd.read_csv(file_path)
    df = df.rename(columns={"sentiment_score": "sentiment_v2_new"})
    df.to_csv(file_path, index=False)
    print("🔥 CHAOS INJECTED: Renamed 'sentiment_score' to 'sentiment_v2_new'.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="CauseTrace Chaos Engineering Tester")
    parser.add_argument("--test", choices=["missing_data", "outlier", "schema_drift", "restore"], required=True)
    args = parser.parse_args()

    if args.test == "missing_data": test_missing_data()
    elif args.test == "outlier": test_extreme_outlier()
    elif args.test == "schema_drift": test_schema_drift()
    elif args.test == "restore": restore_data()