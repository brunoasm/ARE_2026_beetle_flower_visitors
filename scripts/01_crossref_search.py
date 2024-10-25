import pandas as pd
import requests
import time
import backoff
import requests_cache
import argparse
import json
from pathlib import Path
from datetime import datetime

def parse_args():
    """Set up command-line argument parsing."""
    parser = argparse.ArgumentParser(
        description='Collect pollinator research papers for plant families from Crossref API',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument(
        '-i', '--input',
        default='plant_families.txt',
        help='Input file containing plant family names (one per line)'
    )
    parser.add_argument(
        '-o', '--output',
        default='crossref_results.csv',
        help='Output CSV file name'
    )
    parser.add_argument(
        '--test',
        action='store_true',
        help='Run in test mode (only process first two requests)'
    )
    parser.add_argument(
        '--email',
        required=True,
        help='Email address for Crossref API identification'
    )
    parser.add_argument(
        '--restart',
        action='store_true',
        help='Restart from beginning, ignoring previous progress'
    )
    return parser.parse_args()

# Define relevant work types
WORK_TYPES = [
    'journal-article',
    'proceedings-article',
    'book-chapter',
    'dissertation',
    'report',
    'monograph',
    'dataset'
]

class ProgressTracker:
    """Track progress of family processing"""
    def __init__(self, progress_file='progress.json'):
        self.progress_file = progress_file
        self.load_progress()
    
    def load_progress(self):
        """Load progress from file if it exists"""
        if Path(self.progress_file).exists():
            with open(self.progress_file, 'r') as f:
                self.progress = json.load(f)
        else:
            self.progress = {
                'completed_families': [],
                'last_updated': None,
                'total_records': 0
            }
    
    def save_progress(self):
        """Save current progress to file"""
        self.progress['last_updated'] = datetime.now().isoformat()
        with open(self.progress_file, 'w') as f:
            json.dump(self.progress, f, indent=2)
    
    def mark_family_complete(self, family, records_added):
        """Mark a family as completed and update stats"""
        if family not in self.progress['completed_families']:
            self.progress['completed_families'].append(family)
            self.progress['total_records'] += records_added
            self.save_progress()
    
    def is_family_completed(self, family):
        """Check if a family has been completed"""
        return family in self.progress['completed_families']
    
    def reset(self):
        """Reset progress"""
        self.progress = {
            'completed_families': [],
            'last_updated': None,
            'total_records': 0
        }
        self.save_progress()
    
    def print_status(self):
        """Print current progress status"""
        print("\nProgress Status:")
        print(f"Total families completed: {len(self.progress['completed_families'])}")
        print(f"Total records collected: {self.progress['total_records']}")
        if self.progress['last_updated']:
            print(f"Last updated: {self.progress['last_updated']}")
        print()

# Install cache for requests to avoid duplicate DOI lookups
requests_cache.install_cache('crossref_cache', expire_after=86400)  # Cache for 24 hours

@backoff.on_exception(
    backoff.expo,
    (requests.exceptions.RequestException, requests.exceptions.HTTPError),
    max_tries=8
)
def get_works(session, query, cursor='*', rows=100):
    """
    Make request to Crossref API with exponential backoff on failure.
    Using query.bibliographic as recommended by Crossref for better results.
    """
    url = 'https://api.crossref.org/works'
    params = {
        'query.bibliographic': query,
        'cursor': cursor,
        'rows': rows,
        'select': 'DOI,title,author,published-print,container-title,volume,issue,page,type'
    }
    response = session.get(url, params=params)
    response.raise_for_status()
    return response.json()

def extract_author_string(work):
    """Extract author names from work metadata."""
    if 'author' not in work:
        return ''
    authors = work['author']
    author_names = []
    for author in authors:
        if 'family' in author:
            name = f"{author.get('family', '')}"
            if 'given' in author:
                name = f"{author['given']} {name}"
            author_names.append(name)
    return ' and '.join(author_names)

def extract_year(work):
    """Extract publication year from work metadata."""
    if 'published-print' in work and 'date-parts' in work['published-print']:
        return work['published-print']['date-parts'][0][0]
    return None

def process_work(work, plant_family):
    """Process a single work and return a dictionary of bibliographic data."""
    return {
        'doi': work.get('DOI', ''),
        'title': work['title'][0] if work.get('title') else '',
        'author': extract_author_string(work),
        'year': extract_year(work),
        'journal': work['container-title'][0] if work.get('container-title') else '',
        'volume': work.get('volume', ''),
        'issue': work.get('issue', ''),
        'pages': work.get('page', ''),
        'plant_family': plant_family,
        'type': work.get('type', '')
    }

def process_family(session, family, df, existing_dois, output_file, test_mode=False):
    """Process all works for a single family using cursor pagination."""
    print(f"Processing {family}...")
    
    cursor = '*'
    request_count = 0
    records_added = 0
    types_found = set()  # Track what types we find
    
    while cursor:
        try:
            # Check if we've hit test mode limit
            if test_mode and request_count >= 2:
                print("Test mode: stopping after 2 requests")
                break
                
            # Construct search query for family and pollinators
            query = f'"{family}" pollinator'
            
            # Make API request
            response_data = get_works(session, query, cursor=cursor)
            message = response_data['message']
            request_count += 1
            
            # Process results
            for work in message['items']:
                # Track types we find
                work_type = work.get('type', 'unknown')
                types_found.add(work_type)
                
                # Only process if it's a type we want
                if work_type in WORK_TYPES:
                    doi = work.get('DOI', '')
                    if doi and doi not in existing_dois:
                        work_data = process_work(work, family)
                        df = pd.concat([df, pd.DataFrame([work_data])], ignore_index=True)
                        existing_dois.add(doi)
                        records_added += 1
            
            # Save after each API request (checkpoint)
            df.to_csv(output_file, index=False)
            
            # Update cursor for next page
            cursor = message.get('next-cursor', None)
            
            # Report types found if we're in test mode or this is first request
            if (test_mode or request_count == 1) and types_found:
                print(f"Work types found in results: {', '.join(sorted(types_found))}")
            
            # If we have a next page, add a polite delay
            if cursor:
                time.sleep(1)
                
        except Exception as e:
            print(f"Error processing {family} at cursor {cursor}: {str(e)}")
            time.sleep(5)  # Longer delay on error before retrying
            continue
    
    print(f"Processed {request_count} pages, found {len(types_found)} different work types")
    return df, records_added

def main():
    # Parse command line arguments
    args = parse_args()
    
    # Initialize progress tracker
    tracker = ProgressTracker()
    if args.restart:
        print("Restarting from beginning...")
        tracker.reset()
    else:
        tracker.print_status()
    
    # Configure session with appropriate headers
    session = requests.Session()
    session.headers.update({
        'User-Agent': f'PlantPollinatorResearch/1.0 (mailto:{args.email})',
    })
    
    # Initialize dataframe to store results
    COLUMNS = ['doi', 'title', 'author', 'year', 'journal', 'volume', 'issue', 'pages', 'plant_family', 'type']
    df = pd.DataFrame(columns=COLUMNS)
    
    # Load existing data if available
    if Path(args.output).exists():
        df = pd.read_csv(args.output)
        existing_dois = set(df['doi'])
    else:
        existing_dois = set()
    
    # Read plant families
    with open(args.input, 'r') as f:
        plant_families = [line.strip() for line in f if line.strip()]
    
    print(f"Found {len(plant_families)} families to process")
    
    # Process each family
    for family in plant_families:
        if not args.restart and tracker.is_family_completed(family):
            print(f"Skipping {family} (already completed)")
            continue
            
        df, records_added = process_family(
            session=session,
            family=family,
            df=df,
            existing_dois=existing_dois,
            output_file=args.output,
            test_mode=args.test
        )
        
        # Mark family as completed and update progress
        tracker.mark_family_complete(family, records_added)
        print(f"Added {records_added} new records for {family}")
        
        time.sleep(2)  # Polite delay between families
    
    # Print final status
    tracker.print_status()

if __name__ == "__main__":
    main()
