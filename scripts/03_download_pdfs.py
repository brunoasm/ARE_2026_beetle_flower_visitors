import pandas as pd
import requests
import webbrowser
from pathlib import Path
import json
import time
import os

BATCH_SIZE = 70
HARVARD_PROXY = os.environ['HARVARD_PROXY_URL']
UCHICAGO_PROXY = os.environ['UCHICAGO_PROXY_URL']
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

def get_article_info(doi):
    """Get article URL and publisher info from Crossref API."""
    try:
        time.sleep(0.1)  # Rate limit for Crossref API
        response = requests.get(f"{CROSSREF_BASE}{doi}")
        response.raise_for_status()
        data = response.json()
        message = data.get('message', {})
        
        publisher = message.get('publisher', '')
        is_oup = 'OUP' in publisher or 'Oxford University Press' in publisher
        is_jstor = 'JSTOR' in publisher
        is_elsevier = 'Elsevier' in publisher
        
        # For JSTOR or Elsevier, look for the resource URL
        if is_jstor or is_elsevier:
            resource = message.get('resource', {})
            primary = resource.get('primary', {})
            url = primary.get('URL', '')
            
            if is_jstor and url and 'jstor.org/stable/' in url:
                # Convert JSTOR URL to direct PDF link
                base_url = url.split('?')[0]  # Remove query parameters
                pdf_url = base_url.replace('/stable/', '/stable/pdf/')
                print(f"DEBUG - Converting JSTOR URL: {url} -> {pdf_url}")
                return pdf_url, is_oup, is_jstor, is_elsevier
                
            elif is_elsevier and url and 'linkinghub.elsevier.com' in url:
                # Convert Elsevier URL to direct PDF link
                pii = url.split('pii/')[-1]
                pdf_url = f"https://www.sciencedirect.com/science/article/pii/{pii}/pdf"
                print(f"DEBUG - Converting Elsevier URL: {url} -> {pdf_url}")
                return pdf_url, is_oup, is_jstor, is_elsevier
        
        # For others, use regular URL
        url = message.get('URL')
        print(f"DEBUG - Publisher: {publisher}")
        return url, is_oup, is_jstor, is_elsevier
        
    except (requests.RequestException, json.JSONDecodeError) as e:
        print(f"Error fetching info for DOI {doi}: {str(e)}")
        return None, False, False, False

def get_proxy_url(article_url):
    """Determine which proxy to use based on the article URL."""
    print(f"DEBUG - Checking URL for proxy selection: {article_url}")  # Debug print
    is_oup = 'oup' in article_url.lower() or 'academic.oup.com' in article_url.lower()
    selected_proxy = UCHICAGO_PROXY if is_oup else HARVARD_PROXY
    print(f"DEBUG - Selected proxy: {'UChicago' if is_oup else 'Harvard'}")  # Debug print
    return f"{selected_proxy}{article_url}"

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
        
        # Initialize lists to store different types of articles
        regular_articles = []  # For OUP and others
        pdf_articles = []      # For JSTOR and Elsevier
        batch_dois = []
        
        # First pass: categorize articles
        for _, study in batch.iterrows():
            article_url, is_oup, is_jstor, is_elsevier = get_article_info(study['doi'])
            if article_url:
                batch_dois.append(study['doi'])
                if is_jstor or is_elsevier:
                    # Store PDF articles for later
                    pdf_articles.append({
                        'doi': study['doi'],
                        'url': article_url,
                        'publisher': 'JSTOR' if is_jstor else 'Elsevier'
                    })
                else:
                    # Store regular articles for immediate opening
                    regular_articles.append({
                        'doi': study['doi'],
                        'url': article_url,
                        'is_oup': is_oup
                    })
            else:
                print(f"Could not get URL for DOI: {study['doi']}")
        
        # First open regular articles
        if regular_articles:
            print("\nOpening regular articles...")
            for article in regular_articles:
                if article['is_oup']:
                    final_url = article['url']
                    print(f"Opening: {article['doi']} (OUP - direct access)")
                else:
                    final_url = f"{HARVARD_PROXY}{article['url']}"
                    print(f"Opening: {article['doi']} (with Harvard proxy)")
                webbrowser.get('firefox').open(final_url, new=2, autoraise=False)
                time.sleep(0.7)  # Delay between opening Firefox windows
        
        # Then open PDF articles
        if pdf_articles:
            print("\nOpening PDF articles...")
            for article in pdf_articles:
                final_url = f"{HARVARD_PROXY}{article['url']}"
                print(f"Opening: {article['doi']} ({article['publisher']} PDF with Harvard proxy)")
                webbrowser.get('firefox').open(final_url, new=2, autoraise=False)
                time.sleep(0.5)  # Delay between opening Firefox windows
        
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

