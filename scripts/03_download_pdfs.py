import pandas as pd
import requests
import webbrowser
from pathlib import Path
import json

BATCH_SIZE = 12
PROXY_BASE = "https://login.ezp-prod1.hul.harvard.edu/login?url="
CROSSREF_BASE = "https://api.crossref.org/works/"
CSV_PATH = Path("analysis/classified_studies.csv")

def load_studies():
    """Load and filter studies from CSV."""
    if not CSV_PATH.exists():
        raise FileNotFoundError("Could not find classified_studies.csv")
    
    df = pd.read_csv(CSV_PATH)
    
    # Add processed column if it doesn't exist
    if 'processed' not in df.columns:
        print("Adding 'processed' column to CSV...")
        df['processed'] = False
        df.to_csv(CSV_PATH, index=False)
    
    # Filter for unprocessed studies with visitor data
    mask = (df['has_visitor_data'] == True) & (~df['processed'].fillna(False))
    return df, df[mask]

def get_article_url(doi):
    """Get article URL from Crossref API."""
    try:
        response = requests.get(f"{CROSSREF_BASE}{doi}")
        response.raise_for_status()
        data = response.json()
        
        if 'message' in data and 'URL' in data['message']:
            return data['message']['URL']
        return None
        
    except (requests.RequestException, json.JSONDecodeError) as e:
        print(f"Error fetching URL for DOI {doi}: {str(e)}")
        return None

def mark_batch_processed(full_df, batch_dois):
    """Mark batch of DOIs as processed in CSV."""
    full_df.loc[full_df['doi'].isin(batch_dois), 'processed'] = True
    full_df.to_csv(CSV_PATH, index=False)
    print("Marked current batch as processed and updated CSV")

def process_studies():
    """Process unprocessed studies in batches."""
    full_df, unprocessed_studies = load_studies()
    
    if len(unprocessed_studies) == 0:
        print("No unprocessed studies found!")
        return

    total_batches = (len(unprocessed_studies) + BATCH_SIZE - 1) // BATCH_SIZE
    
    for i in range(0, len(unprocessed_studies), BATCH_SIZE):
        batch = unprocessed_studies.iloc[i:i + BATCH_SIZE]
        current_batch = i // BATCH_SIZE + 1
        
        print(f"\nProcessing batch {current_batch}/{total_batches}")
        batch_dois = []
        
        # Open all URLs in current batch
        for _, study in batch.iterrows():
            article_url = get_article_url(study['doi'])
            if article_url:
                proxy_url = f"{PROXY_BASE}{article_url}"
                print(f"Opening: {study['doi']}")
                webbrowser.get('firefox').open(proxy_url, new=2)
                batch_dois.append(study['doi'])
            else:
                print(f"Could not get URL for DOI: {study['doi']}")
        
        # Always wait for user confirmation before marking as processed
        input("\nPress Enter after downloading PDFs from this batch...")
        
        # Mark batch as processed only after confirmation
        mark_batch_processed(full_df, batch_dois)
        
        if current_batch < total_batches:
            input("\nPress Enter when ready for next batch...")

def main():
    try:
        process_studies()
        print("\nAll studies processed!")
    except Exception as e:
        print(f"An error occurred: {str(e)}")

if __name__ == "__main__":
    main()
